"""Optional local oracle tests. MSI binaries/firmware/captures are not test fixtures."""

import unittest
from pathlib import Path

from msi_flash.capture import captured_sectors, read_capture
from msi_flash.firmware import Firmware
from msi_flash.nt68550 import NT68550, program
from msi_flash.protocol import checksum, sector_reports

ROOT = Path(__file__).resolve().parent.parent
CAPTURE = ROOT / "captures/gi-flash-fw021.pcap"
IMAGE = ROOT / "firmware/MSI_MPG321URX_V021_7F69.bin"


def normalize_unused_spi_arguments(packet):
    if packet[:2] != b"\x04\xae":
        return packet
    packet = bytearray(packet)
    count = packet[14] & 15
    # MSI sends uninitialized stack bytes here. They are outside the actual
    # SPI argument length; our implementation deliberately sends zeroes.
    packet[7 + count : 14] = bytes(7 - count)
    packet[15] = checksum(packet[4:15])
    return bytes(packet)


class Oracle:
    def __init__(self, packets):
        self.packets = packets
        self.index = 0
        self.count = 0

    def write(self, data):
        packet = self.packets[self.index]
        self.index += 1
        if packet.direction != "OUT":
            raise AssertionError(
                f"unexpected write while expecting a read, frame {self.index}"
            )
        if normalize_unused_spi_arguments(data) != normalize_unused_spi_arguments(
            packet.data
        ):
            raise AssertionError(
                f"frame {self.index}, expected {packet.data.hex()}, got {data.hex()}"
            )
        self.count += 1

    def read(self):
        packet = self.packets[self.index]
        self.index += 1
        if packet.direction != "IN":
            raise AssertionError(
                f"unexpected read while expecting a write, frame {self.index}"
            )
        return packet.data

    def exchange(self, data, delay=0):
        self.write(data)
        return self.read()


@unittest.skipUnless(
    CAPTURE.exists() and IMAGE.exists(), "local MSI capture and firmware unavailable"
)
class CaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packets = read_capture(CAPTURE)
        cls.firmware = Firmware.load(IMAGE)

    def test_all_sector_bytes_match(self):
        actual = captured_sectors(self.packets)
        expected = list(self.firmware.sectors(0, 0x6000))
        self.assertEqual(len(actual), len(expected))
        for (address, packets), (expected_address, data) in zip(
            actual, expected, strict=True
        ):
            self.assertEqual(address, expected_address)
            self.assertEqual(packets, sector_reports(address, data))

    def test_complete_isp_lifecycle_against_capture(self):
        binary = [p for p in self.packets if p.data[0] == 4]
        oracle = Oracle(binary)
        device = NT68550(oracle, sleep=lambda _: None)
        # USB interface selection and OSD freeze precede the binary capture scope.
        oracle.original_write = oracle.write

        def scoped_write(data):
            if data[0] == 4:
                oracle.original_write(data)

        oracle.write = scoped_write
        device.select_interface = lambda _: None
        device.enter()
        program(device, self.firmware)
        device.exit()
        self.assertEqual(oracle.index, len(binary))
        self.assertTrue(device.committed)


if __name__ == "__main__":
    unittest.main()

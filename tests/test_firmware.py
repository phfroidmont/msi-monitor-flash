import unittest

from msi_flash.firmware import MAGIC, MODEL, PANEL, Firmware
from msi_flash.nt68550 import NT68550
from msi_flash.protocol import ProtocolError


def image():
    data = bytearray(b"\xff" * 0x9000)
    metadata = (
        MAGIC
        + b"UART"
        + bytes([MODEL])
        + MAGIC
        + b"PANEL"
        + bytes([len(PANEL)])
        + PANEL.encode()
        + MAGIC
        + b"SCALER\x07NT68550"
        + MAGIC
        + b"FWVER\0\x15"
    )
    data[0x8200 : 0x8200 + len(metadata)] = metadata
    data[0x6000:0x7000] = bytes(4096)
    for index in range(9):
        address = 0x10000 + index * 4096
        data[0x6000 + index * 8 : 0x6004 + index * 8] = address.to_bytes(4, "big")
    return bytes(data)


class FirmwareTests(unittest.TestCase):
    def test_metadata_and_unknown_hash(self):
        firmware = Firmware.parse(image())
        self.assertEqual(firmware.version, 21)
        self.assertRaises(ValueError, firmware.require_known)

    def test_bad_metadata(self):
        self.assertRaises(ValueError, Firmware.parse, image()[:-1])
        self.assertRaises(
            ValueError, Firmware.parse, image().replace(bytes([MODEL]), b"\x01")
        )
        self.assertRaises(
            ValueError, Firmware.parse, image().replace(b"NT68550", b"NT68500")
        )

    def test_two_bank_table_relocation(self):
        firmware = Firmware.parse(image())
        for bank, table in [(0, 0x6000), (0x800000, 0x7000)]:
            sectors = list(firmware.sectors(bank, table))
            self.assertEqual([s[0] for s in sectors], [bank + 0x8000, table])
            for index in range(9):
                address = int.from_bytes(
                    sectors[-1][1][index * 8 : index * 8 + 4], "big"
                )
                self.assertEqual(address, bank + 0x10000 + index * 4096)
            self.assertEqual(sectors[-1][1][-1], 0xAA)

    def test_refuse_ambiguous_boot_markers(self):
        self.assertEqual(NT68550.destination((0xAA, 0)), (0x800000, 0x7000, 0x6000))
        self.assertEqual(NT68550.destination((0, 0xAA)), (0, 0x6000, 0x7000))
        for markers in [(0, 0), (0xAA, 0xAA), (0xFF, 0xFF), (0xFF, 0xAA)]:
            self.assertRaises(ProtocolError, NT68550.destination, markers)


if __name__ == "__main__":
    unittest.main()

"""The verified NT68550 / GD25Q128 path, reconstructed from protocol facts.

This is deliberately NOT a generic MSI flasher. Unsupported chip IDs, ambiguous
boot markers and unknown image hashes are rejected before any flash write.
"""

import json
import logging
import time
from pathlib import Path

from .firmware import Firmware
from .protocol import (
    ProtocolError,
    acknowledge,
    ascii_get,
    ascii_value,
    command,
    flash_checksum,
    inner_command,
    inner_value,
    invalidate_reports,
    read_register,
    report,
    sector_reports,
    write_register,
)

LOG = logging.getLogger(__name__)
FLASH_ID = bytes.fromhex("c84018")  # GD25Q128: 16 MiB

# Register/value pairs for NT68550 ISP clock, bus and SPI-controller setup.
ISP_REGISTERS = (
    (0xF1DF, 0x80),
    (0xF1A8, 2),
    (0xF0E0, 0xAA),
    (0xF0E2, 0xAA),
    (0xF068, 0x9F),
    (0xF067, 0x20),
    (0xF0E5, 0x40),
    (0xF0E5, 0),
    (0xF0E6, 4),
    (0xF04E, 0),
    (0xD909, 0x55),
    (0xD90A, 0x55),
    (0xD906, 4),
    (0xD905, 3),
    (0xD904, 0xFE),
    (0xD90B, 0x84),
    (0xD90C, 0x30),
    (0xF04C, 0x60),
    (0xD929, 6),
)


class NT68550:
    def __init__(self, transport, sleep=time.sleep):
        self.transport = transport
        self.sleep = sleep
        self.in_isp = False
        self.writes_started = False
        self.committed = False

    def rpc(
        self,
        packet: bytes,
        response_opcode: int | None = None,
        size: int = 0,
        delay: float = 0,
    ):
        reply = self.transport.exchange(packet, delay)
        acknowledge(reply, packet[1])
        if response_opcode is not None:
            return inner_value(reply, packet[1], response_opcode, size)
        return reply

    def get_register(self, address: int) -> int:
        return self.rpc(read_register(address), 0x78, 1, 0.005)[0]

    def set_register(self, address: int, value: int) -> None:
        self.rpc(write_register(address, value), 0x87, 0, 0.005)

    def select_interface(self, i2c: bool):
        packet = report(1, bytes([0xE0, int(i2c)]) + b"\xff" * 61)
        reply = self.transport.exchange(packet)
        if reply[:3] != bytes([1, 0x5A, int(i2c)]):
            raise ProtocolError("USB controller did not select the requested interface")

    def select_address_bank(self, bank: int = 0):
        # 24-bit addressing is sufficient for the verified 16 MiB part.
        if bank != 0:
            raise ValueError("unverified extended-address bank")
        value = self.rpc(inner_command(0xA2, bytes([0x0F, bank])), 0xF0, 1)
        if value != bytes([bank]):
            raise ProtocolError("extended address bank mismatch")

    def enter(self):
        # Reading feature 0040: suspends scaler operation for ISP. No response is
        # expected. This is intentionally NOT exposed through the read-only CLI.
        self.transport.write(ascii_get(b"0040:"))
        self.in_isp = True
        self.sleep(0.2)
        self.select_interface(True)
        self.sleep(2)
        chip = self.rpc(command(0xA0, bytes.fromhex("04340636"), 0x6F), 0xA5, 1)
        if chip != b"\0":
            raise ProtocolError(f"unverified scaler ID A5{chip.hex()}")
        self.set_register(0xF0E0, 0xAA)
        self.set_register(0xF0E2, 0xAA)
        state = self.get_register(0xF049) & 15
        if state > 7:
            raise ProtocolError("unverified scaler operating mode")
        self.set_register(0xF055, state)
        for value in (0x9F020008, 0x9F120008):
            self.rpc(command(0xBC, b"\xb0\xff\x3d"))
            self.rpc(
                command(0xBE, b"\xb0\x80" + value.to_bytes(4, "little") + b"\0\x10\0")
            )
            self.rpc(command(0xBC, b"\xb0\x8c\x80"))
        for address, value in ISP_REGISTERS:
            self.set_register(address, value)
        self.sleep(0.5)
        ident = self.spi_read(b"\x01\x03", b"\x9f", 16, initial_probe=True)[3:6]
        if ident != FLASH_ID:
            raise ProtocolError(
                f"unverified SPI flash {ident.hex()}, expected {FLASH_ID.hex()}"
            )
        self.set_register(0xF067, 0x20)
        self.rpc(command(0xAD, bytes.fromhex("6e029900f5")))
        # MSI's compatibility probe has empty SPI descriptors for this flash ID;
        # it reads the staging buffer, then records both status registers.
        self.spi_read(b"", b"", 16)
        self.initial_sr1 = self.status(5)
        self.initial_sr2 = self.status(0x35)
        LOG.info("ISP entered: NT68550, GD25Q128 (%s)", ident.hex())

    def spi_read(
        self, descriptor: bytes, body: bytes, size: int, initial_probe=False
    ) -> bytes:
        # An SRAM command descriptor points into the staging area at 0xA700.
        if len(descriptor) % 2 or len(descriptor) + len(body) + size > 256:
            raise ValueError("invalid SPI descriptor")
        if initial_probe:
            self.set_register(0xF0C0, 0x13)
        else:
            self.get_register(0xF0C0)
        registers = [
            (0xF0C1, 0xA7),
            (0xF0C2, 0),
            (0xF0C3, 0xA7),
            (0xF0C4, len(descriptor)),
            (0xF0C5, 0xA7),
            (0xF0C6, len(descriptor) + len(body)),
        ]
        # MSI's first JEDEC probe sets F0C0 explicitly instead of reading it.
        for address, value in registers:
            self.set_register(address, value)
        for index, value in enumerate(descriptor + body):
            self.set_register(0xA700 + index, value)
        self.rpc(inner_command(0xAF, b"\x25"), 0xDA, 0, 0.1)
        self.sleep(0.2)
        offset = 0 if initial_probe else len(descriptor) + len(body)
        return bytes(self.get_register(0xA700 + offset + i) for i in range(size))

    def spi_short(self, body: bytes, read: bool = False) -> None:
        if not 1 <= len(body) <= 7:
            raise ValueError("SPI short operation is limited to seven bytes")
        payload = (
            b"\x53" + body.ljust(7, b"\0") + bytes([(0x10 if read else 0) | len(body)])
        )
        self.rpc(inner_command(0xAE, payload), 0xAC, 0, 0.02)

    def status(self, opcode: int) -> int:
        self.spi_short(bytes([opcode]), read=True)
        self.sleep(0.1)
        return self.get_register(0xF880)

    def markers(self) -> tuple[int, int]:
        return tuple(
            self.spi_read(b"\x05\x01", b"\x0b" + address.to_bytes(3, "big") + b"\0", 1)[
                0
            ]
            for address in (0x6FFF, 0x7FFF)
        )

    @staticmethod
    def destination(markers: tuple[int, int]) -> tuple[int, int, int]:
        # Refuse ambiguous/recovery states rather than guessing which bank is good.
        if markers == (0xAA, 0):
            return 0x800000, 0x7000, 0x6000
        if markers == (0, 0xAA):
            return 0, 0x6000, 0x7000
        raise ProtocolError(
            f"ambiguous boot markers {markers}; no flash writes allowed"
        )

    def unprotect(self):
        for address, mask, set_bit in (
            (0xF134, 0x40, True),
            (0xF11D, 0x40, False),
            (0xF11C, 0x40, True),
        ):
            value = self.get_register(address)
            self.set_register(address, value | mask if set_bit else value & ~mask)
        sr1 = self.initial_sr1
        # For GD25Q128, clear block protection and set QE in status register 2.
        self.spi_short(b"\x06")
        self.spi_short(bytes([1, sr1 & 0x63]))
        self.spi_short(b"\x06")
        self.spi_short(b"\x31\x02")
        self.sleep(0.3)
        if self.status(5) & 0x9C or self.status(0x35) != 2:
            raise ProtocolError(
                "SPI protection/status did not match the verified state"
            )

    def sum_block(self, address: int) -> int:
        value = self.rpc(flash_checksum(address), 0xC9, 2, 0.01)
        return int.from_bytes(value, "big")

    def invalidate(self, table: int):
        self.select_address_bank()
        packets = invalidate_reports(table + 0xE00)
        self.writes_started = True
        for packet in packets[:-2]:
            self.transport.write(packet)
            self.sleep(0.002)
        value = self.rpc(packets[-2], 0xC3, 0, 0.01)
        if value != b"":
            raise ProtocolError("unexpected invalidate reply")
        if self.sum_block(table + 0xE00) != 0:
            raise ProtocolError("boot marker invalidation did not verify")

    def write_sector(self, address: int, data: bytes):
        packets = sector_reports(address, data)
        for packet in packets[:5]:
            self.rpc(packet)
            self.sleep(0.005)
        for packet in packets[5:73]:
            self.transport.write(packet)
            self.sleep(0.002)
        self.rpc(packets[73], delay=0.01)
        self.rpc(packets[74])
        self.sleep(0.2)
        self.rpc(packets[75])
        for _ in range(200):
            result = self.rpc(packets[76], delay=0.001)[5]
            if result == 0x55:
                break
            if result == 5:
                raise ProtocolError(f"scaler refused sector {address:#x}")
            self.sleep(0.02)
        else:
            raise ProtocolError(f"scaler busy timeout at sector {address:#x}")
        for offset in range(0, 4096, 512):
            expected = sum(data[offset : offset + 512]) & 0xFFFF
            actual = self.sum_block(address + offset)
            if actual != expected:
                raise ProtocolError(
                    f"checksum mismatch at {address + offset:#x}: {actual:#x} != {expected:#x}"
                )

    def exit(self):
        if not self.in_isp:
            return
        self.rpc(command(0xA1, bytes.fromhex("0434063c")))
        self.sleep(30)
        self.in_isp = False


def calibration(transport) -> dict[str, str]:
    # The colour response has a shortened header: 5b00[ + 12 data + sum + CR.
    reply = transport.exchange(ascii_get(b"0[000"))
    if (
        reply[:6] != b"\x015b00["
        or reply[19] != 13
        or sum(reply[6:18]) & 255 != reply[18]
    ):
        raise ProtocolError("invalid colour calibration backup")
    colour = reply[6:18]
    serial = ascii_value(transport.exchange(ascii_get(b"00131")), b"00131", 14)
    if sum(serial[:13]) & 255 != serial[13]:
        raise ProtocolError("invalid serial-number backup")
    return {"colour": colour.hex(), "serial": serial[:13].hex()}


def restore_calibration(transport, backup):
    colour, serial = bytes.fromhex(backup["colour"]), bytes.fromhex(backup["serial"])
    if len(colour) != 12 or len(serial) != 13:
        raise ValueError("invalid saved calibration length")
    messages = [
        b"5b00Z\xa5\x55\xaa00\r",
        b"5b001i" + colour + bytes([sum(colour) & 255]) + b"000000\r",
        b"5b001j" + serial + bytes([sum(serial) & 255]) + b"00000\r",
        b"5b00Z\xa8\xaa\x5500\r",
    ]
    for message in messages:
        reply = transport.exchange(report(1, message))
        if reply[:7] != b"\x015600+\r":
            raise ProtocolError("monitor refused calibration restore")
    if calibration(transport) != backup:
        raise ProtocolError("restored calibration does not match backup")


def save_backup(path: Path, identity: dict, values: dict):
    # Never overwrite an earlier recovery backup.
    with path.open("x") as stream:
        json.dump({"identity": identity, "calibration": values}, stream, indent=2)
        stream.write("\n")
        stream.flush()
        import os

        os.fsync(stream.fileno())


def program(device: NT68550, firmware: Firmware):
    firmware.require_known()
    device.unprotect()
    bank, table, previous = device.destination(device.markers())
    LOG.info(
        "destination bank=%#x table=%#x; preserving table=%#x", bank, table, previous
    )
    # The old boot marker remains valid until all new data and the new table verify.
    device.invalidate(table)
    device.select_address_bank()
    sectors = list(firmware.sectors(bank, table))
    for index, (address, data) in enumerate(sectors, 1):
        if address == table:
            device.select_address_bank()
        device.write_sector(address, data)
        if index % 32 == 0 or index == len(sectors):
            LOG.info(
                "verified %d/%d sectors (%d%%), address=%#x",
                index,
                len(sectors),
                100 * index // len(sectors),
                address,
            )
    device.committed = True
    device.invalidate(previous)
    LOG.info("new bank verified and committed")

"""Validate MSI's raw image metadata, without copying or bundling firmware."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

MAGIC = bytes.fromhex("55aa6699")
MODEL = 0x9F
PANEL = "SDC QMC315AA01_D01"
SCALER = "NT68550"
KNOWN_IMAGES = {
    "4934abd71dff21492dda3b2dd630f3539ba1d6639de88f2f3684fd03d59f019e": 21,
    "532d59d03773dba3a6125cd62be974ce753974dcfa891de327eb4690ce831845": 20,
}


def field(data: bytes, tag: bytes) -> bytes:
    key = MAGIC + tag
    if data.count(key) != 1:
        raise ValueError(f"expected exactly one {tag!r} metadata field")
    return data[data.index(key) + len(key) :]


def text_field(data: bytes, tag: bytes) -> str:
    value = field(data, tag)
    if not value or not 1 <= value[0] <= 63 or len(value) < value[0] + 1:
        raise ValueError(f"invalid length for {tag!r}")
    return value[1 : 1 + value[0]].decode("ascii")


@dataclass(frozen=True)
class Firmware:
    data: bytes
    version: int
    digest: str

    @classmethod
    def load(cls, path: Path) -> "Firmware":
        return cls.parse(path.read_bytes())

    @classmethod
    def parse(cls, data: bytes) -> "Firmware":
        if not 0x8000 < len(data) <= 0x800000 or len(data) % 512:
            raise ValueError("image size is outside the verified layout")
        if field(data, b"UART")[0] != MODEL:
            raise ValueError("image is not for the MPG 321URX")
        if text_field(data, b"PANEL") != PANEL or text_field(data, b"SCALER") != SCALER:
            raise ValueError("panel or scaler mismatch")
        version = int.from_bytes(field(data, b"FWVER")[:2], "big")
        if not 1 <= version <= 255:
            raise ValueError("invalid firmware version")
        return cls(data, version, sha256(data).hexdigest())

    def require_known(self) -> None:
        if KNOWN_IMAGES.get(self.digest) != self.version:
            raise ValueError("image hash is not a verified MSI FW.020/FW.021 download")

    def sectors(self, bank: int, table: int):
        if bank not in (0, 0x800000) or table != (0x6000 if bank == 0 else 0x7000):
            raise ValueError("unverified bank/table layout")
        for offset in range(0x8000, len(self.data), 4096):
            yield bank + offset, self.data[offset : offset + 4096].ljust(4096, b"\xff")
        # Boot code (0x0000-0x5fff) and tables are NOT copied as image sectors.
        boot_table = bytearray(self.data[0x6000:0x7000])
        for offset in range(0, 9 * 8, 8):
            address = int.from_bytes(boot_table[offset : offset + 4], "big") + bank
            boot_table[offset : offset + 4] = address.to_bytes(4, "big")
        boot_table[-1] = 0xAA
        yield table, bytes(boot_table)

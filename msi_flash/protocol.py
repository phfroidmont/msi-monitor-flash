"""Pure framing helpers; these functions never access hardware."""

from functools import reduce
from operator import xor

REPORT_SIZE = 64


class ProtocolError(RuntimeError):
    pass


def checksum(data: bytes) -> int:
    return reduce(xor, data, 0)


def report(report_id: int, payload: bytes) -> bytes:
    if len(payload) > REPORT_SIZE - 1:
        raise ValueError("payload does not fit in a HID report")
    return bytes([report_id]) + payload.ljust(REPORT_SIZE - 1, b"\0")


def ascii_get(code: bytes) -> bytes:
    if len(code) != 5:
        raise ValueError("GET requires a five-byte feature code")
    return report(1, b"58" + code + b"\r")


def ascii_value(reply: bytes, code: bytes, size: int | None = None) -> bytes:
    prefix = b"\x015b" + code
    if len(reply) != REPORT_SIZE or not reply.startswith(prefix):
        raise ProtocolError(f"unexpected response to GET {code!r}")
    if size is None:
        end = reply.find(b"\r", len(prefix))
        if end < 0:
            raise ProtocolError("missing ASCII response terminator")
    else:
        # Calibration contains arbitrary bytes (including CR and NUL).
        end = len(prefix) + size
        if end >= REPORT_SIZE or reply[end] != 13:
            raise ProtocolError("invalid fixed-length response")
    return reply[len(prefix) : end]


def command(opcode: int, payload: bytes = b"", flag: int = 0) -> bytes:
    return report(4, bytes([opcode, flag, 0]) + payload)


def inner_command(opcode: int, body: bytes) -> bytes:
    frame = bytes([0x6E, len(body)]) + body
    return command(opcode, frame + bytes([checksum(frame)]), flag=0x6F)


def acknowledge(reply: bytes, opcode: int) -> None:
    if len(reply) != REPORT_SIZE or reply[:4] != bytes([4, opcode, 1, 0]):
        raise ProtocolError(f"failed command {opcode:#04x}: {reply.hex()}")


def inner_value(reply: bytes, opcode: int, response_opcode: int, size: int) -> bytes:
    acknowledge(reply, opcode)
    frame = reply[4 : 7 + size]
    if frame[:3] != bytes([0x6F, size + 1, response_opcode]):
        raise ProtocolError(f"invalid inner response to {opcode:#04x}")
    if checksum(frame) != reply[7 + size]:
        raise ProtocolError(f"bad response checksum for {opcode:#04x}")
    return frame[3:]


def write_register(address: int, value: int) -> bytes:
    return inner_command(0xA9, b"\x78" + address.to_bytes(2, "big") + bytes([value]))


def read_register(address: int) -> bytes:
    return inner_command(0xAA, b"\x87" + address.to_bytes(2, "big"))


def flash_checksum(address: int) -> bytes:
    if not 0 <= address < 0x1000000 or address % 512:
        raise ValueError("checksum address must be a 24-bit, 512-byte-aligned address")
    return inner_command(0xB5, b"\x36" + address.to_bytes(3, "big"))


def sector_reports(address: int, data: bytes) -> list[bytes]:
    if not 0 <= address < 0x1000000 or address % 4096 or len(data) != 4096:
        raise ValueError(
            "sector must be exactly 4096 bytes at an aligned 24-bit address"
        )
    # The scaler's RAM staging area receives 58 + 67*60 + 18 = 4096 bytes.
    return [
        command(0xBC, b"\xb0\xff\xf2"),
        command(0xBD, b"\xb0\0" + address.to_bytes(3, "big") + b"\x10\0\0"),
        command(0xBC, b"\xb0\xff\xf3"),
        command(0xBC, b"\xb0\xe2\x87"),
        command(0xBC, b"\xb0\xff\xd0"),
        command(0xBF, b"\xb0\0" + data[:58]),
        *(command(0xC0, data[i : i + 60]) for i in range(58, 4078, 60)),
        command(0xC1, data[4078:]),
        # 0xC2 is sent without the I2C flag even though its body is checksummed.
        command(0xC2, bytes.fromhex("6e07b5f00f6e9113ec23")),
        command(0xBC, b"\xb0\xff\xf2"),
        command(0xC3, b"\xb0\x05", flag=0xB1),
        *(flash_checksum(address + i) for i in range(0, 4096, 512)),
    ]


def invalidate_reports(address: int) -> list[bytes]:
    """Program 512 zero bytes over an inactive table's final block (not an erase)."""
    if address not in (0x6E00, 0x7E00):
        raise ValueError("not a known boot-table marker block")
    header = b"\x6e\0\x3c" + address.to_bytes(3, "big") + bytes.fromhex("f00f6e9113ec")
    return [
        command(0xB6, header + bytes(48)),
        *(command(0xB7, bytes(60)) for _ in range(7)),
        command(0xB8, bytes(44) + bytes([checksum(header)]), flag=0x6F),
        flash_checksum(address),
    ]

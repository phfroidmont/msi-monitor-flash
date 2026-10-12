"""Read QEMU usb-host pcap files offline. No device access and no packet replay."""

import struct
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Packet:
    time: float
    direction: str
    data: bytes


def read_capture(path: Path) -> list[Packet]:
    packets = []
    with path.open("rb") as stream:
        header = stream.read(24)
        if len(header) != 24:
            raise ValueError("truncated pcap header")
        magic, _, _, _, _, _, link = struct.unpack("<IHHiIII", header)
        if magic != 0xA1B2C3D4 or link != 220:
            raise ValueError("expected little-endian LINKTYPE_USB_LINUX_MMAPPED pcap")
        start = None
        while record := stream.read(16):
            if len(record) != 16:
                raise ValueError("truncated pcap record")
            sec, usec, size, _ = struct.unpack("<IIII", record)
            if not 64 <= size <= 1024 * 1024:
                raise ValueError("unexpected pcap record length")
            raw = stream.read(size)
            if len(raw) != size:
                raise ValueError("truncated pcap packet")
            timestamp = sec + usec / 1e6
            if start is None:
                start = timestamp
            kind, transfer, endpoint = raw[8:11]
            status = struct.unpack_from("<i", raw, 28)[0]
            # QEMU incorrectly includes the 64-byte usbmon header in len_cap.
            # Use the pcap record length for the actual trailing transfer data.
            data = raw[64:]
            if transfer != 1 or not data:
                continue
            direction = "IN" if endpoint & 0x80 else "OUT"
            if (direction == "IN" and kind != ord("C")) or (
                direction == "OUT" and kind != ord("S")
            ):
                continue
            if status:
                raise ValueError(f"USB error {status} in captured HID transfer")
            if len(data) != 64:
                raise ValueError(f"unexpected HID report size {len(data)}")
            packets.append(Packet(timestamp - start, direction, data))
    return packets


def captured_sectors(packets: list[Packet]) -> list[tuple[int, list[bytes]]]:
    """Extract whole write-sector sequences, excluding interleaved OSD polling."""
    outgoing = [p.data for p in packets if p.direction == "OUT" and p.data[0] == 4]
    sectors = []
    for index, report in enumerate(outgoing):
        if report[1] != 0xBD:
            continue
        address = int.from_bytes(report[6:9], "big")
        sequence = outgoing[index - 1 : index + 84]
        if len(sequence) != 85 or sequence[-1][1] != 0xB5:
            raise ValueError(f"incomplete captured sector at {address:#x}")
        sectors.append((address, sequence))
    return sectors

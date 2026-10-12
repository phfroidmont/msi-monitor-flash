import unittest

from msi_flash.protocol import (
    ProtocolError,
    ascii_get,
    ascii_value,
    command,
    flash_checksum,
    inner_value,
    invalidate_reports,
    read_register,
    sector_reports,
    write_register,
)


def padded(hex_string):
    return bytes.fromhex(hex_string).ljust(64, b"\0")


class ProtocolTests(unittest.TestCase):
    def test_get(self):
        self.assertEqual(ascii_get(b"001<0"), padded("0135383030313c300d"))
        self.assertEqual(
            ascii_value(padded("0135623030313c303032310d"), b"001<0"), b"021"
        )

    def test_binary_calibration_can_contain_terminator(self):
        value = b"\r\0" * 7
        reply = (b"\x015b00131" + value + b"\r").ljust(64, b"\0")
        self.assertEqual(ascii_value(reply, b"00131", 14), value)

    def test_register_frames(self):
        self.assertEqual(write_register(0xF0E0, 0xAA), padded("04a96f006e0478f0e0aaa8"))
        self.assertEqual(read_register(0xF049), padded("04aa6f006e0387f04953"))
        response = padded("04aa01006f02784356")
        self.assertEqual(inner_value(response, 0xAA, 0x78, 1), b"\x43")
        corrupt = bytearray(response)
        corrupt[8] ^= 1
        self.assertRaises(ProtocolError, inner_value, bytes(corrupt), 0xAA, 0x78, 1)

    def test_sector_packet_count_and_boundaries(self):
        data = bytes(range(256)) * 16
        packets = sector_reports(0x8000, data)
        self.assertEqual(len(packets), 85)
        self.assertEqual(packets[1], padded("04bd0000b000008000100000"))
        rebuilt = (
            packets[5][6:] + b"".join(p[4:] for p in packets[6:73]) + packets[73][4:22]
        )
        self.assertEqual(rebuilt, data)
        self.assertEqual(packets[74], padded("04c200006e07b5f00f6e9113ec23"))
        self.assertEqual(packets[77], flash_checksum(0x8000))

    def test_invalidation(self):
        packets = invalidate_reports(0x6E00)
        self.assertEqual(len(packets), 10)
        self.assertEqual(
            packets[0][:16], bytes.fromhex("04b600006e003c006e00f00f6e9113ec")
        )
        self.assertEqual(packets[8][48], 0xC3)

    def test_reject_bad_sizes(self):
        self.assertRaises(ValueError, sector_reports, 1, bytes(4096))
        self.assertRaises(ValueError, sector_reports, 0, bytes(4095))
        self.assertRaises(ValueError, command, 0xBF, bytes(64))
        self.assertRaises(ValueError, invalidate_reports, 0)
        self.assertRaises(ValueError, flash_checksum, 1)


if __name__ == "__main__":
    unittest.main()

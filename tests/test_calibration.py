import unittest
from unittest.mock import Mock

from msi_flash.nt68550 import calibration, restore_calibration, save_backup
from msi_flash.protocol import ProtocolError


def replies(colour, serial):
    return [
        (b"\x015b00[" + colour + bytes([sum(colour) & 255]) + b"\r").ljust(64, b"\0"),
        (b"\x015b00131" + serial + bytes([sum(serial) & 255]) + b"\r").ljust(64, b"\0"),
    ]


class CalibrationTests(unittest.TestCase):
    def test_binary_backup_and_restore(self):
        colour = bytes([13, 0, 255, 10]) * 3
        serial = b"TEST000000001"
        transport = Mock()
        transport.exchange.side_effect = replies(colour, serial)
        backup = calibration(transport)
        self.assertEqual(backup, {"colour": colour.hex(), "serial": serial.hex()})
        transport.reset_mock()
        ack = b"\x015600+\r".ljust(64, b"\0")
        transport.exchange.side_effect = [ack] * 4 + replies(colour, serial)
        restore_calibration(transport, backup)
        messages = [call.args[0] for call in transport.exchange.call_args_list]
        self.assertEqual(messages[1][1:7], b"5b001i")
        self.assertEqual(messages[1][7:19], colour)
        self.assertEqual(messages[2][7:20], serial)
        self.assertEqual(len(messages), 6)

    def test_bad_checksum_never_restores(self):
        transport = Mock()
        reply = bytearray(replies(bytes(12), b"TEST000000001")[0])
        reply[18] ^= 1
        transport.exchange.return_value = bytes(reply)
        self.assertRaises(ProtocolError, calibration, transport)
        self.assertEqual(transport.exchange.call_count, 1)

    def test_backup_never_overwrites(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backup.json"
            save_backup(path, {}, {})
            original = path.read_bytes()
            self.assertRaises(FileExistsError, save_backup, path, {}, {})
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()

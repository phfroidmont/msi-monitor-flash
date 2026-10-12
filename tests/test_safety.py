import unittest
from unittest.mock import Mock

from msi_flash.nt68550 import NT68550
from msi_flash.protocol import ProtocolError, ascii_get


class SafetyTests(unittest.TestCase):
    def test_invalid_ack_does_not_advance(self):
        transport = Mock()
        transport.exchange.return_value = bytes(64)
        device = NT68550(transport, sleep=lambda _: None)
        self.assertRaises(ProtocolError, device.write_sector, 0x8000, bytes(4096))
        self.assertEqual(transport.exchange.call_count, 1)
        transport.write.assert_not_called()

    def test_checksum_error_stops_before_next_block(self):
        device = NT68550(Mock(), sleep=lambda _: None)
        device.rpc = Mock(return_value=b"\x04\xc3\x01\0\xb1\x55" + bytes(58))
        device.sum_block = Mock(return_value=1)
        self.assertRaises(ProtocolError, device.write_sector, 0x8000, bytes(4096))
        device.sum_block.assert_called_once_with(0x8000)

    def test_empty_get_does_not_fit(self):
        self.assertRaises(ValueError, ascii_get, b"")


if __name__ == "__main__":
    unittest.main()

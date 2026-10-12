import argparse
import json
import logging
import sys
import time
from pathlib import Path

from .capture import captured_sectors, read_capture
from .firmware import MODEL, PANEL, Firmware
from .nt68550 import NT68550, calibration, program, restore_calibration, save_backup
from .protocol import ProtocolError, ascii_get, ascii_value, sector_reports
from .transport import USBTransport

LOG = logging.getLogger(__name__)


def info(transport) -> dict:
    values = {}
    for name, code in (
        ("model_raw", b"00140"),
        ("protocol", b"00150"),
        ("firmware", b"001<0"),
        ("serial", b"00130"),
        ("panel_raw", b"001?0"),
    ):
        reply = transport.exchange(ascii_get(code))
        values[name] = ascii_value(reply, code)
    if values["model_raw"] != b"00" + bytes([MODEL]):
        raise ProtocolError("this is not the verified MPG 321URX HID model")
    if values["protocol"] != b"V23":
        raise ProtocolError("monitor protocol is outside the verified V23 profile")
    panel = values["panel_raw"]
    if panel[:1] != bytes([len(PANEL)]) or panel[1 : 1 + len(PANEL)] != PANEL.encode():
        raise ProtocolError("monitor panel does not match the verified profile")
    return {
        "model": "MPG 321URX QD-OLED",
        "protocol": values["protocol"].decode(),
        "firmware": values["firmware"].decode(),
        "serial": values["serial"].decode(),
        "panel": PANEL,
    }


def require_ac_power():
    sources = Path("/sys/class/power_supply")
    if not any(p.read_text().strip() == "1" for p in sources.glob("*/online")):
        raise RuntimeError("external power is required")


def reconnect(identity: dict, backup: dict, expected_version: int | None = None):
    # Re-enumeration gives the controller a new USB address. Do not reuse the old handle.
    last_error = None
    for _ in range(20):
        try:
            with USBTransport() as transport:
                NT68550(transport).select_interface(False)
                ready = ascii_value(transport.exchange(ascii_get(b"00100")), b"00100")
                if ready != b"001":
                    raise ProtocolError("scaler has not finished booting")
                current = info(transport)
                if current["serial"] != identity["serial"]:
                    raise ProtocolError(
                        "different monitor reconnected; refusing calibration restore"
                    )
                if (
                    expected_version is not None
                    and int(current["firmware"]) != expected_version
                ):
                    raise ProtocolError(
                        "firmware version did not change to the requested version"
                    )
                if calibration(transport) != backup:
                    restore_calibration(transport, backup)
                print(json.dumps(current, indent=2))
                return
        except (RuntimeError, OSError) as error:
            last_error = error
            time.sleep(2)
    raise RuntimeError(f"cannot verify restarted monitor: {last_error}")


def run_isp(args):
    firmware = None
    if args.action == "flash":
        firmware = Firmware.load(args.firmware)
        firmware.require_known()
        if not args.yes:
            raise ValueError("flash requires --yes; read the safety instructions first")
    require_ac_power()
    with USBTransport() as transport:
        identity = info(transport)
        if firmware and int(identity["firmware"]) > firmware.version:
            raise ValueError("firmware downgrades are not supported")
        values = calibration(transport)
        save_backup(args.backup, identity, values)
        LOG.info("identity verified; calibration saved to %s", args.backup)
        device = NT68550(transport)
        try:
            device.enter()
            if firmware:
                program(device, firmware)
            else:
                markers = device.markers()
                bank, table, previous = device.destination(markers)
                print(
                    f"boot markers={markers}; destination bank={bank:#x}, table={table:#x}, active table={previous:#x}"
                )
            device.exit()
        except BaseException:
            if device.in_isp and not device.writes_started:
                LOG.error("aborted before any firmware write; restarting the scaler")
                device.exit()
            elif device.writes_started:
                LOG.critical(
                    "flash failed after writes started: keep power connected; use the MSI VM recovery path"
                )
            raise
    reconnect(identity, values, firmware.version if firmware else None)


def main():
    parser = argparse.ArgumentParser(
        description="Experimental MSI monitor firmware interoperability tool"
    )
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("info", help="read model, panel and firmware identity only")
    image = commands.add_parser("inspect", help="validate a firmware file offline")
    image.add_argument("firmware", type=Path)
    compare = commands.add_parser(
        "compare-capture",
        help="compare generated sector packets with MSI's capture offline",
    )
    compare.add_argument("firmware", type=Path)
    compare.add_argument("capture", type=Path)
    probe = commands.add_parser(
        "probe-isp",
        help="inspect boot markers in ISP; temporarily blanks and restarts the display, no firmware writes",
    )
    probe.add_argument("--backup", type=Path, required=True)
    flash = commands.add_parser(
        "flash", help="EXPERIMENTAL: write a verified FW.020/FW.021 image"
    )
    flash.add_argument("firmware", type=Path)
    flash.add_argument("--backup", type=Path, required=True)
    flash.add_argument(
        "--yes",
        action="store_true",
        help="acknowledge unsupported flashing and physical cable/power requirements",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    try:
        if args.action == "info":
            with USBTransport() as transport:
                print(json.dumps(info(transport), indent=2))
            return
        if args.action in ("probe-isp", "flash"):
            run_isp(args)
            return
        firmware = Firmware.load(args.firmware)
        print(
            f"FW.{firmware.version:03d}, {len(firmware.data)} bytes, SHA-256 {firmware.digest}"
        )
        if args.action == "inspect":
            return
        actual = captured_sectors(read_capture(args.capture))
        expected = list(firmware.sectors(0, 0x6000))
        if len(actual) != len(expected):
            raise ValueError("captured and generated sector counts differ")
        count = 0
        for (address, packets), (expected_address, data) in zip(
            actual, expected, strict=True
        ):
            if address != expected_address:
                raise ValueError(f"unexpected sector address {address:#x}")
            for index, (packet, generated) in enumerate(
                zip(packets, sector_reports(address, data), strict=True)
            ):
                if packet != generated:
                    raise ValueError(
                        f"packet mismatch at sector {address:#x}, packet {index}"
                    )
                count += 1
        print(
            f"MATCH: {len(actual)} sectors, {count} outgoing HID reports, byte for byte"
        )
    except (ValueError, RuntimeError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from error

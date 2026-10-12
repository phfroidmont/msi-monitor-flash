# MSI monitor flash — Linux

An experimental, native Linux scaler-firmware tool for the **MSI MPG 321URX
QD-OLED**. It communicates directly with the monitor's USB controller, without
Windows, Wine, Gaming Intelligence, GPU passthrough, or MSI's DLLs.

**Hardware-tested:** successfully re-flashed FW.021 entirely from Linux, verified
all 682 sectors, and checked firmware identity/calibration after reboot.

**This is unofficial firmware-writing software. A bug, power interruption, or
USB disconnect can leave your monitor unusable. Keep the working Windows VM
recovery setup until you are satisfied with the tool. Recovery is not guaranteed.**

## Narrow scope, deliberate safeguards

- One verified profile: MPG 321URX, HID model `0x9f`, protocol `V23`, panel
  `SDC QMC315AA01_D01`, NT68550 scaler, GD25Q128 (`c84018`) SPI flash.
- USB VID/PID alone is not enough: MSI shares `1462:3fa4` across monitors.
  The tool also checks the HID descriptor, model, protocol and panel.
- Firmware metadata and a known download SHA-256 must both match. Currently
  only MSI's FW.020/FW.021 files are recognized; newer releases deliberately
  require another review. Downgrades are refused.
- Writes the inactive boot bank, verifies every 512-byte block using the scaler's
  checksum command, commits the new boot table last, then invalidates the old marker.
  It does not replace the bootloader. Checksums are 16-bit sums, not cryptographic
  read-back verification or a guarantee against firmware bugs.
- Saves colour calibration and serial number before entering ISP; restores them
  if needed and checks them again after reboot.
- No generic register editor, forced model override, blind replay or automatic
  retry of uncertain flash writes.

## Run

With Nix, from **any directory** (replace the path with your checkout):

```sh
nix run /home/phfroidmont/Projects/msi-monitor-flash -- info
nix run /home/phfroidmont/Projects/msi-monitor-flash -- inspect /path/to/firmware.bin
```

From the checkout, use `nix run . -- info`. Firmware and backup paths are relative
to the directory you run the command from, not the checkout.

The flake provides a default app/package, a named `msi-monitor-flash` app/package,
a development shell (`nix develop`) and checks (`nix flake check`), with nixpkgs
pinned in `flake.lock`. Linux x86_64 and aarch64 outputs are available.

`nix build /path/to/checkout` creates `result/bin/msi-monitor-flash`. Its closure
includes only the original Python code, Python/PyUSB and libusb, **not** captures,
calibration backups, firmware or MSI binaries.

Elsewhere: Python 3.10+, PyUSB and libusb 1.0. Install PyUSB in a virtual environment,
and run the same `python -m msi_flash` commands from this directory (or install the
project with `pip install .` in that environment). There are no
MSI runtime dependencies. `MSI_FLASH_LIBUSB` can specify an explicit libusb library
path if the system loader cannot find it.

`info` only sends the five verified identity GET requests. `inspect` and
`compare-capture` are entirely offline.

Download and extract the firmware yourself from
[MSI's support page](https://www.msi.com/Monitor/MPG-321URX-QD-OLED/support#firmware).
Do not use firmware for the 321URXW, 322URX, or MAG models.

### USB permissions

Install `contrib/70-msi-monitor.rules` using your distribution's normal udev
configuration, then reload the rules and reconnect the USB cable. On NixOS:

```nix
services.udev.packages = [
  (pkgs.writeTextDir "lib/udev/rules.d/70-msi-monitor.rules"
    (builtins.readFile /path/to/msi-monitor-flash/contrib/70-msi-monitor.rules))
];
```

The rule belongs **before** `73-seat-late.rules`, which applies `uaccess`. It grants
the active desktop user access to this USB device, not to arbitrary hardware.
The tool claims interface 0 through libusb and reattaches the Linux HID driver
on close. Stop Gaming Intelligence, its VM, and other MSI USB-control programs
first. Multiple matching monitors are refused.

### Probe ISP without firmware writes

This **temporarily blanks and restarts the monitor**. It changes volatile scaler
registers to read the flash identity and boot markers, but does not erase/program
firmware or change SPI protection bits.

```sh
mkdir -p backups
systemd-inhibit --what=idle:sleep --why='Inspect monitor ISP' \
  nix run /path/to/checkout -- probe-isp --backup backups/before-probe.json
```

### Flash

1. Connect **only DisplayPort, USB-B upstream and power** to the monitor. Unplug
   HDMI and USB-C from the monitor. Keep USB-B on a stable direct connection.
2. Keep the computer on AC power. Keep the laptop lid open and a second screen
   available if possible. The target screen will go black.
3. Disable host idle/sleep handling. Do not suspend, switch input/KVM, rebuild the
   operating system, unplug cables, or interrupt the process during the write.
4. Use a **new** backup filename; existing backups are never overwritten.

```sh
systemd-inhibit --what=idle:sleep --why='Monitor firmware update' \
  nix run /path/to/checkout -- flash firmware/MSI_MPG321URX_V021_7F69.bin \
    --backup backups/before-flash.json --yes
```

The tool checks external-power presence; it cannot determine whether you left an
unused HDMI/USB-C cable connected or whether a USB hub is reliable. `--yes`
acknowledges those physical requirements and the risk of unsupported flashing.
Expect roughly ten minutes, including checksum checks and the final reboot wait.

If it fails **after writes started**, leave power connected and save the log. The
tool does not automatically reset the scaler or retry a possibly partial write.
Use the known-working MSI VM recovery route; do not experiment with raw commands.

After success, follow MSI's instructions: unplug/reconnect the monitor's AC power,
reset its OSD settings, and reapply your picture settings.

## Tests and evidence

```sh
nix flake check
nix develop -c python -m unittest discover -s tests -v
nix develop
python -m msi_flash compare-capture firmware/MSI_MPG321URX_V021_7F69.bin \
  captures/gi-flash-fw021.pcap
```

The local capture validates **682 sectors / 57,970 outgoing sector reports** byte
for byte. A separate test exercises the complete ISP sequence against recorded
responses, including identity, SPI status, bank markers, table invalidation,
programming and exit. Four short-SPI requests differ only in unused argument
padding: MSI sends uninitialized stack bytes there; this implementation sends
zeroes and recalculates the XOR checksum.

Capture tests skip when the private local files are absent. Other tests use
synthetic data and cover malformed replies, block-checksum failures, wrong models,
unknown images, bank relocation, ambiguous markers and calibration preservation.

See [protocol notes](docs/protocol.md) and [hardware test record](docs/testing.md).

## Repository contents

Only original implementation code, synthetic tests and functional protocol notes
belong in Git. **Do not commit MSI binaries, decompiled output, firmware images,
USB captures, or personal calibration backups.** Captures contain the firmware
being written. These materials stay in gitignored `re/`, `firmware/`, `captures/`
and `backups/` directories. No remote repository is required.

Not affiliated with or endorsed by MSI. See `LICENSE`.

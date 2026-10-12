# Local verification record

Hardware: MSI MPG 321URX QD-OLED, model ID 9F, HID protocol V23,
NT68550 + GD25Q128 SPI flash, connected to NixOS on `stellaris`.
Date: 2026-10-12.

## Completed checks

1. MSI Gaming Intelligence 0.1.4.9 successfully re-flashed FW.021 from a Windows
   VM with USB passthrough. QEMU recorded the USB transaction locally.
2. The original Linux packet builders reproduce all 682 captured sectors and
   57,970 sector reports byte for byte, including the final table's AA marker.
3. A complete ISP transaction test consumes all captured binary requests/replies,
   validating operation order and response interpretation. Only unused short-SPI
   argument bytes are normalized (MSI's uninitialized stack padding versus zero).
4. Native Linux identity and colour/serial calibration reads succeeded.
5. A native Linux ISP probe identified NT68550 / `c84018`, read markers `(AA, 00)`,
   restarted the monitor and read FW.021 back successfully. No firmware write
   occurred in that probe.

## Native flash

**Succeeded:** native FW.021 → FW.021 flash into the upper bank, with no Windows VM
or MSI runtime binaries running.

- Start: 02:45:29 local time; ISP identification completed at 02:45:33.
- Destination payload bank: `800000`; new table: `007000`; old table: `006000`.
- All 682 sectors (5,456 individual 512-byte block sums) verified.
- New bank committed at 02:52:59; old marker invalidation also verified.
- Monitor rebooted and the Linux tool read FW.021, V23 and the expected identity.
- Colour calibration and serial data matched the pre-flash backup.
- Total command duration: approximately 8 minutes, including the reboot wait.
- USB returned to the Linux HID driver; the sleep/idle inhibitor ended normally.

This is a successful test on one monitor, not a guarantee for every unit or future
firmware. Native FW.020 flashing has not been tested; its metadata/hash were
checked offline. The reference lower-bank transaction remains the successful MSI
Windows capture, rather than a second unnecessary native flash.

## Private evidence (not distributed)

- `captures/gi-flash-fw021.pcap`: reference Windows transaction.
- `captures/linux-flash-fw021.log`: native operation/verification log.
- `backups/before-probe.json` and `backups/before-linux-flash.json`: personal
  calibration recovery records. Never commit these.
- `firmware/MSI_MPG321URX_V021_7F69.bin`: MSI download, SHA-256
  `4934abd71dff21492dda3b2dd630f3539ba1d6639de88f2f3684fd03d59f019e`.

The Windows VM is retained as a potential recovery option, not proof that any
failure is recoverable.

# Verified MPG 321URX protocol facts

These notes describe functional interfaces observed on owned hardware and checked
against MSI Gaming Intelligence 0.1.4.9. They do not contain MSI source code.
Other MSI models can share the USB VID/PID without sharing this protocol/profile.

## USB transport

- Composite device `1462:3fa4`, interface 0, HID class 3.
- Interrupt OUT endpoint `0x02`, interrupt IN endpoint `0x81`.
- Reports are exactly 64 bytes, including the first byte (report ID).
- Report ID `0x01`: OSD/control ASCII and interface selection.
- Report ID `0x04`: the scaler I2C/ISP channel.
- Verified HID descriptor SHA-256:
  `fa85f6013264f197b61acda0e51777d56a6776c4f7c5bc3459deff0a051f8c14`.

Reading the descriptor, not just the product string, catches a different protocol
generation. The application then queries model, protocol and panel before ISP.

## Identity and calibration

Regular GET report: `01` + ASCII `58` + five-byte feature code + CR + zero padding.
Response: `01` + ASCII `5b` + echoed code + value + CR + zero padding.

| GET code | Verified result |
| --- | --- |
| `00140` | `00` followed by binary `9f` (model identifier) |
| `00150` | `V23` |
| `001<0` | three-digit scaler firmware version |
| `00130` | 13-byte monitor serial |
| `001?0` | length byte, panel text, fixed trailing zeroes |

Calibration is binary; CR/NUL can occur **inside** it:

- GET `0[000` replies with shortened prefix `5b00[`, 12 colour bytes, an 8-bit
  additive checksum, then CR.
- GET `00131` replies with 13 serial bytes, an 8-bit additive checksum, then CR.
- A restore transaction begins with `5b00Z a5 55 aa 30 30 CR` and ends with
  `5b00Z a8 aa 55 30 30 CR`.
- The colour payload uses `5b001i`; serial uses `5b001j`. Both include the saved
  bytes, their additive checksum, padding ASCII zeroes and CR.
- Successful SET acknowledgement is ASCII `5600+` + CR.

Interface selection is `01 e0 <0=normal, 1=I2C>` followed by FF padding; response
starts `01 5a <selected interface>`.

## ISP report framing

Outer request: `[04, command, flag, 00] + payload + zero padding`.
Outer response: `[04, echoed command, 01, 00] + optional payload + zero padding`.
Status other than `01` is rejected.

For checksummed I2C operations the request payload is
`[6e, body length] + body + XOR(all preceding payload bytes)`.
The reply payload is `[6f, body length, response opcode] + data + XOR`.
The XOR is only a framing check, not firmware integrity verification.

| Command | Meaning | Reply handling |
| --- | --- | --- |
| A0 | identify/enter scaler ISP | inner A5 + ID byte (00 here) |
| A9 | write 16-bit scaler register | inner 87 |
| AA | read 16-bit scaler register | inner 78 + byte |
| AF | execute SPI staging descriptor | inner DA |
| AD | configure flash operation | outer acknowledgement |
| AE | short SPI operation, up to 7 bytes | inner AC |
| A2 | select extended address bank | inner F0 + bank byte |
| B5 | checksum 512 flash bytes | inner C9 + big-endian 16-bit sum |
| B6/B7/B8 | program a 512-byte block | only B8 replies (inner C3) |
| BC | write a bridge register | outer acknowledgement |
| BD | configure sector address/staging | outer acknowledgement |
| BF/C0/C1 | stage a 4096-byte sector | only C1 replies |
| C2 | trigger sector operation | outer acknowledgement, then 200 ms wait |
| C3 | poll sector completion | 55 = done, 05 = failed |
| A1 | restart/leave scaler ISP | outer acknowledgement; re-enumeration follows |

I2C command flag is usually `6f`; the block/staging operations use `00`, and C3
uses `b1`. Sector operation C2 has an inner checksummed body but flag `00`.
Never infer the correct flag from the presence of a checksum alone.

## SPI and boot-bank layout

The verified SPI part is GD25Q128, JEDEC ID `c8 40 18`, 16 MiB capacity.
Other parts are intentionally refused.

The SPI staging descriptor lives at scaler SRAM `a700`. Its boundaries are set
through registers `f0c1`–`f0c6`. AF executes it; AA reads the result. Short SPI
operations use AE; their readback byte is at register `f880`.

- Lower firmware payload base: `000000`; its boot table is at flash `006000`.
- Upper firmware payload base: `800000`; its boot table is at flash `007000`.
- Marker `aa` in a table's final byte indicates a committed table.
- The implementation only accepts one AA marker and one zero marker. Both valid,
  neither valid, FF, and other values are recovery/unknown states, not guesses.
- To invalidate a table, MSI programs zeroes over its **final 512-byte block**
  (`006e00` or `007e00`) and verifies a zero sum. This is not a whole-chip erase.
- Payload sectors start at image offset `8000`. The first eight sectors are
  omitted: bootloader and boot tables are not replaced as ordinary payload.
- The last short sector is padded with FF, not zeroes.
- The boot table is sourced from image `6000:7000`. Nine big-endian addresses in
  its 8-byte entries are relocated by the chosen bank base. The final byte is
  changed to AA. This table is written and verified **after all payload sectors**.
- Only then is the previous table's marker invalidated.

### Sector transfer

Prepare BC/BD bridge registers and staging address. Data is sent as:

1. BF carries 58 bytes after its 6-byte report header.
2. 67 C0 reports carry 60 bytes each after their 4-byte headers.
3. C1 carries the remaining 18 bytes.
4. C2 starts the sector operation. Wait and poll C3 for completion.
5. Query B5 eight times (one per 512-byte block), compare each big-endian 16-bit
   additive sum with the corresponding image block.

No blind retries follow a transport error or bad checksum. Before the final table
commit, the previous boot table remains valid, but this alone does not guarantee
recoverability under all failure modes.

## Raw image metadata

Tagged fields have prefix `55 aa 66 99` and occur once in the verified images:

| Tag | Payload |
| --- | --- |
| UART | binary model byte `9f` |
| PANEL | length byte + `SDC QMC315AA01_D01` |
| SCALER | length byte + `NT68550` |
| FWVER | two bytes: major byte 00, version byte 14/15 (hex) |

The CLI interprets the two FWVER bytes as 20/21 for this profile. Metadata is not
authenticated; the separate known-download SHA-256 gate rejects unreviewed files.

## Known evidence limitations

The captured Windows transaction wrote the lower payload bank. Upper-bank
address/table relocation is additionally derived from the updater's functional
behaviour and covered by synthetic tests; a live test record is kept separately.
There is no claim of support for Realtek/MediaTek scalers, older USB protocol
generations, MCU firmware, EDID replacement or USB-C hub updates.

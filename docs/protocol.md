# USB protocol

The camera is a USB CDC serial device (product string `NIR Eye Cam`, serial
number = the board's unique ID). Baud rate is ignored. The firmware streams
only while the host holds DTR (pyserial does by default). When DTR drops it
stops streaming and switches the IR LED off.

Defined in `common/nec_protocol.h` (both firmwares) and `host/nireyecam/protocol.py`.
Change both together.

## Device → host: packets

32-byte little-endian header, then `payload_len` bytes.

| Offset | Type | Field |
|---|---|---|
| 0 | 4 bytes | magic `NEC1` |
| 4 | u8 | type: `F` frame, `S` sync edge, `T` text |
| 5 | u8 | flags: bit 0 = auto exposure on |
| 6 | u16 | header size (32) |
| 8 | u32 | payload length |
| 12 | u32 | seq: frame = VSYNC count since START (gaps = dropped frames); sync = edge count since boot; text = reply count |
| 16 | u64 | t_us: device clock (µs since boot) at the frame's VSYNC edge / the sync edge |
| 24 | u16 | width (frame only) |
| 26 | u16 | height (frame only) |
| 28 | u16 | exposure in sensor lines (frame only) |
| 30 | u8 | analog gain index 0–3 = 1×/2×/4×/8× (frame only) |
| 31 | u8 | reserved |

A frame's payload is `width × height` 8-bit pixels, row-major. Sync packets
have no payload. A text payload is UTF-8.

## Host → device: commands

ASCII lines ending in `\n`. Each gets exactly one text reply,
`OK <CMD> …` or `ERR <CMD> <reason>`:

| Command | Effect |
|---|---|
| `PING` | `OK PING` |
| `INFO` | `OK INFO {json}`: firmware, board, uid, sensor status, current settings, sync edge count |
| `INIT` | retry sensor detection |
| `RES 160x120` \| `320x240` \| `320x320` | resolution (stopped only); 160×120 is 2×2 binned |
| `EXP <lines>` | integration time, clamped to [2, frame length − 2] |
| `AGAIN <0-3>` | analog gain |
| `AE <0\|1>` | sensor auto exposure |
| `FLL <lines>` | frame length; longer = lower frame rate, longer maximum exposure |
| `LED <0-1000>` | IR LED PWM duty (per mille) |
| `SYNCGEN <0\|1>` | drive SYNC_OUT with the random pulse train |
| `START` / `STOP` | stream frames |

Replies are sent between packets, never inside a frame.

### ESP32-P4 / VD55G1 build

Same packets and the same commands, with these differences
(`firmware-p4/main/main.c`):

| Command | Difference |
|---|---|
| `RES <W>x<H>` | any centred crop up to 804×704, W×H a multiple of 8 |
| `ROI <X> <Y>` | new: move the crop (stopped only) |
| `AGAIN <0-28>` | VD55G1 gain code: gain = 32 / (32 − code) |
| `DGAIN <q8>` | new: digital gain, 256 = 1× |
| `LED <0-1000>` | on/off: any value above 0 lets the sensor's STROBE drive the LEDs |
| `TRIG <hz>` | new: frame-trigger square wave on TRIG_OUT (0 = off) |
| `EXTSYNC <0\|1>` | new: start frames on the sensor's GPIO0 (not implemented yet) |

`INFO` adds `sensor`, `model_id`, `revision`, `x`, `y`, `line_time_ns`,
`frame_period_us`, `dgain`, `trig_hz`, `extsync` and `csi_errors`.

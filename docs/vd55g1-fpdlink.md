# Design sketch: VD55G1 serializer head over one coax (FPD-Link III)

Status: **design only, nothing built**; the chosen design for the freely
moving rigs (see "Options" in [hardware.md](hardware.md)). Items marked
**VERIFY** are open questions that could change the design; the plan below
tests them first.

Why this build: on a freely moving animal the camera's cable has to pass the
commutator, and the rigs' Doric joint has only four contacts free for it
(the HDMI port carries Neuropixels; see "Tether and commutator" in
[hardware.md](hardware.md)). The [ESP32-P4 build](vd55g1-esp32p4.md) needs all
four for USB, which leaves no wire for SYNC, puts a whole ESP32-P4 board on the
head, and sends USB through a slip ring that is not rated for it.

This build moves everything but the sensor off the animal. The head carries
the VD55G1, a TI **DS90UB953** serializer and its supplies. One thin coax
carries power to the head, video from it, and I²C and GPIO back to it, the way
Neuropixels, Open Ephys ONIX headstages and the UCLA Miniscope V4 do. A
deserializer and the ESP32-P4 sit at the base, and USB runs on an ordinary
cable from there to the PC.

## Block diagram

```
   ┌──────────────── head (on the animal) ────────────────┐
   │ eye ── close-up lens ── CAM-5G1 promodule            │
   │        CSI-2, 1 lane, RAW10 ──► DS90UB953 ──┐        │
   │        I²C, XSHUTDOWN, GPIO0 ◄─ GPIO / I²C ─┤        │
   │        GPIO1 STROBE ──► LED MOSFET ──► IR LEDs       │
   │ 25 MHz osc ──► 953 CLKIN and sensor CLKIN            │
   │ PoC filter ──► LDOs: 2.8 / 1.8 / 1.15 V             │
   └──────────────────────────────────────┬───────────────┘
                                          │ 50 Ω micro-coax, ~1 m
                     Molex 4-pin, 2 contacts ─ Doric joint ─ Molex 4-pin
                                          │ coax, ~1 m
   ┌──────────────── base (off the animal) ┴──────────────┐
   │ PoC injector ──► DS90UB934 ──► 12-bit parallel ──►    │
   │   ESP32-P4 DVP camera input (PCLK, VS, HS, D[9:2])   │
   │ rig TTL ──► ESP32-P4 GPIO (timestamps, trigger)      │
   │ ESP32-P4 USB-HS ──► PC (nireyecam protocol)          │
   └──────────────────────────────────────────────────────┘
```

## Parts

| Part | Notes |
|---|---|
| ST **CAM-5G1-080NIR** promodule | As in the ESP32-P4 build: 804 × 704 mono global shutter, 940 nm bandpass. Rails 2.8 / 1.8 / 1.15 V, external clock 6–27 MHz, MIPI 250–1200 Mbit/s. |
| TI **DS90UB953-Q1** serializer (head) | CSI-2 in, FPD-Link III out over coax or shielded twisted pair, power-over-coax (PoC) compatible. Single 1.8 V supply, 0.28 W typical, 5 × 5 mm VQFN-32. Run in its backward-compatible **DVP mode** (below). |
| 25 MHz oscillator (head) | DVP mode needs an external clock on the 953's CLKIN, and the sensor must run from the same clock, either directly or from the 953's CLK_OUT. |
| TI **DS90UB934-Q1** deserializer (base) | FPD-Link III in, 12-bit parallel LVCMOS out (up to 100 MHz), 50 Ω coax or 100 Ω STP, PoC. I²C pass-through to the remote side, back-channel GPIOs, 3.3 V LVCMOS I/O. 7 × 7 mm VQFN-48. |
| ESP32-P4 (base) | Takes the 934's parallel output on its DVP camera input. No longer needs to be small or light. |
| 50 Ω micro-coax | Miniscope V4 uses coax down to 0.3 mm; ONIX uses 150–300 µm. Two pieces, ~1 m each side of the joint. |
| PoC networks (head and base) | Inductor/ferrite filters that put DC on the coax without loading the signal. TI SNLA224 (913A-class links) and SNLA272 (953). |

## The link: why DVP mode

The 953's normal mode runs the coax at 4.16 Gbit/s, which a slip ring and
Micro-Fit pins are unlikely to carry. In **DVP mode** it sends the older 28-bit
frames that the DS90UB934 and DS90UB914A receive (TI SNLA270):

    forward channel   = CLKIN × 28
    CSI-2 throughput  = CLKIN × 20   (RAW10; must equal the sensor's MIPI rate)
    pixel clock out   = CLKIN × 2    (RAW10, at the 934)

CLKIN can run 25–66.5 MHz in RAW10 (953 datasheet, clocking modes). Start at
the bottom:

| CLKIN | Forward channel | Pixel rate | Full 804 × 704 | 320 × 320 crop |
|---|---|---|---|---|
| 25 MHz | 700 Mbit/s | 50 Mpixel/s | ~88 fps (link-limited) | sensor-limited |
| 50 MHz | 1.4 Gbit/s | 100 Mpixel/s | sensor-limited | sensor-limited |

Frame rates are before blanking. The sensor's own limits are about 170 fps at
800 × 700 and 460 fps at 320 × 240. 700 Mbit/s is the rate the ONIX
headstages run over their micro-coax ("700 MHz carrier"), so it is the
starting point with a track record.

What DVP mode costs:

- **RAW10 or RAW12 only, no RAW8.** The sensor sends RAW10
  (`REG_FORMAT_CTRL` 0x030A = 10, CSI-2 data type 0x2B instead of 0x2A;
  **VERIFY** the VD55G1 accepts it). Wiring the 934's D[9:2] to an 8-bit DVP
  input hands the ESP32-P4 8-bit pixels, so the NEC1 frames stay RAW8.
- **The sensor's MIPI rate is fixed by the clock:** at 25 MHz, one lane at
  exactly 500 Mbit/s. **VERIFY** the VD55G1's PLL can make exactly that from
  25 MHz.
- **A slower back channel**, 2.5 Mbit/s, which needs different AC-coupling
  capacitors (100 nF and 47 nF with a 49.9 Ω load, SNLA270 §2).
- **Fewer features:** TI says many of the 953's advanced features are not
  available in DVP mode.

## Through the commutator

- **Two of the four Molex contacts:** the coax centre on position 3 and its
  shield on 4, the bottom row (positions as in hardware.md's camera cable
  draft; the joint passes each position straight through). That leaves the
  top row, 1 and 2, spare. If power on the coax proves noisy through the slip
  ring, use them for a separate DC pair and drop the PoC networks.
- **Evidence:** Doric lists the UCLA Miniscope V4, whose single coax carries
  power, control and video, as tested through this model of joint, but
  through Doric's own adapter on the HDMI port. The Micro-Fit path is
  untested: **VERIFY** in phase 0.
- **Power:** the head draws ~0.5 W (953 0.28 W, sensor ≤ ~0.15 W, LEDs and
  regulator losses; estimate). Thin coax has a resistive centre conductor, so
  measure the tether's loop resistance first. ~5–6 V on the coax, as ONIX
  headstages use, with LDOs on the head is simplest; at ~12 V the head draws
  only ~50 mA and even 10 Ω loses ~0.5 V, but it needs a small buck converter
  near the sensor.

## Synchronization

The ESP32-P4 is at the base, so the rig's TTL goes into one of its GPIOs
locally. No sync wire crosses the joint.

1. **Time-aligned (free-running sensor):** the ESP32-P4 timestamps the sync
   pulses and each frame start, taken from the 934's VS output. The latency
   through the link is fixed (**VERIFY** it is constant), so
   `nireyecam-align` works unchanged.
2. **Exposure-locked (EXTSYNC):** the ESP32-P4 drives a 934 GPIO, the back
   channel carries it to a 953 GPIO, and that drives the sensor's GPIO0. In
   DVP mode the 953 datasheet gives back-channel GPIOs a typical latency of
   12.2 µs and jitter of 12 µs, up to 20 kHz (Table 6-7). Small next to a
   millisecond exposure, but it is jitter on the exposure start of every
   camera on the line.
3. **STROBE stays on the head:** sensor GPIO1 drives the LED MOSFET directly.
   The 953 and the sensor's I/O both run at 1.8 V, so the head needs no level
   shifters (**VERIFY** the 953's GPIO levels).

## Firmware (ESP32-P4, at the base)

- **Capture:** ESP-IDF's DVP camera controller (`esp_cam_ctlr_dvp`, 8 or 16
  data lines) instead of the CSI controller. The 934's pixel clock is 50 MHz
  at CLKIN 25 MHz. A community driver quotes 80 MHz as the ESP32-P4's DVP
  limit: **VERIFY** in the technical reference manual. The alternative is a
  **DS90UB954** deserializer, whose CSI-2 output would keep `capture.c` as it
  is; SNLA270 covers only the 934 and 914A, so **VERIFY** the 954 accepts a
  953 in DVP mode.
- **I²C:** the ESP32-P4 talks to the 934, which passes I²C through to the 953
  and, by alias address, to the sensor. Bring-up programs the 953's DVP-mode
  registers (SNLA270 §3), then runs the existing `vd55g1.c` against the alias
  address, with RAW10.
- **GPIOs:** XSHUTDOWN and GPIO0 come from 953 GPIOs, set through I²C or the
  back channel. FSYNC_OUT is no longer needed for timestamps (VS at the base
  replaces it).
- **Host:** unchanged. Frames reach `nireyecam-record` as RAW8 NEC1 packets
  over USB-HS, on a normal cable.

## Plan, risk first

0. **Prove the link through the joint before designing anything.** No sensor
   is needed: both chips have a built-in self-test (BIST) that counts link
   errors (the 953's `BIST_ERR_CNT`, register 0x54), and the 953 can generate
   test video. **VERIFY** both run in DVP mode.
   - **Boards:** TI's [DS90UB953-Q1EVM](https://www.ti.com/tool/DS90UB953-Q1EVM)
     and [DS90UB934-Q1EVM](https://www.ti.com/tool/DS90UB934-Q1EVM). TI lists
     them as compatible. Both have FAKRA coax connectors, and the 934 board
     has a USB bridge for TI's Analog LaunchPAD register tool.
   - **Rework for DVP mode** (SNLA270 §2): on the 953 board, fit the 0 Ω
     resistor at R1 that enables its on-board oscillator for CLKIN and strap
     MODE to DVP. SNLA270 asks for 100 nF and 47 nF AC-coupling capacitors
     with a 49.9 Ω load on both sides, for the slower back channel: change
     them on the 953 board and check the 934 board's values.
   - **Through the joint:** a FAKRA-to-micro-coax pigtail on each board, ~1 m
     of micro-coax on each side, centre to position 3 and shield to 4 of the
     Molex 4-pin port (positions as in hardware.md's camera cable draft).
   - **Test:** power the 953 locally first, as SNLA270 recommends for
     debugging, then over the coax. Run BIST while turning the joint by hand
     for many minutes and count errors and losses of lock. Then raise CLKIN
     towards 50 MHz to see how much margin there is.
1. **Sensor on the link:** the CAM-5G1 on its P-Board, CSI-2 into the 953
   board (pinout and lane polarity checked), the 934's parallel output into an
   ESP32-P4 dev board's DVP pins. RAW10 frames, then RAW8 NEC1 packets into
   `nireyecam-record`. **Clock catch:** the P-Board clocks the sensor from its
   own oscillator (12 MHz in ST's overlay, `firmware-p4/main/board.h`), but in
   DVP mode the sensor must run from the 953's clock, directly or through the
   953's CLK_OUT, or "a variation or drift in CSI-2 data could cause buffer
   overflows" (SNLA270 §6). **VERIFY** whether the P-Board's CLKIN can be fed
   from the 953 board; if not, this step waits for the head board, or runs
   only as a short, drift-tolerant check.
2. **Sync:** time-aligned first, then the back-channel trigger to GPIO0 with
   two heads on one trigger line; measure the exposure skew with a
   fast-blinking LED.
3. **Head and base boards:** head = BM28 receptacle + 953 + oscillator + LDOs
   + PoC filter + LED driver + coax connector, as small as the parts allow;
   base = coax connector + PoC injector + 934 + ESP32-P4 module + USB-C + sync
   input.

## Compared with the ESP32-P4 build on the same joint

| | ESP32-P4 on the head, USB | Serializer head |
|---|---|---|
| Joint contacts | all 4 | 2 (2 spare) |
| SYNC through the joint | no path | not needed (timestamps at the base); trigger over the back channel |
| On the head | ESP32-P4 board + sensor, ~1.25 W | sensor + serializer, ~0.5 W |
| Image data to the PC | ~1 MB/s at USB full speed; ~10 MB/s only if high speed survives the joint | ~50 Mpixel/s over the link; USB-HS on a normal cable |
| Proven through this joint model | no | a similar link (Miniscope V4), but via the HDMI port |
| Work | cables, and the existing ESP32-P4 design | two new boards, new capture path |

## Sources

- TI [SNLA270](https://ti.com/lit/pdf/snla270), "DS90UB953-Q1 Backwards
  Compatibility Modes for Operation With Parallel Output Deserializers" (DVP
  mode, rate equations, AC coupling, PoC notes, register setup).
- TI [DS90UB953-Q1 datasheet](https://www.ti.com/lit/pdf/snls552) (SNLS552E):
  features, clocking modes (RAW10 external clock 25–66.5 MHz), Table 6-7
  (back-channel GPIO timing). [Product page](https://www.ti.com/product/DS90UB953-Q1).
- TI [DS90UB934-Q1](https://www.ti.com/product/DS90UB934-Q1) product page and
  datasheet (SNLS507C: I²C pass-through, back-channel GPIO, 3.3 V LVCMOS I/O).
  TI SNLA224 and SNLA272 (power over coax).
- Open Ephys ONIX docs,
  [Coaxial Data Serialization & Power](https://open-ephys.github.io/onix-docs/Hardware%20Guide/Headstages/serialization.html)
  (DS90UB933/934, 150–300 µm coax, 700 MHz carrier).
  [Miniscope V4 wiki](https://github.com/Aharoni-Lab/Miniscope-v4/wiki)
  (single 50 Ω coax for power, communication and data).
- Doric Lenses
  [Assisted 1x1 Fiber-optic & Electric Rotary Joints, 24 contacts](https://neuro.doriclenses.com/products/assisted-fiber-optic-electric-rotary-joints-1)
  (Miniscope V4 tested via HDMI + 4).
- ESP-IDF `components/esp_driver_cam/dvp/include/esp_cam_ctlr_dvp.h` (v6.1);
  [esp_cam_io_parl](https://components.espressif.com/components/haqqscripter/esp_cam_io_parl)
  README (80 MHz DVP clock, community driver).
- CNX Software,
  [VD55G1 announcement](https://www.cnx-software.com/2023/12/12/stmicro-vd55g1-small-low-power-global-shutter-i3c-camera-sensor-for-computer-vision/)
  (frame rates by resolution).

"""A software stand-in for the firmware, so the host side runs without hardware.

It speaks the same protocol as the firmware, with the RP2040 command set plus
the ESP32-P4's TRIG, EXTSYNC and BLINK. Each simulated camera has its own
clock (offset + ppm drift, like a real crystal), and all cameras in one
``SimWorld`` share a sync wire, a frame-trigger wire and a blinking test LED
that every camera sees, so recording, alignment and the two-camera sync check
can be tested end to end. ``truth`` records the true start time of every frame
and pulse, which is what the tests check against.

A frame's exposure is the window [start, start + exposure lines x line time],
for the whole frame at once (a global shutter, like the VD55G1). With EXTSYNC
on, a frame starts ``trigger_latency_s`` after a trigger edge, and an edge that
arrives while the previous frame is still being read out is missed.
"""

from __future__ import annotations

import bisect
import json
import math
import random
import threading
import time

import numpy as np

from . import protocol

RESOLUTIONS = {(160, 120): 0x0080, (320, 240): 0x0104, (320, 320): 0x0158}  # -> default frame length
BASE_FPS = 30.0  # frame rate at each resolution's default frame length


class SimWorld:
    """True time plus the shared sync line."""

    def __init__(self, seed: int = 0) -> None:
        self.t0 = time.monotonic()
        self._rng = random.Random(seed)
        self._lock = threading.Lock()
        self._pulses: list[float] = []
        self._next: float | None = None
        self.driver: object | None = None  # the camera whose SYNCGEN is on
        # TRIG and BLINK: (first edge, period) in true time, from the owner's clock.
        self.trigger_owner: object | None = None
        self._trigger: tuple[float, float] | None = None
        self.blink_owner: object | None = None
        self._blink: tuple[float, float] | None = None

    def now(self) -> float:
        return time.monotonic() - self.t0

    def set_syncgen(self, who: object, on: bool) -> None:
        with self._lock:
            if on and self.driver is None:
                self.driver = who
                self._next = self.now() + 0.001
            elif not on and self.driver is who:
                self.driver = None
                self._next = None

    def set_trigger(self, who: object, hz: float, ppm: float = 0.0) -> None:
        """The frame-trigger wire. One camera drives it, at ``hz`` on its own clock."""
        with self._lock:
            if hz > 0 and self.trigger_owner in (None, who):
                self.trigger_owner = who
                self._trigger = (self.now() + 0.001, 1.0 / (hz * (1 + ppm * 1e-6)))
            elif hz <= 0 and self.trigger_owner is who:
                self.trigger_owner, self._trigger = None, None

    def trigger_edges_between(self, a: float, b: float) -> list[float]:
        """Trigger rising edges in (a, b]."""
        with self._lock:
            trig = self._trigger
        if trig is None:
            return []
        t0, period = trig
        first = max(0, math.floor((a - t0) / period) + 1)
        last = math.floor((b - t0) / period)
        return [t0 + n * period for n in range(first, last + 1)]

    def set_blink(self, who: object, hz: float, ppm: float = 0.0) -> None:
        """The test LED: a 50% square wave at ``hz`` on its driver's clock."""
        with self._lock:
            if hz > 0 and self.blink_owner in (None, who):
                self.blink_owner = who
                self._blink = (self.now() + 0.0005, 1.0 / (hz * (1 + ppm * 1e-6)))
            elif hz <= 0 and self.blink_owner is who:
                self.blink_owner, self._blink = None, None

    def blink_on_time(self, a: float, b: float) -> float:
        """Seconds the test LED is lit within [a, b]."""
        with self._lock:
            blink = self._blink
        if blink is None or b <= a:
            return 0.0
        t0, period = blink

        def lit_until(t: float) -> float:
            if t <= t0:
                return 0.0
            n, r = divmod(t - t0, period)
            return n * period / 2 + min(r, period / 2)

        return lit_until(b) - lit_until(a)

    def pulses_between(self, a: float, b: float) -> list[float]:
        """Rising edges in (a, b], generating the random train lazily."""
        with self._lock:
            while self._next is not None and self._next <= b:
                self._pulses.append(self._next)
                self._next += self._rng.uniform(0.4, 1.0)
            lo = bisect.bisect_right(self._pulses, a)
            hi = bisect.bisect_right(self._pulses, b)
            return self._pulses[lo:hi]


class _Eye:
    """A dark pupil in an iris, with a corneal glint; the pupil breathes slowly."""

    def __init__(self, w: int, h: int, rng: np.random.Generator) -> None:
        self.w, self.h, self.rng = w, h, rng
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        self.xx, self.yy = xx, yy
        self.s = w / 160.0
        # The test LED, in a corner clear of the eye.
        self.led = (xx - 0.08 * w) ** 2 + (yy - 0.1 * h) ** 2 < (3 * self.s) ** 2

    def render(self, t: float, brightness: float, led: float = 0.0) -> np.ndarray:
        s, xx, yy = self.s, self.xx, self.yy
        cx = self.w / 2 + 3 * s * math.sin(0.7 * t)
        cy = self.h / 2 + 2 * s * math.cos(0.5 * t)
        img = np.full((self.h, self.w), 150.0, np.float32)
        img[((xx - self.w / 2) / (0.45 * self.w)) ** 2 + ((yy - self.h / 2) / (0.33 * self.h)) ** 2 < 1] = 115
        r2 = (xx - cx) ** 2 + (yy - cy) ** 2
        img[r2 < (30 * s) ** 2] = 80
        pupil_r = s * (10 + 6 * math.sin(2 * math.pi * 0.25 * t))
        img[r2 < pupil_r**2] = 20
        img[(xx - cx - 6 * s) ** 2 + (yy - cy + 5 * s) ** 2 < (2 * s) ** 2] = 250
        img = img * brightness
        img[self.led] += led
        img += self.rng.normal(0, 3, img.shape)
        return np.clip(img, 0, 255).astype(np.uint8)


class SimDevice:
    """Emulates one camera's firmware on a background thread."""

    def __init__(self, world: SimWorld, seed: int = 0) -> None:
        rng = random.Random(seed)
        self.world = world
        self.uid = f"SIM{seed:013d}"
        self.clock_ppm = rng.uniform(-50, 50)
        self.clock_offset_s = rng.uniform(1, 100)  # seconds since "boot"
        self.trigger_latency_s = rng.uniform(5e-6, 60e-6)  # trigger edge -> exposure start
        self.truth_frames: dict[int, float] = {}   # seq -> true frame start
        self.truth_sync: dict[int, float] = {}     # sync seq -> true edge time

        self.width, self.height = 160, 120
        self.frame_length = RESOLUTIONS[(160, 120)]
        self.exposure = 107
        self.again = 0
        self.ae = False
        self.led = 0
        self.streaming = False
        self.vsync = 0
        self.sync_edges = 0
        self.extsync = False
        self.trig_hz = 0
        self.blink_hz = 0
        self.missed_triggers = 0
        self._next_frame: float | None = None
        self._pending: list[float] = []  # EXTSYNC frame starts not yet read out
        self._last_start: float | None = None
        self._last_trigger_check = world.now()
        self._last_sync_check = world.now()
        self._np_rng = np.random.default_rng(seed)
        self._eye = _Eye(self.width, self.height, self._np_rng)

        self._in = bytearray()
        self._out = bytearray()
        self._lock = threading.Condition()
        self._closed = False
        self._thread = threading.Thread(target=self._run, name=f"sim-{seed}", daemon=True)
        self._thread.start()

    def dev_us(self, true_t: float) -> int:
        return int(round((true_t * (1 + self.clock_ppm * 1e-6) + self.clock_offset_s) * 1e6))

    def frame_period(self) -> float:
        default = RESOLUTIONS[(self.width, self.height)]
        return (1.0 / BASE_FPS) * self.frame_length / default

    def exposure_s(self) -> float:
        return self.exposure * self.frame_period() / self.frame_length

    # -- host side
    def write(self, data: bytes) -> None:
        with self._lock:
            self._in += data

    def read_some(self, timeout: float = 0.05) -> bytes:
        with self._lock:
            if not self._out:
                self._lock.wait(timeout)
            data, self._out = bytes(self._out), bytearray()
            return data

    def close(self) -> None:
        self._closed = True
        self.world.set_syncgen(self, False)
        self.world.set_trigger(self, 0)
        self.world.set_blink(self, 0)
        self._thread.join(timeout=1.0)

    # -- device side
    def _emit(self, packet: bytes) -> None:
        with self._lock:
            self._out += packet
            self._lock.notify_all()

    def _reply(self, text: str) -> None:
        self._emit(protocol.pack(protocol.TEXT, text.encode(), t_us=self.dev_us(self.world.now())))

    def _run(self) -> None:
        while not self._closed:
            with self._lock:
                lines = []
                while b"\n" in self._in:
                    raw, _, rest = bytes(self._in).partition(b"\n")
                    self._in = bytearray(rest)
                    lines.append(raw.decode(errors="replace").strip())
            for line in lines:
                if line:
                    self._handle(line)

            now = self.world.now()
            for p in self.world.pulses_between(self._last_sync_check, now):
                self.sync_edges += 1
                self.truth_sync[self.sync_edges] = p
                self._emit(protocol.pack(protocol.SYNC, seq=self.sync_edges, t_us=self.dev_us(p)))
            self._last_sync_check = now

            edges = self.world.trigger_edges_between(self._last_trigger_check, now)
            self._last_trigger_check = now
            if self.streaming and self.extsync:
                for e in edges:
                    start = e + self.trigger_latency_s
                    if self._last_start is not None and start < self._last_start + self.frame_period():
                        self.missed_triggers += 1  # still reading out the previous frame
                        continue
                    self._pending.append(start)
                    self._last_start = start
                while self._pending and self._pending[0] + self.frame_period() <= now:
                    self._emit_frame(self._pending.pop(0))

            while (self.streaming and not self.extsync and self._next_frame is not None
                   and self._next_frame + self.frame_period() <= now):
                start = self._next_frame
                self._emit_frame(start)
                self._next_frame = start + self.frame_period() / (1 + self.clock_ppm * 1e-6)
            time.sleep(0.002)

    def _emit_frame(self, start: float) -> None:
        self.vsync += 1
        self.truth_frames[self.vsync] = start
        # The test LED adds light in proportion to how long it was lit during the exposure.
        lit = self.world.blink_on_time(start, start + self.exposure_s())
        img = self._eye.render(start, self._brightness(), led=1e5 * lit * 2**self.again)
        self._emit(
            protocol.pack(
                protocol.FRAME,
                img.tobytes(),
                flags=protocol.FLAG_AE if self.ae else 0,
                seq=self.vsync,
                t_us=self.dev_us(start),
                width=self.width,
                height=self.height,
                exposure=self.exposure,
                again=self.again,
            )
        )

    def _brightness(self) -> float:
        led = 0.05 + 0.95 * self.led / 1000  # no IR, almost no image
        exposure = self.exposure / 107
        return min(led * exposure * (2**self.again), 3.0)

    def _handle(self, line: str) -> None:
        parts = line.split()
        cmd, arg = parts[0].upper(), (parts[1] if len(parts) > 1 else None)

        def num(lo: int, hi: int) -> int | None:
            try:
                v = int(arg, 0)
            except (TypeError, ValueError):
                return None
            return v if lo <= v <= hi else None

        if cmd == "PING":
            return self._reply("OK PING")
        if cmd == "INFO":
            info = {
                "fw": "sim", "board": "sim", "uid": self.uid, "cam_ok": 1, "cam_error": "",
                "width": self.width, "height": self.height, "exposure": self.exposure,
                "frame_length": self.frame_length, "again": self.again, "ae": int(self.ae),
                "frame_period_us": round(self.frame_period() * 1e6),
                "line_time_ns": round(self.frame_period() / self.frame_length * 1e9),
                "led": self.led, "syncgen": int(self.world.driver is self),
                "trig_hz": self.trig_hz, "extsync": int(self.extsync), "blink_hz": self.blink_hz,
                "streaming": int(self.streaming), "slots": 10,
                "sync_edges": self.sync_edges, "sync_overflows": 0,
            }
            return self._reply("OK INFO " + json.dumps(info))
        if cmd == "INIT":
            return self._reply("OK INIT")
        if cmd == "START":
            if not self.streaming:
                self.streaming, self.vsync = True, 0
                self._next_frame = self.world.now() + self._rng_phase()
                self._pending, self._last_start = [], None
                self._last_trigger_check = self.world.now()
            return self._reply("OK START")
        if cmd == "STOP":
            self.streaming = False
            self._pending = []
            return self._reply("OK STOP")
        if cmd == "RES":
            if self.streaming:
                return self._reply("ERR RES stop first")
            try:
                w, h = (int(x) for x in (arg or "").lower().split("x"))
            except ValueError:
                return self._reply("ERR RES expected WxH")
            if (w, h) not in RESOLUTIONS:
                return self._reply("ERR RES supported: 160x120 320x240 320x320")
            self.width, self.height, self.frame_length = w, h, RESOLUTIONS[(w, h)]
            self.exposure = min(self.exposure, self.frame_length - 2)
            self._eye = _Eye(w, h, self._np_rng)
            return self._reply(f"OK RES {w}x{h}")
        if cmd == "EXP":
            v = num(0, 0xFFFF)
            if v is None:
                return self._reply("ERR EXP expected lines")
            self.exposure = max(2, min(v, self.frame_length - 2))
            return self._reply(f"OK EXP {self.exposure}")
        if cmd == "AGAIN":
            v = num(0, 3)
            if v is None:
                return self._reply("ERR AGAIN expected 0-3")
            self.again = v
            return self._reply(f"OK AGAIN {v}")
        if cmd == "AE":
            v = num(0, 1)
            if v is None:
                return self._reply("ERR AE expected 0 or 1")
            self.ae = bool(v)
            return self._reply(f"OK AE {v}")
        if cmd == "FLL":
            v = num(0, 0xFFFF)
            if v is None:
                return self._reply("ERR FLL expected lines")
            self.frame_length = max(v, self.height + 8)
            self.exposure = min(self.exposure, self.frame_length - 2)
            return self._reply(f"OK FLL {self.frame_length} EXP {self.exposure}")
        if cmd == "LED":
            v = num(0, 1000)
            if v is None:
                return self._reply("ERR LED expected 0-1000")
            self.led = v
            return self._reply(f"OK LED {v}")
        if cmd == "SYNCGEN":
            v = num(0, 1)
            if v is None:
                return self._reply("ERR SYNCGEN expected 0 or 1")
            self.world.set_syncgen(self, bool(v))
            return self._reply(f"OK SYNCGEN {int(self.world.driver is self)}")
        if cmd == "TRIG":
            v = num(0, 1000)
            if v is None:
                return self._reply("ERR TRIG expected 0-1000 Hz")
            self.trig_hz = v
            self.world.set_trigger(self, v, self.clock_ppm)
            return self._reply(f"OK TRIG {v}")
        if cmd == "EXTSYNC":
            v = num(0, 1)
            if v is None:
                return self._reply("ERR EXTSYNC expected 0 or 1")
            if self.streaming:
                return self._reply("ERR EXTSYNC stop first")
            self.extsync = bool(v)
            return self._reply(f"OK EXTSYNC {v}")
        if cmd == "BLINK":
            v = num(0, 10000)
            if v is None:
                return self._reply("ERR BLINK expected 0-10000 Hz")
            self.blink_hz = v
            self.world.set_blink(self, v, self.clock_ppm)
            return self._reply(f"OK BLINK {v}")
        return self._reply(f"ERR {cmd} unknown command")

    def _rng_phase(self) -> float:
        # Free-running cameras start their frames at unrelated moments.
        return float(self._np_rng.uniform(0, self.frame_period()))


class SimTransport:
    """The Camera-facing side of a SimDevice."""

    def __init__(self, world: SimWorld, seed: int = 0) -> None:
        self.device = SimDevice(world, seed)

    def read_some(self) -> bytes:
        return self.device.read_some()

    def write(self, data: bytes) -> None:
        self.device.write(data)

    def close(self) -> None:
        self.device.close()

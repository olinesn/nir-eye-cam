"""Talking to one camera: command/reply over the CDC port, packets on a queue."""

from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass

from . import protocol

PRODUCT = "NIR Eye Cam"
USB_VID, USB_PID = 0x2E8A, 0x000A


class CameraError(RuntimeError):
    pass


class SerialTransport:
    """pyserial wrapped to the three calls Camera uses."""

    def __init__(self, port: str) -> None:
        import serial

        self._s = serial.Serial(port, timeout=0.05, write_timeout=1.0)
        self._s.dtr = True  # the firmware streams only while DTR is asserted
        self._s.reset_input_buffer()

    def read_some(self) -> bytes:
        return self._s.read(self._s.in_waiting or 1)

    def write(self, data: bytes) -> None:
        self._s.write(data)

    def close(self) -> None:
        self._s.close()


@dataclass(frozen=True)
class FoundCamera:
    port: str
    serial_number: str | None
    product: str | None


def discover() -> list[FoundCamera]:
    """Serial ports that look like NIR Eye Cams (by product string, else VID/PID)."""
    from serial.tools import list_ports

    found = []
    for p in list_ports.comports():
        if (p.product or "").startswith(PRODUCT) or (p.vid, p.pid) == (USB_VID, USB_PID):
            found.append(FoundCamera(p.device, p.serial_number, p.product))
    return sorted(found, key=lambda c: c.port)


class Camera:
    """One device. Frames and sync events arrive on ``data`` as (Packet, host_ns)."""

    def __init__(self, transport, name: str = "cam", reply_timeout: float = 3.0) -> None:
        self.name = name
        self.reply_timeout = reply_timeout
        self.data: queue.Queue = queue.Queue()
        self.log: list[str] = []  # text the device sent that answered no command
        self.parser = protocol.StreamParser()
        self.error: BaseException | None = None
        self._t = transport
        self._replies: queue.Queue = queue.Queue()
        self._cmd_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._reader, name=f"read-{name}", daemon=True)
        self._thread.start()

    def _reader(self) -> None:
        try:
            while not self._stop.is_set():
                chunk = self._t.read_some()
                if not chunk:
                    continue
                now = time.monotonic_ns()
                for p in self.parser.feed(chunk):
                    if p.type == protocol.TEXT:
                        self._replies.put(p.text)
                    else:
                        self.data.put((p, now))
        except BaseException as e:  # surfaced to the recorder; the thread just ends
            if not self._stop.is_set():
                self.error = e

    def command(self, line: str, timeout: float | None = None) -> str:
        """Send one command; return the reply after "OK <VERB>", raise on "ERR"."""
        verb = line.split()[0].upper()
        deadline = time.monotonic() + (timeout or self.reply_timeout)
        with self._cmd_lock:
            self._t.write((line + "\n").encode())
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise CameraError(f"{self.name}: no reply to {line!r}")
                if self.error:
                    raise CameraError(f"{self.name}: connection lost: {self.error}")
                try:
                    text = self._replies.get(timeout=min(left, 0.1))
                except queue.Empty:
                    continue
                if text.startswith(f"OK {verb}"):
                    return text[len(verb) + 3 :].strip()
                if text.startswith(f"ERR {verb}") or text.startswith("ERR ?"):
                    raise CameraError(f"{self.name}: {text}")
                self.log.append(text)

    def info(self) -> dict:
        return json.loads(self.command("INFO"))

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)
        self._t.close()


def open_camera(spec: str, name: str, sim_world=None) -> Camera:
    """``spec`` is a serial port, or ``sim`` / ``sim:<seed>`` for a simulated camera."""
    if spec == "sim" or spec.startswith("sim:"):
        from .simulator import SimTransport, SimWorld

        seed = int(spec.split(":", 1)[1]) if ":" in spec else abs(hash(name)) % 10_000
        world = sim_world or SimWorld()
        return Camera(SimTransport(world, seed=seed), name)
    return Camera(SerialTransport(spec), name)

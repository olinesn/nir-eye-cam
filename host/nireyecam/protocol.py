"""Wire format shared with the firmware (firmware/src/protocol.h, docs/protocol.md).

Device -> host is a stream of packets: a 32-byte little-endian header, then
``payload_len`` bytes. Host -> device is newline-terminated ASCII commands.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

MAGIC = b"NEC1"
HEADER = struct.Struct("<4sBBHIIQHHHBB")
HEADER_SIZE = HEADER.size
assert HEADER_SIZE == 32

FRAME = ord("F")
SYNC = ord("S")
TEXT = ord("T")
TYPES = (FRAME, SYNC, TEXT)

FLAG_AE = 0x01

MAX_FRAME_BYTES = 320 * 320
MAX_TEXT_BYTES = 4096


@dataclass(frozen=True)
class Packet:
    type: int
    flags: int
    seq: int
    t_us: int
    width: int = 0
    height: int = 0
    exposure: int = 0
    again: int = 0
    payload: bytes = b""

    @property
    def text(self) -> str:
        return self.payload.decode("utf-8", "replace")

    def image(self):
        """The frame as a (height, width) uint8 numpy array."""
        import numpy as np

        return np.frombuffer(self.payload, dtype=np.uint8).reshape(self.height, self.width)


def pack(
    type: int,
    payload: bytes = b"",
    *,
    flags: int = 0,
    seq: int = 0,
    t_us: int = 0,
    width: int = 0,
    height: int = 0,
    exposure: int = 0,
    again: int = 0,
) -> bytes:
    header = HEADER.pack(
        MAGIC, type, flags, HEADER_SIZE, len(payload), seq, t_us, width, height, exposure, again, 0
    )
    return header + payload


def _plausible(type: int, header_size: int, payload_len: int, width: int, height: int) -> bool:
    if header_size != HEADER_SIZE or type not in TYPES:
        return False
    if type == FRAME:
        return 0 < width <= 320 and 0 < height <= 320 and payload_len == width * height
    if type == SYNC:
        return payload_len == 0
    return payload_len <= MAX_TEXT_BYTES


class StreamParser:
    """Incremental parser. Feed it bytes in any chunking; it returns whole packets.

    Garbage (e.g. bytes left in the USB FIFO from a previous session) is
    skipped by scanning for the next plausible header; ``bytes_skipped``
    counts it.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self._pos = 0
        self.bytes_skipped = 0
        self.resyncs = 0

    def feed(self, data: bytes) -> list[Packet]:
        self._buf += data
        out: list[Packet] = []
        buf = self._buf
        while len(buf) - self._pos >= HEADER_SIZE:
            pos = self._pos
            if buf[pos : pos + 4] != MAGIC:
                self._skip_to_magic(pos + 1)
                continue
            magic, type, flags, hsize, plen, seq, t_us, w, h, exp, again, _ = HEADER.unpack_from(buf, pos)
            if not _plausible(type, hsize, plen, w, h):
                self._skip_to_magic(pos + 1)
                continue
            end = pos + HEADER_SIZE + plen
            if len(buf) < end:
                break
            out.append(Packet(type, flags, seq, t_us, w, h, exp, again, bytes(buf[pos + HEADER_SIZE : end])))
            self._pos = end
        # Compact once the consumed prefix dominates, so appends stay cheap.
        if self._pos and self._pos * 2 >= len(buf):
            del buf[: self._pos]
            self._pos = 0
        return out

    def _skip_to_magic(self, start: int) -> None:
        idx = self._buf.find(MAGIC, start)
        if idx < 0:
            idx = max(start, len(self._buf) - (len(MAGIC) - 1))  # a magic may straddle chunks
        self.bytes_skipped += idx - self._pos
        self.resyncs += 1
        self._pos = idx

import random

import numpy as np

from nireyecam import protocol


def _frame(seq, w=160, h=120, fill=None):
    rng = np.random.default_rng(seq)
    img = rng.integers(0, 256, (h, w), dtype=np.uint8) if fill is None else np.full((h, w), fill, np.uint8)
    return img, protocol.pack(protocol.FRAME, img.tobytes(), seq=seq, t_us=10_000_000 + seq * 33_333,
                              width=w, height=h, exposure=100, again=1)


def test_header_is_32_bytes_and_round_trips():
    img, raw = _frame(7)
    assert len(raw) == 32 + img.size
    (p,) = protocol.StreamParser().feed(raw)
    assert (p.type, p.seq, p.t_us, p.width, p.height, p.exposure, p.again) == (
        protocol.FRAME, 7, 10_000_000 + 7 * 33_333, 160, 120, 100, 1)
    assert np.array_equal(p.image(), img)


def test_any_chunking_gives_the_same_packets():
    stream = b"".join([_frame(i)[1] for i in range(5)]
                      + [protocol.pack(protocol.SYNC, seq=3, t_us=123),
                         protocol.pack(protocol.TEXT, b"OK PING")])
    rng = random.Random(0)
    for _ in range(20):
        parser, got, i = protocol.StreamParser(), [], 0
        while i < len(stream):
            n = rng.randint(1, 5000)
            got += parser.feed(stream[i : i + n])
            i += n
        assert [p.type for p in got] == [protocol.FRAME] * 5 + [protocol.SYNC, protocol.TEXT]
        assert got[-1].text == "OK PING" and got[-2].t_us == 123
        assert parser.bytes_skipped == 0


def test_garbage_and_false_magic_are_skipped():
    # Leftover bytes, a magic with an impossible header, then real packets.
    bad_header = protocol.MAGIC + bytes(40)
    stream = b"\x00junk" + bad_header + _frame(1)[1] + b"NEC" + _frame(2)[1]
    got = protocol.StreamParser().feed(stream)
    assert [p.seq for p in got] == [1, 2]


def test_magic_inside_a_payload_does_not_split_the_frame():
    img = np.zeros((120, 160), np.uint8)
    img.flat[100:104] = list(protocol.MAGIC)
    raw = protocol.pack(protocol.FRAME, img.tobytes(), seq=1, width=160, height=120)
    (p,) = protocol.StreamParser().feed(raw + _frame(2)[1])[:1]
    assert np.array_equal(p.image(), img)

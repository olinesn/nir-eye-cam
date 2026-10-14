// Wire format between the camera and the host, shared by both firmwares
// (firmware/ for the RP2040, firmware-p4/ for the ESP32-P4). Mirrored by
// host/nireyecam/protocol.py and documented in docs/protocol.md -- change all
// three together.
//
// Device -> host: a stream of packets, each a fixed 32-byte little-endian
// header followed by `payload_len` bytes.
// Host -> device: newline-terminated ASCII commands (see main.c).

#pragma once

#include <stdint.h>

#define NEC_MAGIC0 'N'
#define NEC_MAGIC1 'E'
#define NEC_MAGIC2 'C'
#define NEC_MAGIC3 '1'

#define NEC_PKT_FRAME 'F'  // payload: width*height 8-bit pixels, row-major
#define NEC_PKT_SYNC  'S'  // no payload; seq = sync edge count, t_us = edge time
#define NEC_PKT_TEXT  'T'  // payload: UTF-8 text (command replies, warnings)

#define NEC_FLAG_AE   0x01 // frame: auto exposure was on, exposure field is the last set value

#define NEC_HEADER_SIZE 32

typedef struct __attribute__((packed)) {
    uint8_t  magic[4];      // "NEC1"
    uint8_t  type;          // NEC_PKT_*
    uint8_t  flags;         // NEC_FLAG_*
    uint16_t header_size;   // always NEC_HEADER_SIZE
    uint32_t payload_len;   // bytes after the header
    uint32_t seq;           // frame: VSYNC count since START (gaps = dropped frames)
                            // sync: rising-edge count since boot; text: reply count
    uint64_t t_us;          // device clock (time_us_64) at frame start / sync edge
    uint16_t width;         // frame only
    uint16_t height;        // frame only
    uint16_t exposure;      // frame only: integration time in sensor lines
    uint8_t  again;         // frame only: analog gain index (0=1x, 1=2x, 2=4x, 3=8x)
    uint8_t  reserved;
} nec_header_t;

_Static_assert(sizeof(nec_header_t) == NEC_HEADER_SIZE, "header must be 32 bytes");

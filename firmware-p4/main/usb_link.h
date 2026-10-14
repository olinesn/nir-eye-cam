// USB CDC serial on the ESP32-P4's high-speed port, via esp_tinyusb.

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

typedef void (*usb_rx_cb_t)(const uint8_t* data, size_t len);

esp_err_t usb_link_init(usb_rx_cb_t on_rx);

// True while the host holds DTR (pyserial asserts it on open).
bool usb_link_connected(void);

// Queue up to `len` bytes; returns how many were accepted (0 if the FIFO is full).
size_t usb_link_write(const void* data, size_t len);
void usb_link_flush(void);

#include "usb_link.h"

#include "esp_check.h"
#include "tinyusb.h"
#include "tinyusb_cdc_acm.h"
#include "tinyusb_default_config.h"

static const char* TAG = "usb";

static usb_rx_cb_t s_on_rx;
static volatile bool s_connected;

static void on_rx(int itf, cdcacm_event_t* event)
{
    uint8_t buf[CONFIG_TINYUSB_CDC_RX_BUFSIZE];
    size_t n = 0;
    while (tinyusb_cdcacm_read(itf, buf, sizeof(buf), &n) == ESP_OK && n > 0) {
        if (s_on_rx) s_on_rx(buf, n);
    }
}

static void on_line_state(int itf, cdcacm_event_t* event)
{
    s_connected = event->line_state_changed_data.dtr;
}

esp_err_t usb_link_init(usb_rx_cb_t cb)
{
    s_on_rx = cb;
    // Default config: the high-speed port on the ESP32-P4, descriptors from
    // Kconfig (product string "NIR Eye Cam", see sdkconfig.defaults).
    const tinyusb_config_t cfg = TINYUSB_DEFAULT_CONFIG();
    ESP_RETURN_ON_ERROR(tinyusb_driver_install(&cfg), TAG, "driver");

    tinyusb_config_cdcacm_t acm = {
        .cdc_port = TINYUSB_CDC_ACM_0,
        .callback_rx = on_rx,
        .callback_line_state_changed = on_line_state,
    };
    return tinyusb_cdcacm_init(&acm);
}

bool usb_link_connected(void) { return s_connected; }

size_t usb_link_write(const void* data, size_t len)
{
    return tinyusb_cdcacm_write_queue(TINYUSB_CDC_ACM_0, data, len);
}

void usb_link_flush(void)
{
    tinyusb_cdcacm_write_flush(TINYUSB_CDC_ACM_0, 0);
}

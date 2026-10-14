#include "capture.h"

#include <string.h>

#include "driver/isp.h"
#include "esp_attr.h"
#include "esp_cam_ctlr.h"
#include "esp_cam_ctlr_csi.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_ldo_regulator.h"
#include "esp_log.h"
#include "sdkconfig.h"
#include "esp_timer.h"
#include "freertos/queue.h"

#include "board.h"

static const char* TAG = "capture";

#define POOL_BYTES (8u * 1024u * 1024u)  // of the P4's 32 MB PSRAM
#define MAX_SLOTS  64

typedef struct {
    uint32_t seq;
} slot_meta_t;

typedef struct {
    cap_frame_t f;
    int slot;
} filled_t;

static esp_ldo_channel_handle_t s_ldo;
static esp_cam_ctlr_handle_t s_cam;
static isp_proc_handle_t s_isp;
static uint8_t* s_slot_buf[MAX_SLOTS];
static slot_meta_t s_meta[MAX_SLOTS];
static uint32_t s_nslots, s_slot_bytes, s_frame_bytes;
static uint16_t s_w, s_h;
static QueueHandle_t s_free;    // int slot
static QueueHandle_t s_filled;  // filled_t
static volatile uint32_t s_seq, s_errors, s_fsync_count;
static volatile int64_t s_fsync_t;
static volatile uint32_t s_fsync_at_arm[MAX_SLOTS];

// Runs inside the ISR callbacks, so it must be in IRAM too.
static int IRAM_ATTR slot_of(const void* buf)
{
    for (uint32_t i = 0; i < s_nslots; i++) {
        if (s_slot_buf[i] == buf) return (int)i;
    }
    return -1;
}

// Called (ISR) at a frame boundary to get the buffer for the NEXT frame. With
// no free slot it returns no buffer, the driver falls back to its internal
// backup buffer, and that frame is dropped without an on_trans_finished.
static bool IRAM_ATTR on_get_new_trans(esp_cam_ctlr_handle_t h, esp_cam_ctlr_trans_t* trans, void* user)
{
    BaseType_t woken = pdFALSE;
    int slot;
    s_seq++;
    if (xQueueReceiveFromISR(s_free, &slot, &woken) == pdTRUE) {
        trans->buffer = s_slot_buf[slot];
        trans->buflen = s_slot_bytes;
        s_meta[slot].seq = s_seq;
        s_fsync_at_arm[slot] = s_fsync_count;
    }
    return woken == pdTRUE;
}

// Called (ISR) when a frame has landed in one of our slots.
static bool IRAM_ATTR on_trans_finished(esp_cam_ctlr_handle_t h, esp_cam_ctlr_trans_t* trans, void* user)
{
    BaseType_t woken = pdFALSE;
    int slot = slot_of(trans->buffer);
    if (slot < 0) return false;  // the driver's backup buffer: a dropped frame

    filled_t item = {.slot = slot};
    item.f.data = s_slot_buf[slot];
    item.f.len = s_frame_bytes;
    item.f.seq = s_meta[slot].seq;
    item.f.width = s_w;
    item.f.height = s_h;
    // VERIFY on hardware: this assumes FSYNC_OUT marks the start of the frame
    // that has just finished, i.e. one edge arrives per frame and before its
    // DMA completes. Then the latest edge is this frame's.
    if (s_fsync_count != s_fsync_at_arm[slot]) {
        item.f.t_us = s_fsync_t;
        item.f.t_from_fsync = true;
    } else {
        item.f.t_us = esp_timer_get_time();
        item.f.t_from_fsync = false;
    }
    xQueueSendFromISR(s_filled, &item, &woken);
    return woken == pdTRUE;
}

static bool IRAM_ATTR on_error(esp_cam_ctlr_handle_t h, const esp_cam_ctlr_error_event_data_t* e, void* user)
{
    s_errors++;
    return false;
}

void IRAM_ATTR capture_on_fsync(int64_t t_us)
{
    s_fsync_t = t_us;
    s_fsync_count++;
}

esp_err_t capture_init(uint16_t w, uint16_t h, int lanes, int lane_mbps)
{
    ESP_RETURN_ON_FALSE(!s_cam, ESP_ERR_INVALID_STATE, TAG, "already initialised");
    s_w = w;
    s_h = h;
    s_frame_bytes = (uint32_t)w * h;

    if (!s_ldo) {
        esp_ldo_channel_config_t ldo = {.chan_id = MIPI_LDO_CHAN, .voltage_mv = MIPI_LDO_MV};
        ESP_RETURN_ON_ERROR(esp_ldo_acquire_channel(&ldo, &s_ldo), TAG, "MIPI PHY LDO");
    }

    // Slots start and end on cache lines: the driver syncs the cache over
    // each buffer's whole length before and after DMA.
    size_t align = CONFIG_CACHE_L2_CACHE_LINE_SIZE;
    if (align < 64) align = 64;
    s_slot_bytes = (s_frame_bytes + align - 1) / align * align;
    s_nslots = POOL_BYTES / s_slot_bytes;
    if (s_nslots > MAX_SLOTS) s_nslots = MAX_SLOTS;
    ESP_RETURN_ON_FALSE(s_nslots >= 2, ESP_ERR_NO_MEM, TAG, "frame too large for the pool");

    s_free = xQueueCreate(s_nslots, sizeof(int));
    s_filled = xQueueCreate(s_nslots, sizeof(filled_t));
    ESP_RETURN_ON_FALSE(s_free && s_filled, ESP_ERR_NO_MEM, TAG, "queues");
    for (uint32_t i = 0; i < s_nslots; i++) {
        s_slot_buf[i] = heap_caps_aligned_calloc(align, 1, s_slot_bytes,
                                                 MALLOC_CAP_SPIRAM | MALLOC_CAP_DMA | MALLOC_CAP_CACHE_ALIGNED);
        ESP_RETURN_ON_FALSE(s_slot_buf[i], ESP_ERR_NO_MEM, TAG, "slot %lu", (unsigned long)i);
        int slot = (int)i;
        xQueueSend(s_free, &slot, 0);
    }

    esp_cam_ctlr_csi_config_t csi = {
        .ctlr_id = 0,
        .h_res = w,
        .v_res = h,
        .lane_bit_rate_mbps = lane_mbps,
        .input_data_color_type = CAM_CTLR_COLOR_RAW8,
        .output_data_color_type = CAM_CTLR_COLOR_RAW8,
        .data_lane_num = lanes,
        .byte_swap_en = false,
        .queue_items = 1,
    };
    ESP_RETURN_ON_ERROR(esp_cam_new_csi_ctlr(&csi, &s_cam), TAG, "CSI controller");
    esp_cam_ctlr_evt_cbs_t cbs = {
        .on_get_new_trans = on_get_new_trans,
        .on_trans_finished = on_trans_finished,
        .on_error = on_error,
    };
    ESP_RETURN_ON_ERROR(esp_cam_ctlr_register_event_callbacks(s_cam, &cbs, NULL), TAG, "callbacks");
    ESP_RETURN_ON_ERROR(esp_cam_ctlr_enable(s_cam), TAG, "enable");

    esp_isp_processor_cfg_t isp = {
        .clk_hz = 80 * 1000 * 1000,
        .input_data_source = ISP_INPUT_DATA_SOURCE_CSI,
        .input_data_color_type = ISP_COLOR_RAW8,
        .output_data_color_type = ISP_COLOR_RAW8,
        .has_line_start_packet = false,
        .has_line_end_packet = false,
        .h_res = w,
        .v_res = h,
        .flags.bypass_isp = 1,
    };
    ESP_RETURN_ON_ERROR(esp_isp_new_processor(&isp, &s_isp), TAG, "ISP");
    ESP_RETURN_ON_ERROR(esp_isp_enable(s_isp), TAG, "ISP enable");

    ESP_LOGI(TAG, "%ux%u RAW8, %lu slots of %lu B", w, h, (unsigned long)s_nslots, (unsigned long)s_slot_bytes);
    return ESP_OK;
}

esp_err_t capture_deinit(void)
{
    if (!s_cam) return ESP_OK;
    capture_stop();
    esp_isp_disable(s_isp);
    esp_isp_del_processor(s_isp);
    s_isp = NULL;
    esp_cam_ctlr_disable(s_cam);
    esp_cam_ctlr_del(s_cam);
    s_cam = NULL;
    for (uint32_t i = 0; i < s_nslots; i++) {
        heap_caps_free(s_slot_buf[i]);
        s_slot_buf[i] = NULL;
    }
    vQueueDelete(s_free);
    vQueueDelete(s_filled);
    s_free = s_filled = NULL;
    s_nslots = 0;
    return ESP_OK;
}

static bool s_running;

esp_err_t capture_start(void)
{
    ESP_RETURN_ON_FALSE(s_cam, ESP_ERR_INVALID_STATE, TAG, "not initialised");
    if (s_running) return ESP_OK;
    // Frames left from a previous run go back to the pool.
    filled_t item;
    while (xQueueReceive(s_filled, &item, 0) == pdTRUE) xQueueSend(s_free, &item.slot, 0);
    s_seq = 0;
    ESP_RETURN_ON_ERROR(esp_cam_ctlr_start(s_cam), TAG, "start");
    s_running = true;
    return ESP_OK;
}

esp_err_t capture_stop(void)
{
    if (!s_cam || !s_running) return ESP_OK;
    s_running = false;
    return esp_cam_ctlr_stop(s_cam);
}

bool capture_take(cap_frame_t* out, int* slot, TickType_t wait)
{
    filled_t item;
    if (!s_filled || xQueueReceive(s_filled, &item, wait) != pdTRUE) return false;
    *out = item.f;
    *slot = item.slot;
    return true;
}

void capture_release(int slot)
{
    xQueueSend(s_free, &slot, 0);
}

uint32_t capture_slots(void) { return s_nslots; }
uint32_t capture_errors(void) { return s_errors; }

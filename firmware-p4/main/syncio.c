#include "syncio.h"

#include "driver/gpio.h"
#include "driver/ledc.h"
#include "esp_attr.h"
#include "esp_check.h"
#include "esp_clk_tree.h"
#include "esp_timer.h"

#include "board.h"
#include "capture.h"

static const char* TAG = "syncio";

#define SYNC_RING        64u
#define SYNC_DEBOUNCE_US 1000
#define PULSE_US         10000
#define INTERVAL_MIN_US  400000u
#define INTERVAL_SPAN_US 600000u

static sync_event_t s_ring[SYNC_RING];
static volatile uint32_t s_head, s_tail, s_overflows, s_edges;
static int64_t s_last_edge;

static esp_timer_handle_t s_gen_timer;
static volatile bool s_gen_on;
static bool s_gen_high;
static uint32_t s_rng;

static uint32_t s_trig_hz, s_blink_hz;
static bool s_led;

static void IRAM_ATTR fsync_isr(void* arg)
{
    capture_on_fsync(esp_timer_get_time());
}

static void IRAM_ATTR sync_isr(void* arg)
{
    int64_t t = esp_timer_get_time();
    if (s_edges && t - s_last_edge < SYNC_DEBOUNCE_US) return;
    s_last_edge = t;
    s_edges++;
    uint32_t h = s_head;
    if (h - s_tail >= SYNC_RING) {
        s_overflows++;
        return;
    }
    s_ring[h % SYNC_RING] = (sync_event_t){.seq = s_edges, .t_us = t};
    __atomic_store_n(&s_head, h + 1, __ATOMIC_RELEASE);
}

bool sync_take_event(sync_event_t* out)
{
    uint32_t t = s_tail;
    if (t == __atomic_load_n(&s_head, __ATOMIC_ACQUIRE)) return false;
    *out = s_ring[t % SYNC_RING];
    s_tail = t + 1;
    return true;
}

uint32_t sync_edge_count(void) { return s_edges; }
uint32_t sync_overflows(void) { return s_overflows; }

static uint32_t xorshift32(void)
{
    uint32_t x = s_rng;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    return s_rng = x;
}

// esp_timer task context. Its jitter does not matter: what the host uses is
// the SYNC_IN interrupt's timestamp of the edge, on every camera alike.
static void syncgen_cb(void* arg)
{
    if (!s_gen_on) {
        gpio_set_level(PIN_SYNC_OUT, 0);
        s_gen_high = false;
        return;
    }
    if (s_gen_high) {
        gpio_set_level(PIN_SYNC_OUT, 0);
        s_gen_high = false;
        esp_timer_start_once(s_gen_timer, INTERVAL_MIN_US + xorshift32() % INTERVAL_SPAN_US - PULSE_US);
    } else {
        gpio_set_level(PIN_SYNC_OUT, 1);
        s_gen_high = true;
        esp_timer_start_once(s_gen_timer, PULSE_US);
    }
}

void syncgen_set(bool on)
{
    bool was = s_gen_on;
    s_gen_on = on;
    if (on && !was && !esp_timer_is_active(s_gen_timer)) esp_timer_start_once(s_gen_timer, 1000);
}

bool syncgen_on(void) { return s_gen_on; }

// TRIG and BLINK are LEDC PWM outputs, each on its own timer. Both run off the
// 40 MHz crystal. LEDC_AUTO_CLK at a fixed 10-bit resolution picks the RC
// oscillator below ~38 Hz (an inaccurate frame rate, and no output at all below
// ~17 Hz), and the timers share one clock mux, so the second output was refused
// whenever the two frequencies picked different sources. The duty resolution is
// chosen per frequency instead: the fewest bits (>= 8) whose divider fits, which
// keeps the divider large and so the frequency close to exact.
static esp_err_t pwm_set(ledc_timer_t timer, ledc_channel_t channel, int gpio, uint32_t hz,
                         uint32_t permille)
{
    if (hz == 0) return ledc_stop(LEDC_LOW_SPEED_MODE, channel, 0);
    uint32_t src = 0;
    ESP_RETURN_ON_ERROR(esp_clk_tree_src_get_freq_hz(SOC_MOD_CLK_XTAL, ESP_CLK_TREE_SRC_FREQ_PRECISION_CACHED, &src),
                        TAG, "crystal frequency");
    int bits = 0;
    for (int b = 8; b < LEDC_TIMER_BIT_MAX; b++) {
        // The divider in 1/256 steps (10.8 fixed point), rounded as the LEDC driver does.
        uint64_t div = (((uint64_t)src << 8) + ((uint64_t)hz << b) / 2) / ((uint64_t)hz << b);
        if (div <= 0x3FFFF) {
            if (div > 0xFF) bits = b;
            break;
        }
    }
    ESP_RETURN_ON_FALSE(bits, ESP_ERR_INVALID_ARG, TAG, "no divider for %lu Hz", (unsigned long)hz);
    ledc_timer_config_t tc = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = (ledc_timer_bit_t)bits,
        .timer_num = timer,
        .freq_hz = hz,
        .clk_cfg = LEDC_USE_XTAL_CLK,
    };
    ESP_RETURN_ON_ERROR(ledc_timer_config(&tc), TAG, "timer %d at %lu Hz", (int)timer, (unsigned long)hz);
    ledc_channel_config_t ch = {
        .gpio_num = gpio,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel = channel,
        .timer_sel = timer,
        .duty = (uint32_t)(((1ull << bits) * permille + 500) / 1000),
        .hpoint = 0,
    };
    return ledc_channel_config(&ch);
}

esp_err_t trig_set(uint32_t hz)
{
    // ~5% high: a short pulse at each frame start.
    esp_err_t err = pwm_set(LEDC_TIMER_0, LEDC_CHANNEL_0, PIN_TRIG_OUT, hz, 50);
    if (err == ESP_OK) s_trig_hz = hz;
    return err;
}

uint32_t trig_hz(void) { return s_trig_hz; }

esp_err_t blink_set(uint32_t hz)
{
    esp_err_t err = pwm_set(LEDC_TIMER_1, LEDC_CHANNEL_1, PIN_BLINK, hz, 500);
    if (err == ESP_OK) s_blink_hz = hz;
    return err;
}

uint32_t blink_hz(void) { return s_blink_hz; }

void led_enable(bool on)
{
    s_led = on;
    gpio_set_level(PIN_LED_EN, on);
}

bool led_enabled(void) { return s_led; }

esp_err_t syncio_init(uint32_t seed)
{
    s_rng = seed ? seed : 0x9e3779b9u;

    gpio_config_t out = {
        .pin_bit_mask = (1ULL << PIN_SYNC_OUT) | (1ULL << PIN_LED_EN),
        .mode = GPIO_MODE_OUTPUT,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&out), TAG, "outputs");
    gpio_set_level(PIN_SYNC_OUT, 0);
    gpio_set_level(PIN_LED_EN, 0);  // IR off until the host asks

    gpio_config_t in = {
        .pin_bit_mask = (1ULL << PIN_FSYNC) | (1ULL << PIN_SYNC_IN),
        .mode = GPIO_MODE_INPUT,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,  // unconnected reads low, not noise
        .intr_type = GPIO_INTR_POSEDGE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&in), TAG, "inputs");
    ESP_RETURN_ON_ERROR(gpio_install_isr_service(ESP_INTR_FLAG_IRAM), TAG, "isr service");
    ESP_RETURN_ON_ERROR(gpio_isr_handler_add(PIN_FSYNC, fsync_isr, NULL), TAG, "fsync isr");
    ESP_RETURN_ON_ERROR(gpio_isr_handler_add(PIN_SYNC_IN, sync_isr, NULL), TAG, "sync isr");

    esp_timer_create_args_t targs = {.callback = syncgen_cb, .name = "syncgen"};
    return esp_timer_create(&targs, &s_gen_timer);
}

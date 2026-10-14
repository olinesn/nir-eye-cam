#include "hardware/pwm.h"
#include "hardware/sync.h"
#include "pico/stdlib.h"

#include "board_pins.h"
#include "syncio.h"

#define SYNC_RING        64u
#define SYNC_DEBOUNCE_US 1000u
#define PULSE_US         10000u
#define INTERVAL_MIN_US  400000u
#define INTERVAL_SPAN_US 600000u
#define LED_WRAP         999u  // 125 MHz / 1000 = 125 kHz, far faster than any exposure

static sync_event_t s_ring[SYNC_RING];
static volatile uint32_t s_head, s_tail, s_overflows;
static volatile uint32_t s_edges;
static uint64_t s_last_edge_us;

static volatile bool s_gen_on;
static bool s_gen_high;
static uint32_t s_gen_interval;
static uint32_t s_rng;
static alarm_id_t s_alarm = -1;

static uint16_t s_led;

static uint32_t xorshift32(void)
{
    uint32_t x = s_rng;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    return s_rng = x;
}

void sync_on_edge(uint64_t t_us)
{
    if (s_edges && t_us - s_last_edge_us < SYNC_DEBOUNCE_US) return;
    s_last_edge_us = t_us;
    s_edges++;
    uint32_t h = s_head;
    if (h - s_tail >= SYNC_RING) {
        s_overflows++;
        return;
    }
    s_ring[h % SYNC_RING] = (sync_event_t){.seq = s_edges, .t_us = t_us};
    __dmb();
    s_head = h + 1;
}

bool sync_take_event(sync_event_t* out)
{
    uint32_t t = s_tail;
    if (t == s_head) return false;
    *out = s_ring[t % SYNC_RING];
    __dmb();
    s_tail = t + 1;
    return true;
}

uint32_t sync_edge_count(void) { return s_edges; }
uint32_t sync_overflows(void) { return s_overflows; }

// Negative return = reschedule relative to when this alarm was due, so the
// pulse train does not accumulate callback latency.
static int64_t syncgen_alarm(alarm_id_t id, void* user)
{
    (void)id;
    (void)user;
    if (!s_gen_on) {
        gpio_put(NEC_PIN_SYNC_OUT, 0);
        s_gen_high = false;
        s_alarm = -1;
        return 0;
    }
    if (s_gen_high) {
        gpio_put(NEC_PIN_SYNC_OUT, 0);
        s_gen_high = false;
        return -(int64_t)(s_gen_interval - PULSE_US);
    }
    gpio_put(NEC_PIN_SYNC_OUT, 1);
    s_gen_high = true;
    s_gen_interval = INTERVAL_MIN_US + xorshift32() % INTERVAL_SPAN_US;
    return -(int64_t)PULSE_US;
}

void syncgen_set(bool on)
{
    s_gen_on = on;
    if (on && s_alarm < 0) s_alarm = add_alarm_in_us(1000, syncgen_alarm, NULL, true);
}

bool syncgen_on(void) { return s_gen_on; }

void led_set(uint16_t permille)
{
    if (permille > 1000) permille = 1000;
    s_led = permille;
    // level > wrap keeps the output high for the whole period (100%).
    pwm_set_gpio_level(NEC_PIN_LED, permille >= 1000 ? LED_WRAP + 1 : permille);
}

uint16_t led_get(void) { return s_led; }

void syncio_init(uint32_t seed)
{
    s_rng = seed ? seed : 0x9e3779b9u;

    gpio_init(NEC_PIN_SYNC_IN);
    gpio_set_dir(NEC_PIN_SYNC_IN, GPIO_IN);
    gpio_pull_down(NEC_PIN_SYNC_IN);  // an unconnected input reads low, not noise

    gpio_init(NEC_PIN_SYNC_OUT);
    gpio_set_dir(NEC_PIN_SYNC_OUT, GPIO_OUT);
    gpio_put(NEC_PIN_SYNC_OUT, 0);

    gpio_set_function(NEC_PIN_LED, GPIO_FUNC_PWM);
    uint slice = pwm_gpio_to_slice_num(NEC_PIN_LED);
    pwm_set_wrap(slice, LED_WRAP);
    pwm_set_clkdiv(slice, 1.0f);
    pwm_set_gpio_level(NEC_PIN_LED, 0);  // IR off until the host asks
    pwm_set_enabled(slice, true);
}

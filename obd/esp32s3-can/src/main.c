#include "can_config.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdbool.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

#include "driver/twai.h"
#include "driver/uart.h"
#include "esp_log.h"

#if !SLCAN_ONLY
#include "esp_wifi.h"
#include "esp_event.h"
#include "nvs_flash.h"
#include "lwip/sockets.h"
#endif

static const char *TAG = "slcan";

/* SLCAN wire format (Lawicel), the de-facto standard CANable/ESP32 bridges use.
 *   Sniffed frame : 't' + 3 id bytes + dlc + data + 0x0D    (11-bit, 500k default)
 *   Extended      : 'T' + 8 id bytes + dlc + n + data + 0x0D
 *   Set bitrate   : 'S' + N   (N=6 -> 500k, 7 -> 800k, 8 -> 1M, 5 -> 250k)
 *   Open          : 'O' ; Close: 'C'
 *   Listen-only   : 'L' ; Active: 'A'  (non-standard but widely understood)
 *   Timestamp     : 'Z' + N
 *   Status        : 'F' (status flags), 'V'/'v' (version)
 */
#define CR 0x0D

static twai_timing_config_t timing_cfg;
static bool bus_open = false;
static bool listen_only = (CAN_LISTEN_ONLY != 0);
static QueueHandle_t rx_queue;          /* twai_message_t from the ISR */
static uart_port_t uart = SLCAN_UART_PORT;

#if !SLCAN_ONLY
static int tcp_clients[4] = {-1, -1, -1, -1};
#endif

/* ------------------------------------------------------------------ CAN --- */

static uint32_t bitrate_from_slcan(char c)
{
    switch (c) {
        case '0': return 10000;
        case '1': return 20000;
        case '2': return 50000;
        case '3': return 100000;
        case '4': return 125000;
        case '5': return 250000;
        case '6': return 500000;     /* OBD-II / this Altima */
        case '7': return 800000;
        case '8': return 1000000;
        default:  return 0;
    }
}

static twai_timing_config_t timing_for(uint32_t bps)
{
    switch (bps) {
        case 10000:   return (twai_timing_config_t)TWAI_TIMING_CONFIG_10KBITS();
        case 20000:   return (twai_timing_config_t)TWAI_TIMING_CONFIG_20KBITS();
        case 50000:   return (twai_timing_config_t)TWAI_TIMING_CONFIG_50KBITS();
        case 100000:  return (twai_timing_config_t)TWAI_TIMING_CONFIG_100KBITS();
        case 125000:  return (twai_timing_config_t)TWAI_TIMING_CONFIG_125KBITS();
        case 250000:  return (twai_timing_config_t)TWAI_TIMING_CONFIG_250KBITS();
        case 800000:  return (twai_timing_config_t)TWAI_TIMING_CONFIG_800KBITS();
        case 1000000: return (twai_timing_config_t)TWAI_TIMING_CONFIG_1MBITS();
        default:      return (twai_timing_config_t)TWAI_TIMING_CONFIG_500KBITS();
    }
}

static void emit(const char *buf, int len);

static bool can_start(uint32_t bps, bool passive)
{
    if (bus_open) {
        twai_stop();
        twai_driver_uninstall();
        bus_open = false;
    }

    timing_cfg = timing_for(bps);
    twai_general_config_t gen = TWAI_GENERAL_CONFIG_DEFAULT(
        (gpio_num_t)CAN_TX_GPIO, (gpio_num_t)CAN_RX_GPIO,
        passive ? TWAI_MODE_LISTEN_ONLY : TWAI_MODE_NORMAL);
    gen.rx_queue_len = 64;
    gen.tx_queue_len = 8;
    twai_filter_config_t filt = TWAI_FILTER_CONFIG_ACCEPT_ALL();

    if (twai_driver_install(&gen, &timing_cfg, &filt) != ESP_OK) {
        ESP_LOGE(TAG, "twai_driver_install failed");
        return false;
    }
    if (twai_start() != ESP_OK) {
        ESP_LOGE(TAG, "twai_start failed");
        twai_driver_uninstall();
        return false;
    }
    bus_open = true;
    ESP_LOGI(TAG, "CAN open @%lu bps %s", (unsigned long)bps,
             passive ? "LISTEN-ONLY" : "ACTIVE");
    return true;
}

/* TWAI receive -> SLCAN frames out to every transport. */
static void can_rx_task(void *arg)
{
    twai_message_t msg;
    char line[40];
    for (;;) {
        if (xQueueReceive(rx_queue, &msg, portMAX_DELAY) != pdTRUE) continue;
        if (msg.rtr) continue;                    /* no RTR for now */

        int n = 0;
        if (msg.extd) {
            line[n++] = 'T';
            line[n++] = "0123456789ABCDEF"[(msg.identifier >> 28) & 0xF];
            line[n++] = "0123456789ABCDEF"[(msg.identifier >> 24) & 0xF];
            line[n++] = "0123456789ABCDEF"[(msg.identifier >> 20) & 0xF];
            line[n++] = "0123456789ABCDEF"[(msg.identifier >> 16) & 0xF];
            line[n++] = "0123456789ABCDEF"[(msg.identifier >> 12) & 0xF];
            line[n++] = "0123456789ABCDEF"[(msg.identifier >> 8) & 0xF];
            line[n++] = "0123456789ABCDEF"[(msg.identifier >> 4) & 0xF];
            line[n++] = "0123456789ABCDEF"[msg.identifier & 0xF];
        } else {
            line[n++] = 't';
            line[n++] = "0123456789ABCDEF"[(msg.identifier >> 8) & 0xF];
            line[n++] = "0123456789ABCDEF"[(msg.identifier >> 4) & 0xF];
            line[n++] = "0123456789ABCDEF"[msg.identifier & 0xF];
        }
        line[n++] = "0123456789ABCDEF"[msg.data_length_code & 0xF];
        for (int i = 0; i < msg.data_length_code && i < 8; i++) {
            line[n++] = "0123456789ABCDEF"[msg.data[i] >> 4];
            line[n++] = "0123456789ABCDEF"[msg.data[i] & 0xF];
        }
        line[n++] = CR;
        emit(line, n);
    }
}

/* --------------------------------------------------------------- output --- */

static void emit(const char *buf, int len)
{
    uart_write_bytes(uart, buf, len);
#if !SLCAN_ONLY
    for (int i = 0; i < 4; i++) {
        if (tcp_clients[i] >= 0) {
            if (send(tcp_clients[i], buf, len, MSG_DONTWAIT) < 0) {
                close(tcp_clients[i]);
                tcp_clients[i] = -1;
            }
        }
    }
#endif
}

/* ---------------------------------------------------------------- input --- */

/* Parse one SLCAN command line from the serial (or TCP) channel. */
static void slcan_command(const char *s, int len)
{
    if (len < 1) return;
    char c = s[0];
    char reply[8];

    switch (c) {
        case 'S': {
            uint32_t bps = (len > 1) ? bitrate_from_slcan(s[1]) : 0;
            if (!bps) { uart_write_bytes(uart, "\a", 1); return; }
            ESP_LOGI(TAG, "set bitrate %lu", (unsigned long)bps);
            timing_cfg = timing_for(bps);
            uart_write_bytes(uart, "\r", 1);
            break;
        }
        case 'O': if (can_start(CAN_BITRATE, listen_only)) uart_write_bytes(uart, "\r", 1);
                  else uart_write_bytes(uart, "\a", 1); break;
        case 'C': if (bus_open) { twai_stop(); twai_driver_uninstall(); bus_open = false; }
                  uart_write_bytes(uart, "\r", 1); break;
        case 'L': listen_only = true;  uart_write_bytes(uart, "\r", 1); break;
        case 'A': listen_only = false; uart_write_bytes(uart, "\r", 1); break;
        case 'V': uart_write_bytes(uart, "V1013\r", 6); break;   /* version */
        case 'v': uart_write_bytes(uart, "v1013\r", 6); break;
        case 'F': uart_write_bytes(uart, "F00\r", 4); break;     /* status: ok */
        case 'Z': uart_write_bytes(uart, "\r", 1); break;        /* timestamp */
        case 'N': uart_write_bytes(uart, "\r", 1); break;        /* serial no */
        case 't': case 'T': {
            if (!bus_open || listen_only) { uart_write_bytes(uart, "\a", 1); return; }
            twai_message_t m = {0};
            int p = 1;
            uint32_t id = 0;
            if (c == 't') {
                for (int i = 0; i < 3 && p < len; i++, p++) id = (id << 4) | strtol((char[]){s[p],0}, NULL, 16);
            } else {
                for (int i = 0; i < 8 && p < len; i++, p++) id = (id << 4) | strtol((char[]){s[p],0}, NULL, 16);
                m.extd = 1;
            }
            m.identifier = id;
            if (p < len) {
                m.data_length_code = strtol((char[]){s[p],0}, NULL, 16);
                p++;
                for (int i = 0; i < m.data_length_code && i < 8 && p + 1 < len; i++, p += 2) {
                    char b[3] = {s[p], s[p+1], 0};
                    m.data[i] = strtol(b, NULL, 16);
                }
            }
            if (twai_transmit(&m, pdMS_TO_TICKS(10)) == ESP_OK) uart_write_bytes(uart, "\r", 1);
            else uart_write_bytes(uart, "\a", 1);
            break;
        }
        case 'r': case 'R': uart_write_bytes(uart, "\r", 1); break;   /* RTR: ack, unsupported */
        default:  uart_write_bytes(uart, "\a", 1); break;
    }
    (void)reply;
}

static void serial_rx_task(void *arg)
{
    uint8_t buf[128];
    char line[64];
    int n = 0;
    for (;;) {
        int r = uart_read_bytes(uart, buf, sizeof(buf), pdMS_TO_TICKS(20));
        for (int i = 0; i < r; i++) {
            char ch = (char)buf[i];
            if (ch == '\r' || ch == '\n') {
                if (n > 0) { line[n] = 0; slcan_command(line, n); n = 0; }
            } else if (n < (int)sizeof(line) - 1) {
                line[n++] = ch;
            }
        }
    }
}

#if !SLCAN_ONLY
/* --------------------------------------------------------- WiFi bridge --- */

static void tcp_task(void *arg)
{
    int srv = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in a = {0};
    a.sin_family = AF_INET;
    a.sin_addr.s_addr = htonl(INADDR_ANY);
    a.sin_port = htons(SLCAN_TCP_PORT);
    int one = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    bind(srv, (struct sockaddr *)&a, sizeof(a));
    listen(srv, 4);
    ESP_LOGI(TAG, "SLCAN TCP listening on %d", SLCAN_TCP_PORT);

    for (;;) {
        int c = accept(srv, NULL, NULL);
        if (c < 0) { vTaskDelay(pdMS_TO_TICKS(100)); continue; }
        int slot = -1;
        for (int i = 0; i < 4; i++) if (tcp_clients[i] < 0) { slot = i; break; }
        if (slot < 0) { close(c); continue; }
        tcp_clients[slot] = c;
        ESP_LOGI(TAG, "client %d connected", slot);
    }
}

static void wifi_task(void *arg)
{
    esp_netif_init();
    esp_event_loop_create_default();
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t ic = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&ic);

    wifi_config_t wc = {0};
    strncpy((char *)wc.sta.ssid, WIFI_SSID, sizeof(wc.sta.ssid) - 1);
    strncpy((char *)wc.sta.password, WIFI_PASS, sizeof(wc.sta.password) - 1);
    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_set_config(WIFI_IF_STA, &wc);
    esp_wifi_start();
    esp_wifi_connect();
    ESP_LOGI(TAG, "connecting to %s", WIFI_SSID);
    vTaskDelete(NULL);
}
#endif

/* ------------------------------------------------------------------ app --- */

void app_main(void)
{
    uart_config_t uc = {
        .baud_rate = 115200,
        .data_bits = UART_DATA_8_BITS,
        .parity    = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    uart_driver_install(uart, SLCAN_RX_BUF, SLCAN_TX_BUF, 0, NULL, 0);
    uart_param_config(uart, &uc);

    rx_queue = xQueueCreate(64, sizeof(twai_message_t));

    ESP_LOGI(TAG, "ESP32-S3 SLCAN bridge  TX=GPIO%d RX=GPIO%d",
             CAN_TX_GPIO, CAN_RX_GPIO);

#if !SLCAN_ONLY
    nvs_flash_init();
    xTaskCreate(wifi_task, "wifi", 4096, NULL, 5, NULL);
    xTaskCreate(tcp_task, "tcp", 4096, NULL, 5, NULL);
#endif

    xTaskCreate(can_rx_task, "can_rx", 4096, NULL, 10, NULL);
    xTaskCreate(serial_rx_task, "ser_rx", 4096, NULL, 10, NULL);

    /* Auto-open the bus so the device is useful the moment it powers up. */
    can_start(CAN_BITRATE, listen_only);

    /* Publish received frames into the queue via the TWAI alert path. */
    for (;;) {
        twai_message_t m;
        if (twai_receive(&m, pdMS_TO_TICKS(50)) == ESP_OK) {
            xQueueSend(rx_queue, &m, 0);
        }
    }
}

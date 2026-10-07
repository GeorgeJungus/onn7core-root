/* CAN / SLCAN configuration for the ESP32-S3 OBD tap.
 *
 * Two ways to reach the bus, chosen at build time:
 *   TWAI_MODE_LISTEN_ONLY - passive. Never ACKs, never transmits. Safe to leave
 *                           connected permanently; cannot disturb the bus.
 *   TWAI_MODE_NORMAL      - active. ACKs frames and can transmit OBD requests.
 *                           Required for PID probing; must NOT be used on a bus
 *                           where nothing else is alive (no ACK source).
 */
#pragma once

#include "driver/twai.h"

/* --- wiring ------------------------------------------------------------- */
#ifndef CAN_TX_GPIO
#define CAN_TX_GPIO 4        /* ESP32-S3 -> transceiver TXD (CTX) */
#endif
#ifndef CAN_RX_GPIO
#define CAN_RX_GPIO 5        /* transceiver RXD (CRX) -> ESP32-S3 */
#endif

/* --- bitrate ------------------------------------------------------------
 * ISO 15765-4 (OBD-II) mandates 500 kbit/s on the 6/14 pins for this era.
 * Some body/comfort networks run 125 or 250 kbit/s - change here to sniff those.
 */
#define CAN_BITRATE 500000

/* --- bus mode -----------------------------------------------------------
 * Start in LISTEN_ONLY: safe, non-intrusive, and sufficient to log every frame.
 * Set to 0 to enable ACTIVE mode for OBD PID probing (the firmware can also
 * toggle this at runtime over the serial command channel).
 */
#ifndef CAN_LISTEN_ONLY
#define CAN_LISTEN_ONLY 1
#endif

/* --- WiFi (omit the whole path with -DSLCAN_ONLY=1) --------------------- */
#ifndef SLCAN_ONLY
#define SLCAN_ONLY 0
#endif

#ifndef WIFI_SSID
#define WIFI_SSID "CHANGE_ME"
#endif
#ifndef WIFI_PASS
#define WIFI_PASS "CHANGE_ME"
#endif

/* TCP port exposing the SLCAN stream over WiFi. */
#define SLCAN_TCP_PORT 33333

/* --- SLCAN serial link -------------------------------------------------- */
#define SLCAN_UART_PORT   UART_NUM_0   /* USB-Serial-JTAG or UART0 console */
#define SLCAN_RX_BUF      512
#define SLCAN_TX_BUF      4096

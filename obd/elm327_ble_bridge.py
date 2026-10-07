#!/usr/bin/env python3
"""BLE-ELM327 -> PTY bridge, so the Veepeak can appear as a SocketCAN interface.

WHY THIS EXISTS
---------------
The Veepeak VP11 is BLE-only (no SPP), so there is no /dev/rfcommN tty. The
kernel's can327 driver is a LINE DISCIPLINE - it needs a serial tty to attach
to. BLE GATT is not a tty. This bridge fills the gap:

    Veepeak (BLE GATT fff0/fff1/fff2)  <->  PTY  ->  ldattach can327  ->  can0

HOW IT WORKS
------------
- Opens a BLE GATT connection to the Veepeak.
- Creates a PTY pair; slcan/can327 open the SLAVE side as if it were a serial
  adapter at 38400 8N1.
- Bytes written by can327 to the PTY are forwarded to the GATT write char.
- GATT notifications are forwarded back into the PTY as if the ELM327 had
  spoken on a serial line.

EXPECTED THROUGHPUT LIMIT (measured, not theoretical)
-----------------------------------------------------
ATMA on this adapter sustains ~1.4 frames/s and hits "BUFFER FULL", which
aborts monitoring. BLE connection-interval batching is the bottleneck, not the
ELM327's 38400-baud UART. This bridge does not fix that - it only makes the
adapter usable as a CAN interface. For full-rate capture use the ESP32-S3
(SLCAN over USB at 921600 baud, or WiFi).

USAGE
-----
    sudo python3 elm327_ble_bridge.py
    # it prints the PTY, e.g. /dev/pts/5, then in another shell:
    sudo ldattach --debug --speed 38400 --eightbits --noparity --onestopbit \
         --iflag -ICRNL,INLCR,-IXOFF 30 /dev/pts/5
    sudo ip link set can0 type can bitrate 500000
    sudo ip link set can0 up
    candump can0
"""
import asyncio
import os
import pty
import sys
import termios

from bleak import BleakScanner, BleakClient

MAC = os.environ.get("VEEPEAK_MAC", "AA:BB:CC:DD:EE:FF")
SVC = "0000fff0-0000-1000-8000-00805f9b34fb"
RX_CH = "0000fff1-0000-1000-8000-00805f9b34fb"   # notify: device -> host
TX_CH = "0000fff2-0000-1000-8000-00805f9b34fb"   # write : host -> device

# BLE writes must be chunked; 20 bytes is the safe default ATT MTU payload.
CHUNK = 20


class Bridge:
    def __init__(self):
        self.master, self.slave = pty.openpty()
        # Raw mode on the slave: can327 does its own framing.
        attrs = termios.tcgetattr(self.slave)
        attrs[0] &= ~(termios.ICRNL | termios.INLCR | termios.IXON | termios.IXOFF)
        attrs[1] &= ~termios.OPOST
        attrs[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG | termios.IEXTEN)
        attrs[6][termios.VMIN] = 1
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(self.slave, termios.TCSANOW, attrs)
        os.set_blocking(self.master, False)
        os.set_blocking(self.slave, False)

    async def run(self):
        # Connect WITHOUT scanning first, then hand the client straight to
        # `async with`.
        #
        # Two pitfalls this avoids:
        #  1. Scanning fails while something already holds the BLE link - the
        #     Veepeak stops advertising the moment it is connected, and bleak's
        #     find_device_by_address only sees advertising devices.
        #  2. Do NOT call client.connect() and then also use `async with client`
        #     - the context manager connects, so the second connect raises
        #     "Client is already connected". That exception used to escape into
        #     the reconnect loop, which then re-scanned while the half-open link
        #     suppressed advertising: a self-inflicted failure loop.
        client = None
        try:
            client = BleakClient(MAC, timeout=30)
            await client.connect()
            if not client.is_connected:
                raise RuntimeError("connect() returned but link is down")
        except Exception as e:
            print(f"  direct connect failed ({e}); scanning instead", file=sys.stderr)
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            dev = await BleakScanner.find_device_by_address(MAC, timeout=30)
            if not dev:
                print("VEEPEAK not found. Is it powered (OBD port / 12V)?",
                      file=sys.stderr)
                sys.exit(1)
            client = BleakClient(dev, timeout=30)
            await client.connect()

        print(f"connected to VEEPEAK ({MAC}) ...", flush=True)
        try:
            c = client
            svc = c.services.get_service(SVC)
            rx = svc.get_characteristic(RX_CH)
            tx = svc.get_characteristic(TX_CH)

            def on_notify(_ch, data: bytearray):
                try:
                    os.write(self.master, bytes(data))
                except OSError:
                    pass

            await c.start_notify(rx, on_notify)
            print(f"  serial PTY = {os.ttyname(self.slave)}", flush=True)
            print(f"  attach with:  sudo ldattach --speed 38400 "
                  f"--eightbits --noparity --onestopbit "
                  f"--iflag -ICRNL,INLCR,-IXOFF 30 {os.ttyname(self.slave)}",
                  flush=True)

            loop = asyncio.get_running_loop()
            while c.is_connected:
                # PTY -> BLE (commands can327 sends to the "adapter")
                try:
                    data = await loop.run_in_executor(None, self._read_master)
                except OSError:
                    await asyncio.sleep(0.05)
                    continue
                if data:
                    for i in range(0, len(data), CHUNK):
                        try:
                            await c.write_gatt_char(tx, data[i:i + CHUNK],
                                                    response=False)
                        except Exception as e:
                            print(f"  BLE write failed: {e}", file=sys.stderr)
                            await asyncio.sleep(0.2)
                else:
                    await asyncio.sleep(0.02)
            print("  BLE link dropped", file=sys.stderr)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    def _read_master(self):
        """Blocking read with a short select timeout, so the loop can breathe."""
        import select
        r, _, _ = select.select([self.master], [], [], 0.1)
        if not r:
            return b""
        try:
            return os.read(self.master, 4096)
        except BlockingIOError:
            return b""


async def main():
    b = Bridge()
    while True:
        try:
            await b.run()
        except KeyboardInterrupt:
            print("\nstopped")
            return
        except Exception as e:
            print(f"reconnecting after: {e}", file=sys.stderr)
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""ATMA -> virtual CAN interface bridge, for LIVE viewing in CANgaroo/SavvyCAN.

WHY THIS INSTEAD OF can327
--------------------------
The kernel's can327 driver is built for a *wired* ELM327 on a real tty. On this
BLE Veepeak it attaches and creates can0, but never completes its AT-init
handshake, so zero frames ever flow (verified: "can0: can327 on pts2" with
0 bytes TX/RX). BLE GATT's connection-interval batching cannot satisfy the
driver's prompt timing.

This script does the same job without the kernel driver:

    1. Opens a BLE GATT link to the Veepeak.
    2. Sends ATMA (Monitor All) so the adapter streams every CAN frame.
    3. Parses each line  "7E8 8 03 41 0C 1A F8 ..."  -> (id, dlc, data).
    4. Injects the frames into a VIRTUAL CAN interface (vcan0) with python-can.

CANgaroo and SavvyCAN both open vcan0 like any other SocketCAN device, so you
get real live views. Frames are real frames from the car - the vcan hop is just
the transport between this script and the GUI.

Throughput caveat (measured): ATMA on this adapter sustains ~1.4 frames/s and
aborts with BUFFER FULL. That is a property of the BLE link, not of this script
- expect a thin trickle, not a full bus capture.

Usage:
    sudo python3 atma_to_vcan.py            # defaults: vcan0
    sudo python3 atma_to_vcan.py vcan1
Then in CANgaroo:  Connection -> SocketCAN -> vcan0
"""
import asyncio
import os
import re
import sys
import time

import can
from bleak import BleakClient, BleakScanner

MAC = os.environ.get("VEEPEAK_MAC", "AA:BB:CC:DD:EE:FF")
SVC = "0000fff0-0000-1000-8000-00805f9b34fb"
RX_CH = "0000fff1-0000-1000-8000-00805f9b34fb"   # notify
TX_CH = "0000fff2-0000-1000-8000-00805f9b34fb"   # write
CHUNK = 20

from atma_parse import parse_line


class Bridge:
    def __init__(self, iface):
        self.iface = iface
        self.bus = can.Bus(channel=iface, interface="socketcan")
        self.count = 0
        self.dropped = 0

    def emit(self, line):
        """Parse one ATMA line and inject it into the virtual CAN interface.

        Uses the validated atma_parse.parse_line, which handles the three real
        quirks seen on this vehicle: glued '>' prompts, absent DLC fields, and
        BUFFER FULL frame-merging.
        """
        r = parse_line(line)
        if r is None:
            return
        cid, data = r
        # Reject the request IDs we ourselves emit, and OBD responses are fine.
        try:
            msg = can.Message(arbitration_id=cid, data=data,
                              is_extended_id=cid > 0x7FF)
            self.bus.send(msg)
            self.count += 1
        except Exception:
            self.dropped += 1

    async def run(self):
        client = None
        try:
            client = BleakClient(MAC, timeout=30)
            await client.connect()
            if not client.is_connected:
                raise RuntimeError("link down after connect()")
        except Exception as e:
            print(f"  direct connect failed ({e}); scanning", file=sys.stderr)
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            dev = await BleakScanner.find_device_by_address(MAC, timeout=30)
            if not dev:
                print("VEEPEAK not found (powered? plugged into OBD?)",
                      file=sys.stderr)
                sys.exit(1)
            client = BleakClient(dev, timeout=30)
            await client.connect()

        print(f"[atma] connected to VEEPEAK; injecting into {self.iface}",
              flush=True)

        buf = bytearray()

        def on_notify(_ch, data: bytearray):
            buf.extend(data)
            while b"\r" in buf:
                raw, _, rest = bytes(buf).partition(b"\r")
                buf.clear(); buf.extend(rest)
                s = raw.decode(errors="replace").strip()
                if not s or s == ">" or s.startswith("SEARCHING"):
                    continue
                if "BUFFER FULL" in s:
                    print("[atma] BUFFER FULL - monitoring aborted; resuming",
                          file=sys.stderr, flush=True)
                    s = s.split("BUFFER FULL")[0].strip()
                    if s:
                        self.emit(s)
                    continue
                if s in ("OK", "STOPPED", "?"):
                    continue
                self.emit(s)

        try:
            svc = client.services.get_service(SVC)
            rx = svc.get_characteristic(RX_CH)
            tx = svc.get_characteristic(TX_CH)
            await client.start_notify(rx, on_notify)

            async def cmd(s, wait=1.0):
                for i in range(0, len(s), CHUNK):
                    await client.write_gatt_char(tx, s[i:i + CHUNK].encode(),
                                                 response=False)
                await asyncio.sleep(wait)

            # --- configure for raw monitoring ---
            await cmd("ATZ\r", 2.5)
            await cmd("ATE0\r", 0.6)
            await cmd("ATL0\r", 0.6)
            await cmd("ATS1\r", 0.6)
            await cmd("ATH1\r", 0.6)
            await cmd("ATSP6\r", 1.5)     # ISO 15765-4 CAN 11bit/500k
            await cmd("ATCAF0\r", 0.8)    # auto formatting off -> raw
            await cmd("ATAT0\r", 0.6)     # adaptive timing off

            print("[atma] sending ATMA (monitor all) ...", flush=True)
            last = time.time()
            # Re-issue ATMA periodically: the adapter aborts on BUFFER FULL and
            # drops back to command mode, so it must be restarted to keep flowing.
            await cmd("ATMA\r", 0.1)
            while client.is_connected:
                await asyncio.sleep(1.0)
                if time.time() - last > 20:
                    await client.write_gatt_char(tx, b"\r", response=False)
                    await asyncio.sleep(0.3)
                    await client.write_gatt_char(tx, b"ATMA\r",
                                                 response=False)
                    last = time.time()
                    print(f"[atma] frames injected: {self.count} "
                          f"(dropped {self.dropped})", flush=True)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass


async def main():
    iface = sys.argv[1] if len(sys.argv) > 1 else "vcan0"
    b = Bridge(iface)
    try:
        await b.run()
    except KeyboardInterrupt:
        print(f"\n[atma] stopped; {b.count} frames injected")
    finally:
        b.bus.shutdown()


if __name__ == "__main__":
    asyncio.run(main())

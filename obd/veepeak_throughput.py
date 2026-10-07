#!/usr/bin/env python3
"""Measure real ATMA (Monitor All) throughput on a live CAN bus.

Decides empirically whether the Veepeak can actually sniff the car, instead of
reasoning from the 38400-baud theory. Counts frames, catches BUFFER FULL, and
records the CAN IDs the vehicle actually broadcasts.

Usage:
    python3 veepeak_throughput.py [seconds]
"""
import asyncio
import re
import sys
import time
from collections import Counter

from bleak import BleakScanner, BleakClient

MAC = "AA:BB:CC:DD:EE:FF"
SVC = "0000fff0-0000-1000-8000-00805f9b34fb"
RX_CH = "0000fff1-0000-1000-8000-00805f9b34fb"
TX_CH = "0000fff2-0000-1000-8000-00805f9b34fb"

DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 20


async def main():
    dev = await BleakScanner.find_device_by_address(MAC, timeout=15)
    if not dev:
        print("VEEPEAK not found"); return

    lines = []
    buf = bytearray()
    events = {"buffer_full": 0, "other_err": 0}
    t0 = None

    async with BleakClient(dev) as c:
        svc = c.services.get_service(SVC)
        rx = svc.get_characteristic(RX_CH)
        tx = svc.get_characteristic(TX_CH)

        def on_notify(_ch, data: bytearray):
            nonlocal t0
            if t0 is None:
                t0 = time.time()
            buf.extend(data)
            while b"\r" in buf:
                line, _, rest = bytes(buf).partition(b"\r")
                buf.clear(); buf.extend(rest)
                s = line.decode(errors="replace").strip()
                if not s:
                    continue
                if "BUFFER FULL" in s:
                    events["buffer_full"] += 1
                    # a frame may be glued to the error on the same line
                    s2 = s.split("BUFFER FULL")[0].strip()
                    if s2:
                        lines.append(s2)
                    continue
                if s in ("OK", ">") or s.startswith("SEARCHING") or s.startswith("STOPPED"):
                    continue
                lines.append(s)

        await c.start_notify(rx, on_notify)

        async def cmd(s, wait=1.0):
            await c.write_gatt_char(tx, s.encode(), response=False)
            await asyncio.sleep(wait)

        # --- set up for raw monitoring ---
        await cmd("ATZ\r", 2.5)
        await cmd("ATE0\r")        # echo off
        await cmd("ATL0\r")        # linefeeds off
        await cmd("ATS1\r")        # spaces on (can327 needs this; helps parsing)
        await cmd("ATH1\r")        # headers ON -> we want the CAN IDs
        await cmd("ATSP6\r", 1.5)  # ISO 15765-4 CAN 11bit/500k (Altima)
        await cmd("ATCAF0\r")      # auto formatting OFF -> raw hex
        await cmd("ATAT0\r")       # adaptive timing off (max throughput)

        print(f"  monitoring for {DURATION}s ... (ATMA)")
        lines.clear()
        t0 = None
        await c.write_gatt_char(tx, b"ATMA\r", response=False)
        await asyncio.sleep(DURATION)
        # stop monitoring
        await c.write_gatt_char(tx, b"\r", response=False)
        await asyncio.sleep(1.0)

        await c.stop_notify(rx)

    elapsed = (time.time() - t0) if t0 else DURATION
    n = len(lines)
    print(f"\n  frames captured : {n}")
    print(f"  elapsed         : {elapsed:.1f}s")
    print(f"  rate            : {n/elapsed:.1f} frames/s")
    print(f"  BUFFER FULL     : {events['buffer_full']}  <- >0 means monitoring aborted")

    # parse CAN IDs from lines like "7E8 8 03 41 0C 1A F8 00 00 00" or raw "7E8 03 41 ..."
    ids = Counter()
    for s in lines:
        m = re.match(r"^([0-9A-Fa-f]{3})\b", s)
        if m:
            ids[m.group(1).upper()] += 1
    if ids:
        print(f"\n  CAN IDs seen ({len(ids)} distinct):")
        for cid, cnt in ids.most_common(25):
            print(f"    0x{cid}  {cnt:6} frames")
    else:
        print("\n  no parsable CAN IDs (bus silent? ignition off?)")

    with open("/tmp/altima_atma_capture.txt", "w") as f:
        f.write("\n".join(lines))
    print(f"\n  raw capture -> /tmp/altima_atma_capture.txt ({n} lines)")


asyncio.run(main())

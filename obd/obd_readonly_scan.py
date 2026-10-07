#!/usr/bin/env python3
"""Read-only OBD-II profile scan for a 2016 Nissan Altima (L33) over BLE.

STRICTLY READ-ONLY. Mode 01/02/03/06/07/09/0A are all queries. This script
never sends mode 04 (clear DTCs) or any write/coding request, and never fuzzes
the proprietary PID range (which would transmit).

Produces: VIN, full supported-PID list by bitmask, DTCs (stored/pending/
permanent), monitor status, calibration IDs, and a live value snapshot.

Usage:  python3 obd_readonly_scan.py
"""
import asyncio
import json
import re
import sys
from bleak import BleakScanner, BleakClient

MAC = "AA:BB:CC:DD:EE:FF"
SVC = "0000fff0-0000-1000-8000-00805f9b34fb"
RX_CH = "0000fff1-0000-1000-8000-00805f9b34fb"
TX_CH = "0000fff2-0000-1000-8000-00805f9b34fb"

# PID names for readable output (SAE J1979 subset)
NAMES = {
    0x01: "monitor status", 0x02: "freeze DTC", 0x03: "fuel system status",
    0x04: "engine load %", 0x05: "coolant temp C", 0x06: "STFT b1 %",
    0x07: "LTFT b1 %", 0x08: "STFT b2 %", 0x09: "LTFT b2 %",
    0x0A: "fuel pressure kPa", 0x0B: "intake MAP kPa", 0x0C: "RPM",
    0x0D: "speed km/h", 0x0E: "timing advance deg", 0x0F: "intake temp C",
    0x10: "MAF g/s", 0x11: "throttle %", 0x12: "secondary air",
    0x13: "O2 sensors present", 0x14: "O2 b1s1 V", 0x15: "O2 b1s2 V",
    0x1C: "OBD compliance", 0x1F: "run time s",
    0x21: "dist w/ MIL km", 0x2C: "commanded EGR %", 0x2D: "EGR error %",
    0x2E: "evap purge %", 0x2F: "fuel level %", 0x30: "warmups",
    0x31: "dist since clear km", 0x33: "barometric kPa",
    0x3C: "cat temp b1s1 C", 0x42: "module volts", 0x43: "abs load %",
    0x45: "rel throttle %", 0x46: "ambient air C", 0x49: "accel D %",
    0x4C: "throttle actuator %", 0x4D: "time w/ MIL min",
    0x4E: "time since clear min", 0x51: "fuel type", 0x5C: "oil temp C",
}


def ids_from_mask(hex4, base):
    out = []
    b = bytes.fromhex(hex4.replace(" ", ""))
    for i in range(32):
        if b[i // 8] & (1 << (7 - (i % 8))):
            out.append(base + 1 + i)
    return out


class Elm:
    def __init__(self, client, tx, rx):
        self.c = client
        self.tx = tx
        self.buf = bytearray()
        self.rx = rx

    def feed(self, data):
        self.buf.extend(data)

    async def cmd(self, s, wait=1.2):
        """Send a command, return the cleaned response text."""
        self.buf.clear()
        await self.c.write_gatt_char(self.tx, (s + "\r").encode(), response=False)
        await asyncio.sleep(wait)
        txt = bytes(self.buf).decode(errors="replace")
        txt = txt.replace("\r", "\n")
        lines = [l.strip() for l in txt.split("\n")]
        out = []
        for l in lines:
            if not l or l == ">" or l.startswith("SEARCHING"):
                continue
            if l in ("OK", "STOPPED", "NO DATA", "?"):
                out.append(l)
                continue
            out.append(l)
        return out


async def main():
    dev = await BleakScanner.find_device_by_address(MAC, timeout=15)
    if not dev:
        print("VEEPEAK not found (asleep?)"); return

    report = {}
    async with BleakClient(dev) as c:
        svc = c.services.get_service(SVC)
        rx = svc.get_characteristic(RX_CH)
        tx = svc.get_characteristic(TX_CH)
        e = Elm(c, tx, rx)

        def nb(_ch, data): e.feed(data)
        await c.start_notify(rx, nb)

        print("=== adapter setup ===")
        for cmd, w in (("ATZ", 2.5), ("ATE0", 0.8), ("ATL0", 0.8),
                       ("ATS0", 0.8), ("ATH0", 0.8), ("ATSP0", 1.5)):
            r = await e.cmd(cmd, w)
            print(f"  {cmd:6} -> {r}")

        print("\n=== ignition / voltage ===")
        print("  ATIGN ->", await e.cmd("ATIGN", 1.0))
        print("  ATRV  ->", await e.cmd("ATRV", 1.0))

        print("\n=== VIN (mode 09 PID 02, multi-frame) ===")
        r = await e.cmd("0902", 3.0)
        print("  raw:", r)
        m = re.search(r"49\s*02\s*01\s*((?:[0-9A-F]{2}\s*){17})", " ".join(r))
        if m:
            vin = bytes.fromhex(m.group(1).replace(" ", "")).decode(errors="replace")
            report["vin"] = vin
            print(f"  VIN = {vin}")
        else:
            print("  (could not parse VIN)")

        print("\n=== supported PIDs (bitmask walk, modes 01) ===")
        allpids = []
        for blk in (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0):
            r = await e.cmd(f"01{blk:02X}", 1.5)
            joined = " ".join(r)
            m = re.search(r"41\s*%02X\s*([0-9A-F ]{11,})" % blk, joined)
            if not m:
                print(f"  block {blk:02X}: no response")
                break
            hx = " ".join(m.group(1).split()[:4])
            pids = ids_from_mask(hx, blk)
            for p in pids:
                if p != blk + 0x20:
                    allpids.append(p)
            print(f"  block {blk:02X}: {len([p for p in pids if p != blk+0x20])} PIDs  [{hx}]")
        report["supported_pids"] = allpids
        print(f"\n  TOTAL supported: {len(allpids)}")
        for p in allpids:
            print(f"    0x{p:02X}  {NAMES.get(p,'(manufacturer/other)')}")

        print("\n=== DTCs (read only - no clearing) ===")
        print("  mode 03 stored   ->", await e.cmd("03", 2.5))
        print("  mode 07 pending  ->", await e.cmd("07", 2.5))
        print("  mode 0A permanent->", await e.cmd("0A", 2.5))

        print("\n=== mode 06 on-board monitors (MID list) ===")
        print("  0600 ->", await e.cmd("0600", 2.5))

        print("\n=== vehicle info (mode 09) ===")
        for pid, lbl in ((0x04, "CALIBRATION_ID"), (0x06, "CVN"),
                         (0x0A, "ECU_NAME")):
            r = await e.cmd(f"09{pid:02X}", 2.5)
            print(f"  09{pid:02X} {lbl:15} -> {r}")
            report[lbl.lower()] = r

        print("\n=== live snapshot ===")
        for p in (0x05, 0x0C, 0x0D, 0x0F, 0x11, 0x2F, 0x33, 0x42, 0x46, 0x5C):
            if p in allpids:
                r = await e.cmd(f"01{p:02X}", 1.2)
                print(f"  0x{p:02X} {NAMES.get(p,''):18} -> {r}")

        await c.stop_notify(rx)

    with open("/tmp/altima_profile.json", "w") as f:
        json.dump(report, f, indent=2)
    print("\n  profile -> /tmp/altima_profile.json")


asyncio.run(main())

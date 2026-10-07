#!/usr/bin/env python3
"""
drive_gps.py - walk the mock 'gps' provider along a route.

This is the SOURCE-AGNOSTIC test harness: it proves the whole chain
(gps provider -> fused -> Maps) works, without needing the hotspot phone.

Later, replace route_points() with a feed from the dedicated car phone
(TCP NMEA / any source). Everything downstream is identical.
"""
import subprocess, sys, time, math

SERIAL = sys.argv[1] if len(sys.argv) > 1 else None
STEPS  = int(sys.argv[2]) if len(sys.argv) > 2 else 60
DWELL  = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0

def adb(*a):
    cmd = ["adb"] + (["-s", SERIAL] if SERIAL else []) + list(a)
    return subprocess.run(cmd, capture_output=True, text=True)

def set_loc(lat, lon, acc=5):
    adb("shell", "cmd", "location", "providers", "set-test-provider-location",
        "gps", "--location", f"{lat},{lon}", "--accuracy", str(acc))

# --- route: a straight 6 km run west along I-235, Des Moines, then back ---
START = (41.5868, -93.6250)
END   = (41.5868, -93.6900)

def route_points(n):
    pts = []
    for i in range(n):
        t = i / max(1, n - 1)
        lat = START[0] + (END[0] - START[0]) * t
        lon = START[1] + (END[1] - START[1]) * t
        pts.append((lat, lon))
    return pts + pts[::-1]   # out and back, endless loop

def main():
    # prerequisites (idempotent)
    adb("shell", "cmd", "location", "set-location-enabled", "true")
    adb("shell", "appops", "set", "2000", "android:mock_location", "allow")
    adb("shell", "cmd", "location", "providers", "add-test-provider", "gps",
        "--requiresSatellite", "--supportsAltitude", "--supportsSpeed",
        "--supportsBearing", "--powerRequirement", "1")
    adb("shell", "cmd", "location", "providers", "set-test-provider-enabled", "gps", "true")

    pts = route_points(STEPS)
    print(f"driving {len(pts)} fixes @ {DWELL}s", flush=True)
    i = 0
    while True:
        lat, lon = pts[i % len(pts)]
        set_loc(lat, lon)
        print(f"[{i:04d}] {lat:.5f},{lon:.5f}", flush=True)
        i += 1
        time.sleep(DWELL)

if __name__ == "__main__":
    main()

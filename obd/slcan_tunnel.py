#!/usr/bin/env python3
"""TCP-SLCAN -> SocketCAN bridge.

The ESP32-S3 can expose its SLCAN stream over TCP so the car's bus is reachable
without a cable (the tablet in the dash, or the workbench over Tailscale).
Kernel `slcand` only talks to a serial device, so it cannot consume a TCP stream.
This bridge fills that gap: it presents the remote SLCAN stream as a *local
serial device* via a PTY, which `slcand` can then attach to normally.

Usage:
    # 1. start the bridge (creates /dev/pts/N, printed to stdout)
    sudo python3 slcan_tunnel.py esp32-can.local:33333

    # 2. attach the kernel driver to the PTY it created
    sudo slcand -o -c -s6 /dev/pts/N slcan0
    sudo ip link set slcan0 up

    # 3. sniff
    candump slcan0
    cansniffer -c slcan0

Why a PTY and not a raw socket: SocketCAN has no "TCP CAN interface" type.
slcand/slcan_attach require a tty. A PTY is the standard, kernel-clean way to
give them one.
"""
import argparse
import os
import pty
import select
import socket
import sys
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="host:port of the ESP32 SLCAN listener")
    ap.add_argument("--baud", type=int, default=115200,
                    help="nominal baud reported to slcand (wire speed is ignored)")
    ap.add_argument("--reconnect", type=float, default=3.0,
                    help="seconds between reconnect attempts")
    a = ap.parse_args()

    host, _, port = a.target.rpartition(":")
    port = int(port)

    # PTY pair: master we drive, slave that slcand opens.
    master, slave = pty.openpty()
    slave_name = os.ttyname(slave)
    # slcand needs a sane line discipline; raw is handled by slcand -o.
    os.set_blocking(master, False)

    print(f"[tunnel] SLCAN PTY ready: {slave_name}", flush=True)
    print(f"[tunnel] now run:  sudo slcand -o -c -s6 {slave_name} slcan0", flush=True)
    print(f"[tunnel] then:     sudo ip link set slcan0 up", flush=True)

    sock = None
    while True:
        if sock is None:
            try:
                sock = socket.create_connection((host, port), timeout=5)
                sock.setblocking(False)
                print(f"[tunnel] connected to {host}:{port}", flush=True)
            except OSError as e:
                print(f"[tunnel] connect failed ({e}); retrying in {a.reconnect}s",
                      flush=True)
                time.sleep(a.reconnect)
                continue

        try:
            r, _, _ = select.select([master, sock], [], [], 0.5)
        except (OSError, ValueError):
            break

        # PTY -> network (commands from slcand: 'O', 'S6', 't...')
        if master in r:
            try:
                data = os.read(master, 4096)
            except OSError:
                data = b""
            if data:
                try:
                    sock.sendall(data)
                except OSError as e:
                    print(f"[tunnel] send failed ({e}); reconnecting", flush=True)
                    sock.close(); sock = None
                    continue

        # network -> PTY (SLCAN frames from the ESP32)
        if sock in r:
            try:
                data = sock.recv(4096)
            except OSError:
                data = b""
            if not data:
                print("[tunnel] peer closed; reconnecting", flush=True)
                sock.close(); sock = None
                continue
            try:
                os.write(master, data)
            except OSError as e:
                # PTY closed (slcand exited) - recreate it
                print(f"[tunnel] PTY write failed ({e}); recreating", flush=True)
                os.close(master); os.close(slave)
                master, slave = pty.openpty()
                os.set_blocking(master, False)
                slave_name = os.ttyname(slave)
                print(f"[tunnel] new PTY: {slave_name}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[tunnel] stopped")

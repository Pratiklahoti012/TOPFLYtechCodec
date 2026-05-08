#!/usr/bin/env python3
import argparse
import asyncio
from bleak import BleakClient, BleakScanner

DEFAULT_DATA_CHAR = "27763561-999c-4d6a-9fc4-c7272be10900"


def bytes_to_short(data: bytes, offset: int) -> int:
    return ((data[offset] & 0xFF) << 8) | (data[offset + 1] & 0xFF)


def decode_temp_block(block: bytes):
    if len(block) != 15:
        return None
    temp_raw = bytes_to_short(block, 8)
    if temp_raw == 0xFFFF:
        temp_c = None
    else:
        sign = -1 if (temp_raw & 0x8000) == 0 else 1
        temp_c = (temp_raw & 0x7FFF) * 0.01 * sign

    hum_raw = bytes_to_short(block, 10)
    humidity = None if hum_raw == 0xFFFF else hum_raw * 0.01
    battery = None if block[7] == 0xFF else block[7]
    voltage = None if block[6] == 0xFF else 2 + block[6] * 0.01
    mac = block[0:6].hex(":").upper()
    return mac, temp_c, humidity, battery, voltage


def parse_temp_payload(data: bytes):
    if len(data) < 17 or data[0] != 0x00 or data[1] != 0x04:
        return []
    out = []
    i = 2
    while i + 15 <= len(data):
        x = decode_temp_block(data[i:i + 15])
        if x:
            out.append(x)
        i += 15
    return out


async def main():
    ap = argparse.ArgumentParser(description="Read T-sense via GATT notifications")
    ap.add_argument("--mac", required=True, help="BLE MAC, e.g. CC:FE:0D:75:14:96")
    ap.add_argument("--char", default=DEFAULT_DATA_CHAR, help="Notify/write characteristic UUID")
    ap.add_argument("--scan-timeout", type=float, default=20.0)
    ap.add_argument("--settle", type=float, default=5.0, help="seconds to wait for auto push")
    ap.add_argument("--send-commands", action="store_true", help="send probe commands to the characteristic")
    args = ap.parse_args()

    print(f"Scanning for {args.mac} ...")
    dev = await BleakScanner.find_device_by_address(args.mac, timeout=args.scan_timeout)
    if not dev:
        raise SystemExit("Device not found")

    cmds = [b"\x00\x01", b"\x00\x02", b"\x00\x03", b"\x01\x01", b"\x02\x00", b"\x10\x00", b"\x00\x10"]

    def on_notify(sender, data):
        b = bytes(data)
        print(f"notify {sender}: {b.hex()} ({list(b)})")
        if b == b"\x04\x00\x02":
            print("  -> device returned 04 00 02 (command rejected/unsupported).")
            print("     This usually means this characteristic is notify-only or requires a login/session command first.")
        readings = parse_temp_payload(b)
        for mac, t, h, bat, volt in readings:
            print(f"  -> sensor={mac} temp={t}C hum={h}% bat={bat}% volt={volt}V")

    async with BleakClient(dev, timeout=20.0) as c:
        print("Connected. Service/characteristic summary:")
        for svc in c.services:
            for ch in svc.characteristics:
                props = ",".join(ch.properties)
                print(f"  - {ch.uuid} props=[{props}]")

        print("Subscribing...")
        await c.start_notify(args.char, on_notify)
        print(f"Waiting {args.settle}s for auto-push...")
        await asyncio.sleep(args.settle)

        if args.send_commands:
            for cmd in cmds:
                print(f"write {cmd.hex()}")
                try:
                    await c.write_gatt_char(args.char, cmd, response=True)
                except Exception:
                    await c.write_gatt_char(args.char, cmd, response=False)
                await asyncio.sleep(2)
        else:
            print("Skipping writes (default). Use --send-commands if you want to probe command support.")

        print("Listening 10s for late packets...")
        await asyncio.sleep(10)
        await c.stop_notify(args.char)


if __name__ == "__main__":
    asyncio.run(main())

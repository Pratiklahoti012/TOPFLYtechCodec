#!/usr/bin/env python3
"""Simple BLE notification reader for T-sense devices using bleak only.

This script intentionally avoids command probing/writes. It just:
1) Scans and connects by MAC.
2) Chooses a notify characteristic (or uses --char if provided).
3) Subscribes and prints decoded packets.
"""

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
        decoded = decode_temp_block(data[i : i + 15])
        if decoded:
            out.append(decoded)
        i += 15
    return out


def pick_notify_char(client: BleakClient, preferred: str | None) -> str:
    if preferred:
        return preferred

    uuids = []
    for svc in client.services:
        for ch in svc.characteristics:
            props = set(ch.properties)
            if "notify" in props:
                uuids.append(ch.uuid)

    if DEFAULT_DATA_CHAR in uuids:
        return DEFAULT_DATA_CHAR
    if uuids:
        return uuids[0]

    raise RuntimeError("No notify characteristic found on device")


async def main():
    parser = argparse.ArgumentParser(description="Simple T-sense BLE notification reader")
    parser.add_argument("--mac", required=True, help="BLE MAC, e.g. CC:FE:0D:75:14:96")
    parser.add_argument("--char", default=None, help="Notification characteristic UUID (optional)")
    parser.add_argument("--scan-timeout", type=float, default=20.0, help="Seconds to scan for the device")
    parser.add_argument("--listen", type=float, default=30.0, help="Seconds to keep listening")
    args = parser.parse_args()

    print(f"Scanning for {args.mac} ...")
    dev = await BleakScanner.find_device_by_address(args.mac, timeout=args.scan_timeout)
    if not dev:
        raise SystemExit("Device not found")

    async with BleakClient(dev, timeout=20.0) as client:
        print("Connected.")
        char_uuid = pick_notify_char(client, args.char)
        print(f"Using notify characteristic: {char_uuid}")

        def on_notify(sender, data):
            packet = bytes(data)
            print(f"notify {sender}: {packet.hex()}")
            readings = parse_temp_payload(packet)
            for mac, temp_c, hum, bat, volt in readings:
                print(f"  -> sensor={mac} temp={temp_c}C hum={hum}% bat={bat}% volt={volt}V")

        await client.start_notify(char_uuid, on_notify)
        print(f"Listening for {args.listen}s ...")
        await asyncio.sleep(args.listen)
        await client.stop_notify(char_uuid)
        print("Done.")


if __name__ == "__main__":
    asyncio.run(main())

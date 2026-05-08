#!/usr/bin/env python3
"""
Read temperature from a nearby TOPFLYtech BLE temperature sensor using BLE advertisements.

This script scans BLE advertisements, decodes manufacturer data blocks in the same format
used by TOPFLYtech codec libraries, and prints temperature updates.

Requirements:
  pip install bleak

Usage:
  python ble_temp_reader.py --name T-Sense
  python ble_temp_reader.py --mac AA:BB:CC:DD:EE:FF
"""

import argparse
import asyncio
from datetime import datetime, timezone

from bleak import BleakScanner


def bytes_to_short(data: bytes, offset: int) -> int:
    return ((data[offset] & 0xFF) << 8) | (data[offset + 1] & 0xFF)


def decode_ble_temp_block(block: bytes):
    """
    Decode one BLE temp payload block using the structure from Topflytech codec
    getBleTempData (mac[6], voltage[1], battery[1], temp[2], humidity[2], light[2], rssi[1]).
    """
    if len(block) < 15:
        return None

    mac = block[0:6].hex().upper()
    if mac.startswith("0000"):
        mac = mac[4:12]

    voltage_tmp = block[6]
    voltage = -999 if voltage_tmp == 255 else 2 + 0.01 * voltage_tmp

    battery_tmp = block[7]
    battery = -999 if battery_tmp == 255 else battery_tmp

    temperature_raw = bytes_to_short(block, 8)
    if temperature_raw == 0xFFFF:
        temperature = -999
    else:
        # Mirrors repo logic: sign bit handling as implemented there.
        temp_positive = -1 if (temperature_raw & 0x8000) == 0 else 1
        temperature = (temperature_raw & 0x7FFF) * 0.01 * temp_positive

    humidity_raw = bytes_to_short(block, 10)
    humidity = -999 if humidity_raw == 0xFFFF else humidity_raw * 0.01

    return {
        "sensor_mac": mac,
        "temperature_c": round(temperature, 2),
        "humidity_pct": round(humidity, 2) if humidity != -999 else -999,
        "battery_pct": battery,
        "voltage_v": round(voltage, 2) if voltage != -999 else -999,
    }


def extract_candidate_blocks(manufacturer_data: dict[int, bytes]):
    """Collect candidate 15-byte blocks from advertisement manufacturer data."""
    blocks = []
    for _, payload in manufacturer_data.items():
        if not payload:
            continue

        # Some frames pack BLE data with leading type bytes; scan payload for 15-byte chunks.
        for i in range(0, max(0, len(payload) - 14)):
            chunk = payload[i : i + 15]
            decoded = decode_ble_temp_block(chunk)
            if decoded is not None:
                blocks.append(decoded)
    return blocks


async def main():
    parser = argparse.ArgumentParser(description="Read nearby TOPFLYtech BLE temperature")
    parser.add_argument("--name", help="Filter by device name substring (e.g. T-Sense)")
    parser.add_argument("--mac", help="Filter by BLE MAC address")
    parser.add_argument("--timeout", type=float, default=0.0, help="Run seconds (0=forever)")
    args = parser.parse_args()

    target_mac = args.mac.upper() if args.mac else None
    name_filter = args.name.lower() if args.name else None

    print("Scanning BLE advertisements... Ctrl+C to stop")

    def detection_callback(device, advertisement_data):
        if target_mac and device.address.upper() != target_mac:
            return
        if name_filter:
            name = (device.name or advertisement_data.local_name or "").lower()
            if name_filter not in name:
                return

        blocks = extract_candidate_blocks(advertisement_data.manufacturer_data)
        for d in blocks:
            now = datetime.now(timezone.utc).isoformat()
            print(
                f"[{now}] dev={device.address} sensor={d['sensor_mac']} "
                f"temp={d['temperature_c']}°C hum={d['humidity_pct']}% "
                f"bat={d['battery_pct']}% volt={d['voltage_v']}V"
            )

    scanner = BleakScanner(detection_callback)
    await scanner.start()
    try:
        if args.timeout > 0:
            await asyncio.sleep(args.timeout)
        else:
            while True:
                await asyncio.sleep(1)
    finally:
        await scanner.stop()


if __name__ == "__main__":
    asyncio.run(main())

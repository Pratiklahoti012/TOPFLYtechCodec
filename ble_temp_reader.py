#!/usr/bin/env python3
"""
Read temperature from a nearby TOPFLYtech BLE temperature sensor.

This scanner decodes manufacturer-data payloads that match TOPFLYtech's BLE temp
message layout used in the repo codec:
  [0x00, 0x04] + N * 15-byte temp blocks
"""

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from bleak import BleakScanner


@dataclass(frozen=True)
class TempReading:
    sensor_mac: str
    temperature_c: float
    humidity_pct: float
    battery_pct: int
    voltage_v: float


def bytes_to_short(data: bytes, offset: int) -> int:
    return ((data[offset] & 0xFF) << 8) | (data[offset + 1] & 0xFF)


def decode_temp_block(block: bytes) -> TempReading | None:
    if len(block) != 15:
        return None

    mac = block[0:6].hex().upper()
    if mac.startswith("0000"):
        mac = mac[4:12]

    voltage_raw = block[6]
    voltage = -999.0 if voltage_raw == 255 else 2 + 0.01 * voltage_raw

    battery = -999 if block[7] == 255 else block[7]

    # Match repo's Python codec implementation exactly.
    temp_raw = bytes_to_short(block, 8)
    if temp_raw == 0xFFFF:
        temperature = -999.0
    else:
        temp_positive = -1 if (temp_raw & 0x8000) == 0 else 1
        temperature = (temp_raw & 0x7FFF) * 0.01 * temp_positive

    humidity_raw = bytes_to_short(block, 10)
    humidity = -999.0 if humidity_raw == 0xFFFF else humidity_raw * 0.01

    reading = TempReading(
        sensor_mac=mac,
        temperature_c=round(temperature, 2),
        humidity_pct=round(humidity, 2) if humidity != -999.0 else -999.0,
        battery_pct=battery,
        voltage_v=round(voltage, 2) if voltage != -999.0 else -999.0,
    )

    # Guard rails: filter obvious false-positive parses.
    if not plausible(reading):
        return None
    return reading


def plausible(r: TempReading) -> bool:
    if r.temperature_c != -999.0 and not (-60.0 <= r.temperature_c <= 125.0):
        return False
    if r.humidity_pct != -999.0 and not (0.0 <= r.humidity_pct <= 100.0):
        return False
    if r.battery_pct != -999 and not (0 <= r.battery_pct <= 100):
        return False
    if r.voltage_v != -999.0 and not (1.5 <= r.voltage_v <= 4.5):
        return False
    return True


def parse_topflytech_temp_payload(payload: bytes) -> Iterable[TempReading]:
    # Expected pattern from repo for temp-only BLE packets: 00 04 + 15-byte blocks.
    if len(payload) < 2 or payload[0] != 0x00 or payload[1] != 0x04:
        return []

    out: list[TempReading] = []
    i = 2
    while i + 15 <= len(payload):
        reading = decode_temp_block(payload[i : i + 15])
        if reading is not None:
            out.append(reading)
        i += 15
    return out


async def main() -> None:
    parser = argparse.ArgumentParser(description="Read nearby TOPFLYtech BLE temperature")
    parser.add_argument("--name", help="Filter by BLE local-name substring (example: T-Sense)")
    parser.add_argument("--mac", help="Filter by advertiser MAC address")
    parser.add_argument("--timeout", type=float, default=0.0, help="Run seconds; 0 = forever")
    args = parser.parse_args()

    target_mac = args.mac.upper() if args.mac else None
    name_filter = args.name.lower() if args.name else None

    last_by_sensor: dict[str, TempReading] = {}
    print("Scanning BLE advertisements... Ctrl+C to stop")

    def callback(device, adv):
        if target_mac and device.address.upper() != target_mac:
            return
        if name_filter:
            name = (device.name or adv.local_name or "").lower()
            if name_filter not in name:
                return

        for payload in adv.manufacturer_data.values():
            for reading in parse_topflytech_temp_payload(payload):
                if last_by_sensor.get(reading.sensor_mac) == reading:
                    continue
                last_by_sensor[reading.sensor_mac] = reading
                now = datetime.now(timezone.utc).isoformat()
                print(
                    f"[{now}] dev={device.address} sensor={reading.sensor_mac} "
                    f"temp={reading.temperature_c}°C hum={reading.humidity_pct}% "
                    f"bat={reading.battery_pct}% volt={reading.voltage_v}V"
                )

    scanner = BleakScanner(callback)
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

"""Weather for the watch's face: Open-Meteo, for the time zone's own city.

No location is looked up or shared: zone1970.tab gives every zone a
representative city with its coordinates (Asia/Kolkata -> Kolkata), the same
table the shell's time-zone map uses. Fetched only while the person has turned
watch weather on (settings: watch-weather-enabled).
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request

FORECAST = "https://api.open-meteo.com/v1/forecast"


def _zone() -> str:
    tz = os.environ.get("TZ", "").lstrip(":")
    if not tz:
        try:
            tz = os.path.realpath("/etc/localtime").split("zoneinfo/", 1)[1]
        except (IndexError, OSError):
            tz = ""
    return tz


def here():
    """(lat, lon, city) for this computer's time zone, or why not."""
    zone = _zone()
    for table in ("/usr/share/zoneinfo/zone1970.tab", "/usr/share/zoneinfo/zone.tab"):
        try:
            lines = open(table, encoding="utf-8").read().splitlines()
        except OSError:
            continue
        for line in lines:
            f = line.split("\t")
            if len(f) < 3 or f[2] != zone:
                continue
            m = re.match(r"^([+-])(\d{2})(\d{2})(\d{2})?([+-])(\d{3})(\d{2})(\d{2})?$", f[1])
            if not m:
                break
            lat = (int(m[2]) + int(m[3]) / 60) * (-1 if m[1] == "-" else 1)
            lon = (int(m[6]) + int(m[7]) / 60) * (-1 if m[5] == "-" else 1)
            return lat, lon, zone.split("/")[-1].replace("_", " ")
    return f"No city is known for the time zone {zone or '(none)'}."


def get(url: str, params: dict, timeout: float = 10.0) -> dict:
    with urllib.request.urlopen(url + "?" + urllib.parse.urlencode(params), timeout=timeout) as r:
        return json.loads(r.read().decode())

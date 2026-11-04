"""shani-link's own switches: what may run in the background without a click.

Not consent for an assistant - a person turns these on in the app, and nothing
here acts on its own beyond what they turned on (receiving files, keeping the
watch connected, showing the phone's texts, telling the watch about a call...).
Every service takes this object as its `config` and asks `get_bool(key)`, the
call it made of Chronoa's config before the move, with the same key names.

Kept in ~/.config/shani-link/settings.json. The first time it is read, what
Chronoa had for the same keys (GSettings, org.shani.chronoa) is carried over,
so a switch someone had already turned on is still on after the move.
"""
from __future__ import annotations

import json
import os
import threading

from shani_link import paths

#: the switches that moved with the devices, all off until turned on
KEYS = (
    "bluetooth-call-enabled", "bluetooth-gatt-enabled", "phone-control-enabled",
    "phone-remote-enabled", "phone-messages-read-enabled", "phone-messages-send-enabled",
    "watch-companion-enabled", "watch-nowplaying-enabled", "watch-weather-enabled",
    "landline-enabled", "bluetooth-media-remote-enabled",
)
#: the one that was on unless turned off (music control from Bluetooth remotes)
DEFAULT_ON = {"bluetooth-media-remote-enabled"}
CHRONOA_SCHEMA = "org.shani.chronoa"


def _from_chronoa() -> dict:
    try:
        from gi.repository import Gio
        source = Gio.SettingsSchemaSource.get_default()
        schema = source.lookup(CHRONOA_SCHEMA, True) if source is not None else None
        if schema is None:
            return {}
        gs = Gio.Settings.new_full(schema, None, None)
        return {k: bool(gs.get_boolean(k)) for k in KEYS if schema.has_key(k)}
    except Exception:  # noqa: BLE001 - no Chronoa is a fresh start
        return {}


class Settings:
    def __init__(self, path=None):
        self.path = path or (paths.config_home() / "shani-link" / "settings.json")
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            data = _from_chronoa()
            if data:
                self._write(data)
            return data
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def get_bool(self, key: str, default: bool = False) -> bool:
        with self._lock:
            return bool(self._data.get(key, default))

    def set_bool(self, key: str, value: bool) -> None:
        with self._lock:
            self._data[key] = bool(value)
            self._write(self._data)

    def all(self) -> dict:
        with self._lock:
            return {k: bool(self._data.get(k, k in DEFAULT_ON)) for k in KEYS}

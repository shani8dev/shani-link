"""The Background page's switches, read and set where the service reads them.

The service holds its own `Settings` in memory, so a switch written only to the
file would not reach a running watcher until the service restarted. So a switch
goes through the service (`dev.shani.Link.SetSetting`) when it answers, and
straight to the file (`shani_link.settings.Settings`) when it does not - the
service reads the file when it next starts.
"""
from __future__ import annotations

import json
import logging

from shani_link import settings as _settings

logger = logging.getLogger(__name__)

BUS_NAME = "dev.shani.Link"
PATH = "/dev/shani/Link"
IFACE = "dev.shani.Link"

#: What each switch is called on the Background page, and what it does.
#: Every key in `settings.KEYS` has a line here (tests/test_app_pages.py holds that).
LABELS = {
    "bluetooth-call-enabled": (
        "Calls through your phone",
        "Show a ringing call from your Bluetooth phone here, and answer it on this computer"),
    "phone-control-enabled": (
        "Receive files over Bluetooth",
        "Accept files your phone shares to this computer, asking each time, into "
        "Downloads; text notifications need it too"),
    "phone-messages-read-enabled": (
        "Show your phone's texts",
        "A notification for each new text, and the caller's name on a ringing call"),
    "phone-messages-send-enabled": (
        "Reply to texts from notifications",
        "A Reply button on the text notification"),
    "phone-remote-enabled": (
        "Phone as a remote",
        "This computer offers itself as a Bluetooth keyboard and media remote to your phone"),
    "bluetooth-gatt-enabled": (
        "Your watch, in the background",
        "A ringing call shows on your watch; the watch switches below need it"),
    "watch-companion-enabled": (
        "Keep the watch connected",
        "Its find-my-phone, music and camera buttons work here; the Da Fit app cannot reach it meanwhile"),
    "watch-nowplaying-enabled": (
        "Music on the watch",
        "What is playing here shows on the watch"),
    "watch-weather-enabled": (
        "Weather on the watch",
        "Sends the forecast for where you are when the watch asks"),
    "landline-enabled": (
        "JioFiber landline",
        "Your landline rings and dials on this computer"),
    "bluetooth-media-remote-enabled": (
        "Media keys from Bluetooth",
        "Headphones' and car kits' play/pause and skip buttons control music here"),
}


class Switches:
    """`get_bool(key)` / `set_bool(key, on)` / `all()`, through the service if it
    answers. `bus=None` asks the session bus; tests pass `bus=False` to stay on
    the file (never the person's own session bus in a test)."""

    def __init__(self, bus=None, store: "_settings.Settings | None" = None):
        self._bus = bus
        self.store = store or _settings.Settings()

    def _conn(self):
        if self._bus is False:
            return None
        if self._bus is None:
            try:
                from gi.repository import Gio
                self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            except Exception:  # noqa: BLE001 - no bus is the file
                self._bus = False
                return None
        return self._bus

    def _call(self, method: str, params=None):
        conn = self._conn()
        if conn is None:
            raise RuntimeError("no session bus")
        from gi.repository import Gio
        reply = conn.call_sync(BUS_NAME, PATH, IFACE, method, params, None,
                               Gio.DBusCallFlags.NONE, 3000, None)
        return reply.unpack() if reply is not None else ()

    def all(self) -> dict:
        try:
            return {k: bool(v) for k, v in json.loads(self._call("Settings")[0]).items()}
        except Exception:  # noqa: BLE001 - the service is not there: the file is
            return self.store.all()

    def get_bool(self, key: str, default: bool = False) -> bool:
        return bool(self.all().get(key, default))

    def set_bool(self, key: str, on: bool) -> None:
        try:
            from gi.repository import GLib
            self._call("SetSetting", GLib.Variant("(sb)", (key, bool(on))))
            return
        except Exception:  # noqa: BLE001
            logger.debug("service did not take %s; writing the file", key, exc_info=True)
        self.store.set_bool(key, on)

    # the shape Chronoa's pages called (config.set(key, "true"))
    def set(self, key: str, value) -> None:
        self.set_bool(key, value in (True, "true", "1", 1))

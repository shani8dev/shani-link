"""Media players over MPRIS (D-Bus): the watch's and Bluetooth remotes' music
buttons. What playerctl does, without it."""
from __future__ import annotations

from typing import List, Optional

PREFIX = "org.mpris.MediaPlayer2."
PATH = "/org/mpris/MediaPlayer2"
PLAYER = "org.mpris.MediaPlayer2.Player"


def _bus():
    from gi.repository import Gio
    return Gio.bus_get_sync(Gio.BusType.SESSION, None)


def players() -> List[str]:
    from gi.repository import Gio
    try:
        names = _bus().call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                 "ListNames", None, None, Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
    except Exception:  # noqa: BLE001
        return []
    return sorted(n for n in names if n.startswith(PREFIX))


def _prop(player: str, name: str):
    from gi.repository import Gio, GLib
    try:
        return _bus().call_sync(player, PATH, "org.freedesktop.DBus.Properties", "Get",
                                GLib.Variant("(ss)", (PLAYER, name)), None, Gio.DBusCallFlags.NONE,
                                2000, None).unpack()[0]
    except Exception:  # noqa: BLE001
        return None


def status(player: str) -> str:
    return _prop(player, "PlaybackStatus") or "Stopped"


def now_playing(player: str) -> str:
    meta = _prop(player, "Metadata") or {}
    title = meta.get("xesam:title", "")
    artist = ", ".join(meta.get("xesam:artist", []) or [])
    return " - ".join(x for x in (artist, title) if x)


def current() -> Optional[str]:
    """The player a button means: the one playing, else the first."""
    found = players()
    playing = [p for p in found if status(p) == "Playing"]
    return (playing or found or [None])[0]


def control(action: str) -> str:
    """play | pause | toggle | next | previous | stop - on the current player."""
    from gi.repository import Gio
    method = {"play": "Play", "pause": "Pause", "toggle": "PlayPause", "next": "Next",
              "previous": "Previous", "stop": "Stop"}.get(action)
    player = current()
    if not method or not player:
        return "No media player is running." if method else f"Unknown media action: {action}"
    try:
        _bus().call_sync(player, PATH, PLAYER, method, None, None, Gio.DBusCallFlags.NONE, 2000, None)
    except Exception as exc:  # noqa: BLE001
        return f"The player did not take it: {exc}"
    return f"{action}: {now_playing(player) or player[len(PREFIX):]}"

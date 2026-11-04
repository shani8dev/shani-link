"""Media players over MPRIS (D-Bus): the watch's and Bluetooth remotes' music
buttons, the app's music row, and Chronoa's `media_control` skill (which keeps
only its wording). What playerctl does, without it.

Seeking is the Player interface's own Seek and SetPosition, and `can_seek` and
`position` are there so a caller can check before and verify after: a live
stream or a player that does not implement seeking says so rather than "done"."""
from __future__ import annotations

import os
from typing import List, Optional

PREFIX = "org.mpris.MediaPlayer2."
PATH = "/org/mpris/MediaPlayer2"
PLAYER = "org.mpris.MediaPlayer2.Player"


_connection: dict = {}


def _bus():
    """The session bus *named now* by DBUS_SESSION_BUS_ADDRESS.

    Not Gio's process-wide singleton: that one is whichever bus the process
    first reached, and a caller that points the variable at another bus (a test
    with its own private daemon, after an earlier one) would keep asking the old
    one and find no players."""
    from gi.repository import Gio
    address = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    conn = _connection.get(address)
    if conn is None or conn.is_closed():
        if not address:
            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        else:
            flags = (Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                     | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION)
            conn = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
        _connection.clear()
        _connection[address] = conn
    return conn


def players() -> List[str]:
    from gi.repository import Gio
    try:
        names = _bus().call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                 "ListNames", None, None, Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
    except Exception:  # noqa: BLE001
        return []
    return sorted(n for n in names if n.startswith(PREFIX))


def _prop(player: str, name: str, iface: str = PLAYER):
    from gi.repository import Gio, GLib
    try:
        return _bus().call_sync(player, PATH, "org.freedesktop.DBus.Properties", "Get",
                                GLib.Variant("(ss)", (iface, name)), None, Gio.DBusCallFlags.NONE,
                                2000, None).unpack()[0]
    except Exception:  # noqa: BLE001
        return None


def identity(player: str) -> str:
    """The player's own name for itself ("Firefox", "Spotify"); the bus name
    ends in an instance id ("chromium.instance4567"), so it is the fallback."""
    return _prop(player, "Identity", "org.mpris.MediaPlayer2") or player.rsplit(".", 1)[-1]


def metadata(player: str) -> dict:
    return _prop(player, "Metadata") or {}


def title_artist(player: str) -> "tuple[str, str]":
    """(title, first artist) of what is playing, each "" when the player does not say."""
    meta = metadata(player)
    artists = meta.get("xesam:artist") or []
    return str(meta.get("xesam:title") or ""), str(artists[0]) if artists else ""


def can_seek(player: str) -> bool:
    return bool(_prop(player, "CanSeek"))


def position(player: str) -> Optional[int]:
    """The play position in microseconds, or None when the player does not say."""
    value = _prop(player, "Position")
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def call(player: str, method: str, params=None) -> "tuple[bool, str]":
    """(done, why-not): one Player method on `player`."""
    from gi.repository import Gio
    try:
        _bus().call_sync(player, PATH, PLAYER, method, params, None, Gio.DBusCallFlags.NONE, 2000, None)
    except Exception as exc:  # noqa: BLE001 - the player's refusal, said
        return False, getattr(exc, "message", None) or str(exc)
    return True, ""


def seek(player: str, offset_us: int) -> "tuple[bool, str]":
    """Move `offset_us` microseconds (negative goes back) - the spec's Seek."""
    from gi.repository import GLib
    return call(player, "Seek", GLib.Variant("(x)", (int(offset_us),)))


def restart(player: str) -> "tuple[bool, str]":
    """The current track from its start: SetPosition(track, 0), or - with no
    track id to name - a Seek back past the start, which the spec says lands on it."""
    from gi.repository import GLib
    track = metadata(player).get("mpris:trackid")
    if track:
        return call(player, "SetPosition", GLib.Variant("(ox)", (str(track), 0)))
    return seek(player, -(position(player) or 0) - 1_000_000)


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


#: the no-argument Player methods, by the word a button or a person uses
METHODS = {"play": "Play", "pause": "Pause", "toggle": "PlayPause", "next": "Next",
           "previous": "Previous", "stop": "Stop"}


def control(action: str) -> str:
    """play | pause | toggle | next | previous | stop - on the current player."""
    method = METHODS.get(action)
    player = current()
    if not method or not player:
        return "No media player is running." if method else f"Unknown media action: {action}"
    done, why = call(player, method)
    if not done:
        return f"The player did not take it: {why}"
    return f"{action}: {now_playing(player) or player[len(PREFIX):]}"

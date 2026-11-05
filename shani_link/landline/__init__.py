"""The JioFiber landline, on this computer, through the router - like JioJoin.

`setup_start()` / `setup_finish(code)` add this computer to the line once (the
owner types the code Jio texts them; see provision.py). After that, with
the line switched on (`set_enabled`), `start()` fetches a fresh password from
the router and registers (ua.py). shani-link's D-Bus service (dev.shani.Link)
runs it for the whole desktop: the shell's call card and dialer, Chronoa's
`landline_call` skill and Phone page, and the incoming-call notification all
act on the one line here:

    dial(number), answer(), hangup(), dtmf(digit), status()

Everything is the standard library and PipeWire's `pw-cat`: no SIP library,
no bridge, no other program.
"""
from __future__ import annotations

import json
import logging
import pathlib
import threading
from typing import Callable, Dict

import subprocess

from shani_link.landline import provision
from shani_link.landline.provision import ProvisionError

logger = logging.getLogger(__name__)

CONSENT_KEY = "landline-enabled"
NOTIFY = ("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
          "org.freedesktop.Notifications")

_STATE: Dict = {"line": None, "app": None, "listeners": [], "sinks": [], "pending": None,
                "nid": 0, "subscribed": False, "starting": False}


# --- setting up, once ----------------------------------------------------------

def is_set_up() -> bool:
    saved = provision.load()
    return bool(saved.get("alias") and saved.get("host"))


def setup_start(host: str = "") -> str:
    """Ask the router to add this computer; returns where the code went (e.g. '...4321')."""
    host = host or provision.find_router()
    if not host:
        raise ProvisionError("could not find the JioFiber router on this network")
    acct = provision.account(host)
    alias = provision.load().get("alias") or provision.default_alias()
    cookie, masked = provision.start_add(host, alias)
    _STATE["pending"] = {"host": host, "alias": alias, "cookie": cookie,
                         "number": str(acct.get("msisdn") or "")}
    return masked or "your registered mobile"


def setup_finish(code: str) -> str:
    """Confirm with the texted code; returns the landline number now on this computer."""
    pending = _STATE.get("pending")
    if not pending:
        raise ProvisionError("start adding this computer first - no code has been asked for")
    if not provision.confirm(pending["host"], code, pending["cookie"]):
        raise ProvisionError("the router did not accept that code")
    creds = provision.credentials(pending["host"], pending["alias"])
    from shani_link.landline import sip
    number = sip.user_of(creds["public"]) or pending["number"]
    provision.save({"host": pending["host"], "alias": pending["alias"], "number": number,
                    "realm": creds["realm"], "user": creds["user"], "public": creds["public"]})
    _STATE["pending"] = None
    return number


def forget() -> None:
    stop()
    try:
        provision.state_file().unlink()
    except OSError:
        pass


# --- the running line ------------------------------------------------------------

def line():
    return _STATE["line"]


def ready() -> bool:
    current = _STATE["line"]
    return bool(current is not None and current.registered)


def listen(callback: Callable[..., None]) -> Callable[[], None]:
    """`callback(kind, **info)` on every line event; returns the way to stop listening."""
    _STATE["listeners"].append(callback)
    return lambda: callback in _STATE["listeners"] and _STATE["listeners"].remove(callback)


def video_sink(sink: Callable[[bytes, int, int], None]) -> Callable[[], None]:
    """`sink(rgb24, width, height)` for every picture the line decodes.

    The decoder's own thread calls this, so anything GTK-shaped has to marshal
    onto the main loop before touching a widget - the same rule `listen`'s
    events follow, for the same reason. A machine with no camera still
    *receives* a picture, so this is not gated on being able to send one.
    """
    _STATE["sinks"].append(sink)
    return lambda: sink in _STATE["sinks"] and _STATE["sinks"].remove(sink)


def _frame(rgb: bytes, width: int, height: int) -> None:
    """One decoded picture, handed to every screen that is listening."""
    for sink in list(_STATE["sinks"]):
        try:
            sink(rgb, width, height)
        except Exception:  # noqa: BLE001 - one broken viewer is not a broken call
            logger.exception("landline: a video sink raised")


def _video_session():
    """A bound video session: a camera to send, and a decoder feeding the screens.

    Both halves are optional, and `video.available()` is what says which of them
    this machine has - a camera with no ffmpeg is no camera at all. The decoder
    is what the panel sees, so it exists whether or not there is anything to
    send, and its sink fans out to the screens through `_frame`.
    """
    try:
        from shani_link.landline import video
        ok, device = video.available()
        camera = video.Camera(device) if ok else None
        return video.VideoRtpSession.bind("127.0.0.1", camera=camera,
                                          decoder=video.Decoder(sink=_frame))
    except Exception as exc:  # noqa: BLE001 - no video session is a voice call
        logger.warning("landline: no video session: %s", exc)
        return None


def enabled() -> bool:
    """Whether the line may run: set up and switched on (setting it up is the
    owner's own act, so a set-up line starts on unless switched off)."""
    return is_set_up() and provision.load().get("enabled", True) is not False


def set_enabled(on: bool) -> None:
    """Switch the line on or off (Chronoa's privacy switch, the shell's Settings)."""
    state = provision.load()
    state["enabled"] = bool(on)
    provision.save(state)
    if on:
        start()
    else:
        stop()


def start(app=None, line_factory=None) -> bool:
    """Register the line when it is set up and allowed; quietly nothing otherwise."""
    if app is not None:
        _STATE["app"] = app
    app = _STATE["app"]
    if _STATE["line"] is not None or _STATE["starting"]:
        return True
    if not enabled():
        return False
    _STATE["starting"] = True
    _subscribe_actions()

    def work():
        try:
            saved = provision.load()
            creds = provision.credentials(saved["host"], saved["alias"])
            _, instance = provision.device_id(saved["alias"])
            from shani_link.landline.ua import Line
            if line_factory is None:
                current = Line(saved["host"], creds, instance, on_event=_event,
                               video_factory=_video_session)
            else:
                current = line_factory(saved["host"], creds, instance, on_event=_event)
            _STATE["line"] = current
            current.start()
        except Exception as exc:  # noqa: BLE001
            logger.warning("landline: could not start: %s", exc)
            _event("unregistered", reason=str(exc))
        finally:
            _STATE["starting"] = False
    threading.Thread(target=work, daemon=True, name="landline-start").start()
    return True


def stop() -> None:
    current, _STATE["line"] = _STATE["line"], None
    if current is not None:
        current.stop()


def status() -> str:
    saved = provision.load()
    if not saved.get("alias"):
        return "The landline is not set up on this computer. Set it up first (Settings, or Chronoa's Phone page)."
    current = _STATE["line"]
    if current is None:
        return f"The landline {saved.get('number', '')} is set up but not connected."
    if not current.registered:
        why = f" ({current.last_error})" if current.last_error else ""
        return f"The landline {saved.get('number', '')} is connecting to the router{why}."
    call = current.call
    if call is None:
        return f"The landline {current.number} is ready."
    return f"The landline {current.number}: {call.state} call with {call.name or call.number}."


def dial(number: str) -> str:
    current = _STATE["line"]
    if current is None or not current.registered:
        raise RuntimeError("the landline is not connected")
    current.dial(number)
    from shani_link.landline.ua import dial_user
    return dial_user(number)


def answer() -> None:
    _need().answer()


def hangup() -> None:
    _need().hangup()


def dtmf(digit: str) -> bool:
    current = _STATE["line"]
    return bool(current and current.dtmf(digit))


# --- in-call controls that do not need the SIP engine -------------------------------------
# Mute is done at PipeWire, not in the RTP sender: it is the same microphone the
# rest of the desktop shares, muting it is instant and visible in the system
# tray, and it stays off the engine the other session is still shaping.

def set_muted(on: bool) -> bool:
    """Mute or unmute the microphone the call uses. True on success."""
    try:
        subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "1" if on else "0"],
                       capture_output=True, timeout=5, check=True)
        _STATE["muted"] = bool(on)
        return True
    except Exception:  # noqa: BLE001
        return False


def muted() -> bool:
    try:
        out = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SOURCE@"],
                             capture_output=True, text=True, timeout=5)
        return "MUTED" in (getattr(out, "stdout", "") or "")
    except Exception:  # noqa: BLE001
        return bool(_STATE.get("muted"))


def in_call() -> bool:
    """Whether a call is active right now (for the in-call controls)."""
    current = _STATE["line"]
    return bool(current and current.call and current.call.state == "active")


def hold() -> bool:
    """Put the live call aside; the next number rings instead of being refused."""
    current = _need()
    return bool(current.hold())


def swap() -> bool:
    """Trade the live call for the one on hold."""
    current = _need()
    return bool(current.swap())


def merge() -> bool:
    """Put every live call into one conference."""
    current = _need()
    return bool(current.merge())

def calls() -> "list":
    """Every live call, oldest first, as the panel sees them (a facade-type view)."""
    current = _STATE["line"]
    return list(getattr(current, "calls", []) or [])


def video_available() -> bool:
    """Whether this machine can *send* a picture (a camera plus ffmpeg).

    Asked by the panel to say why a call has no video, rather than it guessing
    between "no camera here" and "the other side declined" - which are two
    different things to be told.
    """
    try:
        from shani_link.landline import video
        ok, _why = video.available()
        return ok
    except Exception:  # noqa: BLE001
        return False


def _need():
    current = _STATE["line"]
    if current is None:
        raise RuntimeError("the landline is not connected")
    return current


# --- call history (placed / received / missed): the one list every line shares ----------
# The landline used to keep its own landline-calls.json; its rows now go into
# `shani_link.calls_log` with the phone's and the modem's, and that file's old
# rows are carried over the first time the shared list is read.

HISTORY_MAX = 100


def history() -> "list[dict]":
    """Recent landline calls, newest first: {number, name, kind, when, seconds}
    (kind: placed, received or missed) - the shape this has always returned."""
    from shani_link import calls_log
    out = []
    for r in calls_log.rows(HISTORY_MAX, line="landline"):
        kind = "missed" if r.get("missed") else ("received" if r.get("direction") == "in" else "placed")
        out.append({"number": r.get("number", ""), "name": r.get("name", ""), "kind": kind,
                    "when": r.get("when", 0), "seconds": r.get("seconds", 0)})
    return out


def _log_history(kind: str, info: dict) -> None:
    # One row per call, written when it ends: an ended call that was never
    # 'answered' and came in is a missed call; an outgoing one is 'placed'.
    if kind == "ringing":
        _STATE["dir"] = "placed"
    elif kind == "incoming":
        _STATE["dir"] = "received"
    elif kind == "answered":
        _STATE["answered"] = True
    elif kind == "ended":
        from shani_link import calls_log
        direction = _STATE.pop("dir", "placed")
        answered = _STATE.pop("answered", False)
        number = info.get("number", "")
        calls_log.add("landline", number, "in" if direction == "received" else "out",
                      answered or direction == "placed", int(info.get("seconds", 0) or 0),
                      name=info.get("name", "") or _name_for(number))


def _name_for(number: str) -> str:
    """The caller's name from the address book (yours, then the phone's)."""
    try:
        from shani_link import contacts
        return contacts.name_for(number)
    except Exception:  # noqa: BLE001 - a number with no name still rings
        return ""


# --- contacts cache, so a name can be dialled (headless skill included) --------------------

def _contacts_file():
    import os
    base = os.environ.get("XDG_CACHE_HOME") or str(pathlib.Path.home() / ".cache")
    return provision.migrated(pathlib.Path(base) / "shani-link" / "landline-contacts.json")


def cache_contacts(book: "list") -> None:
    """Remember the phone's contact book so a name can be dialled later.

    `book` is the GUI's `[(name, (number, ...)), ...]`. Written by the Phone
    page when it loads contacts; read by `resolve_name` here and by the skill,
    which has no phone link of its own.
    """
    try:
        path = _contacts_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([[n, list(nums)] for n, nums in book]))
    except OSError:
        logger.debug("could not cache contacts", exc_info=True)


def resolve_name(name: str) -> str:
    """The best number for a contact `name` (case/space-insensitive), or ""."""
    want = "".join(name.lower().split())
    if not want:
        return ""
    try:                      # your own address book first: the names you saved win
        from shani_link import contacts
        for c in contacts.book().all():
            if c.numbers() and "".join(c.display_name().lower().split()) == want:
                return c.numbers()[0]
    except Exception:  # noqa: BLE001
        pass
    best = ""
    for entry in history_contacts():
        cname, numbers = entry[0], entry[1]
        flat = "".join(str(cname).lower().split())
        if not numbers:
            continue
        if flat == want:
            return str(numbers[0])
        if not best and want in flat:
            best = str(numbers[0])
    return best


def history_contacts() -> "list":
    try:
        return json.loads(_contacts_file().read_text())
    except (OSError, ValueError):
        return []


# --- events, the call card, the cue --------------------------------------------------------

def _event(kind: str, **info) -> None:
    _log_history(kind, info)
    for callback in list(_STATE["listeners"]):
        try:
            callback(kind, **info)
        except Exception:  # noqa: BLE001
            logger.debug("landline listener failed", exc_info=True)
    if kind == "incoming":
        _notify(_name_for(info.get("number", "")) or info.get("name") or info.get("number")
                or "Unknown caller", info.get("number", ""))
    elif kind in ("answered", "ended"):
        _close_note()


def _notify(who: str, number: str) -> None:
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        from shani_link.notify import APP_NAME, hints as _hints
        hints = _hints("call.incoming", urgency=GLib.Variant("y", 2),
                       **{"x-shani-link": GLib.Variant("s", "landline")})
        reply = bus.call_sync(*NOTIFY, "Notify", GLib.Variant("(susssasa{sv}i)", (
            APP_NAME, 0, "call-start-symbolic", f"Landline: {who}",
            number if number and number != who else "Incoming call",
            ["landline-answer", "Answer", "landline-decline", "Decline"], hints, 0)),
            GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, 3000, None)
        _STATE["nid"] = int(reply.unpack()[0])
    except Exception:  # noqa: BLE001
        logger.debug("landline: no call card", exc_info=True)


def _close_note() -> None:
    nid, _STATE["nid"] = _STATE["nid"], 0
    if not nid:
        return
    try:
        from gi.repository import Gio, GLib
        Gio.bus_get_sync(Gio.BusType.SESSION, None).call_sync(
            *NOTIFY, "CloseNotification", GLib.Variant("(u)", (nid,)), None,
            Gio.DBusCallFlags.NONE, 3000, None)
    except Exception:  # noqa: BLE001
        pass


def _subscribe_actions() -> None:
    if _STATE["subscribed"]:
        return
    try:
        from gi.repository import Gio
        Gio.bus_get_sync(Gio.BusType.SESSION, None).signal_subscribe(
            NOTIFY[0], NOTIFY[2], "ActionInvoked", NOTIFY[1], None,
            Gio.DBusSignalFlags.NONE, _on_action)
        _STATE["subscribed"] = True
    except Exception:  # noqa: BLE001
        pass


def _on_action(_conn, _sender, _path, _iface, _signal, params) -> None:
    nid, key = params.unpack()
    if nid != _STATE["nid"] or not key.startswith("landline-"):
        return
    try:
        answer() if key == "landline-answer" else hangup()
    except RuntimeError as exc:
        logger.info("landline: %s", exc)

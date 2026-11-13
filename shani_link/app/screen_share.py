"""Screen share: this computer's screen, on a paired phone.

`shani_link.screen_stream` does it - the ScreenCast portal round trip, the
encoder, the one-viewer server - over adb (a cable or Wi-Fi debugging) when a
phone is reachable that way, and over the phone's Bluetooth network when one is
not. Moved here from Shani Chronoa, where the panel went through the assistant's
`screen_to_phone` skill and its `screen-share-enabled` consent. Here pressing
Start is the person's own act; the desktop's ScreenCast portal still asks them
what to share, every time.

**The stream is a separate process** (`python -m shani_link.screen_stream`),
because the portal round trip wants a GLib main loop of its own and a stream
should not die with a window. It is tracked by its process, never by name.

**Its log is the page's memory.** Once the process is gone its last sentence is
the only account of why - so the stream writes to a small log under
`~/.local/state/shani-link`, and Check reads the last line back rather than
leaving a person with a frozen phone picture and no explanation.

**The check is slow and the page says so** (it imports GStreamer and reads the
kernel's address table), so every action runs on a worker.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import Any, Optional

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from shani_link.app import common  # noqa: E402
from shani_link import paths  # noqa: E402

TITLE = "Screen share"
ICON = "send-to-symbolic"
SUBTITLE = "Show this computer's screen on your phone, over a cable, Wi-Fi or Bluetooth."

#: The name of each road, as a person would say it.
_ROADS = {"usb": "over the USB cable", "wifi": "over Wi-Fi", "bluetooth": "over Bluetooth"}


#: The running stream, tracked by its process (never found again by name).
_PROC: "dict[str, Optional[subprocess.Popen]]" = {}

#: What the stream last said. One file, overwritten by every start.
_LOG = "screen-stream.log"

#: Whether the next stream listens for fingers. A switch on the page, not a
#: setting: it is about this session's phone, and it is off until pressed.
_TOUCH = {"on": False}


def log_path():
    return paths.state_home() / "shani-link" / _LOG


def last_words() -> str:
    """The last thing the stream said, or "" when it never said anything."""
    try:
        lines = [line.strip() for line in log_path().read_text(encoding="utf-8").splitlines()
                 if line.strip()]
    except OSError:
        return ""
    return lines[-1] if lines else ""


def road_detail(transport: str, where: str) -> str:
    """The sentence for one road: the address, and which road it is."""
    return f"http://{where} would serve it {_ROADS.get(transport, 'over adb')}"


def check() -> "tuple[str, str]":
    """(state, detail): "ready" and the address the phone opens, or why not."""
    from shani_link import screen_stream
    ok, why = screen_stream.gstreamer_ready()
    if not ok:
        return "cannot encode", why
    transport, where = screen_stream.choose()
    if not transport:
        return "no phone", where
    return "ready", road_detail(transport, where)


def act(action: str) -> str:
    """status | start | stop, in a sentence."""
    if action == "status":
        return _status()
    proc = _PROC.get("proc")
    if action == "stop":
        if proc is None or proc.poll() is not None:
            return "Nothing is being shared."
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        _PROC["proc"] = None
        _PROC.pop("where", None)
        return "Stopped. The portal session is closed and nothing is streamed."
    if action != "start":
        return f"action must be status, start or stop, not {action!r}"
    state, detail = check()
    if state != "ready":
        return f"Not started: {state} ({detail}). Fix that first - the portal was not opened."
    if proc is not None and proc.poll() is None:
        return f"Already sharing on {detail}."
    _PROC["proc"] = None                      # the old one has finished; forget it

    from shani_link import screen_stream
    # The same question again, and both the sentence and the stream come from
    # this answer: a cable that blinks between two probes must not leave the
    # page naming one road and the process taking the other.
    transport, where = screen_stream.choose()
    detail = road_detail(transport, where)
    package_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = dict(os.environ)
    env["PYTHONPATH"] = package_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["SHANI_LINK_SCREEN_TRANSPORT"] = transport
    env["SHANI_LINK_SCREEN_TOUCH"] = "1" if _TOUCH["on"] else "0"
    try:
        log = log_path()
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "w", encoding="utf-8") as handle:
            _PROC["proc"] = subprocess.Popen([sys.executable, "-m", "shani_link.screen_stream"],
                                             stdout=handle, stderr=subprocess.STDOUT, env=env)
    except OSError as exc:
        return f"The stream would not start ({exc})."
    _PROC["where"] = detail
    return (f"Sharing the screen on {detail}. Open that address on the phone. "
            "The desktop asked you first and will ask again next time."
            + (" Fingers, a stylus and the keyboard on the phone can drive this computer."
               if _TOUCH["on"] else ""))


def _status() -> str:
    """Whether it can share, whether it is sharing, and - if it stopped - why."""
    proc = _PROC.get("proc")
    if proc is not None:
        if proc.poll() is None:
            return f"Sharing the screen on {_PROC.get('where') or 'the phone'} right now."
        _PROC["proc"] = None
        said = last_words()
        return ("The stream has stopped" + (f": {said}." if said else ".")).replace("..", ".")
    state, detail = check()
    if state == "ready":
        return f"The screen can be shared: {detail}."
    return f"The screen cannot be shared right now ({state}): {detail}."


class _ShareSurface:
    def __init__(self, app: Any, set_content) -> None:
        self._app = app
        self._body = common.page_body()
        for side in ("start", "end", "top", "bottom"):
            getattr(self._body, f"set_margin_{side}")(12)
        set_content(common.scrolled(self._body))

        self._group = common.group()
        self._body.append(self._group)
        self._row = common.row("Share this screen with your phone",
                               "Turn on USB debugging and connect by cable or Wi-Fi, "
                               "or turn on the phone's Bluetooth tethering. You choose "
                               "what to share.",
                               suffix=self._buttons())
        self._group.add(self._row)
        #: What the last action said, in full - a truncated one-line label is not
        #: a report somebody can act on.
        self._report = common.row("What happened last", "(nothing yet)")
        _report_group = common.group()
        _report_group.add(self._report)
        _report_group.set_visible(False)          # until something has happened
        self._report_group = _report_group
        self._body.append(_report_group)

        # Off until pressed: a stream that swallowed every tap would be worse
        # than one that does not, and a palm on the phone is not a click.
        self._touch = common.switch_row(
            "Touch and type on the phone",
            "Fingers, a stylus and the phone's keyboard on this computer "
            "(needs the input group)",
            active=False,
            on_changed=lambda state: _TOUCH.__setitem__("on", bool(state)))
        self._body.append(self._touch)

        self._busy = False

    def _buttons(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        # The fix for the most common failure, one click away: a phone that is
        # not reachable yet. Connect a device does the USB and Wi-Fi handshake
        # (QR code or pairing code) that Check can only report as missing.
        self._connect = common.connect_button(self._app, "android", label="Connect a phone")
        self._connect.set_tooltip_text("Pair the phone over a cable or Wi-Fi - by QR code or pairing code")
        box.append(self._connect)

        self._check = Gtk.Button(label="Check")
        self._check.set_tooltip_text("See whether this computer can share its screen now (can take a minute)")
        self._check.connect("clicked", lambda _b: self._run("status"))

        self._start = Gtk.Button(label="Start")
        self._start.set_tooltip_text("Open the portal and stream to the paired phone")
        self._start.connect("clicked", lambda _b: self._run("start"))

        self._stop = Gtk.Button(label="Stop")
        self._stop.set_tooltip_text("Close the portal session and stop streaming")
        self._stop.connect("clicked", lambda _b: self._run("stop"))

        for widget in (self._check, self._start, self._stop):
            box.append(widget)
        return box

    # -- the controls --------------------------------------------------------

    def _run(self, action: str) -> None:
        """One action, off the drawing thread; the answer goes on the page."""
        if self._busy:
            return
        self._busy = True
        for widget in (self._check, self._start, self._stop):
            widget.set_sensitive(False)
        self._say({"status": "Checking…", "start": "Starting…", "stop": "Stopping…"}.get(action, f"{action}…"))

        def work():
            try:
                text = (act(action) or "").strip() or f"{action} produced no answer"
            except Exception as exc:  # noqa: BLE001 - a worker must still say something
                text = f"{action} failed: {type(exc).__name__}: {exc}"
            GLib.idle_add(lambda: (self._finished(action, text), False)[1])

        threading.Thread(target=work, daemon=True,
                         name="link-screen-share").start()

    def _finished(self, action: str, text: str) -> None:
        self._busy = False
        for widget in (self._check, self._start, self._stop):
            widget.set_sensitive(True)
        self._say(text)

    def _say(self, text: str) -> None:
        """The last answer, in full - and the panel is re-read on every showing.

        A worker's report is the only evidence a person gets from a 90-second
        probe, so a truncated label would hide the part that names the missing
        package.
        """
        self._report.set_subtitle(str(text))
        self._report_group.set_visible(True)


def build(app: Any) -> Gtk.Widget:
    page, set_content = common.surface(TITLE, SUBTITLE)
    surface = _ShareSurface(app, set_content)
    page.surface = surface
    # **A word from the vocabulary, not an empty one.** An unknown word
    # renders an uncoloured dot, which is the failure
    # `common.status_row`'s closed vocabulary exists to prevent.
    page.status = lambda: common.STATUS_UNKNOWN
    return page


__all__ = ["TITLE", "ICON", "build", "act", "check"]

"""One clipboard with the paired phone (KDE Connect's clipboard plugin), while
the person has "Share the clipboard with your phone" on (settings.KEYS:
`phone-clipboard-sync-enabled`, off by default: a clipboard holds passwords
and private things, so sharing it is a choice).

Both ways:
  - what is copied here goes to the phone: `wl-paste --watch` says the
    clipboard changed, the text is read and sent over the paired link
    (`lan_link.send_clipboard`). Text only, up to 64 KB, and **never what a
    password manager marks as secret** (the `x-kde-passwordManagerHint`
    type KeePassXC and others offer: `secret`).
  - what the phone copies comes in through `lan_link.Receiver`
    (`kdeconnect.clipboard`), goes on this clipboard with `wl-copy`, and is
    written down in `clipboard-origin.json` so the shell's clipboard history
    can say "from <phone>".
What came from the phone is not sent back to it (the watch fires for it too).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time

from shani_link import lan_link, paths

logger = logging.getLogger(__name__)
KEY = "phone-clipboard-sync-enabled"
MAX_BYTES = 64 * 1024
SECRET_TYPE = "x-kde-passwordManagerHint"
POLL_SECONDS = 5
ORIGINS_KEPT = 50
WL_PASTE = os.environ.get("SHANI_WL_PASTE", "wl-paste")
WL_COPY = os.environ.get("SHANI_WL_COPY", "wl-copy")


def text_key(text: str) -> str:
    """How the shell finds an item's origin: its first 60 characters, whitespace
    collapsed. cliphist's list shows only the start of an entry, so a hash of the
    whole text could not be matched there; the start can (Services/Clipboard.qml
    normalises the same way)."""
    return " ".join(text.split())[:60]


def origins_file():
    return paths.state_home() / "shani-link" / "clipboard-origin.json"


def record_origin(text: str, device: str) -> None:
    f = origins_file()
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[text_key(text)] = {"device": device, "at": int(time.time())}
    newest = sorted(data.items(), key=lambda kv: kv[1].get("at", 0))[-ORIGINS_KEPT:]
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(newest)), encoding="utf-8")
    tmp.replace(f)


class ClipboardSync:
    def __init__(self, settings, send=None, copy=None) -> None:
        self.settings = settings
        self._send = send or (lambda text: lan_link.send_clipboard(self._host(), text))
        self._copy = copy or self._wl_copy
        self._stop = threading.Event()
        self._from_phone = ""             # the last text the phone sent: not echoed back
        self._last_sent = ""
        self._watch_proc: "subprocess.Popen | None" = None

    # ---- in: the phone copied something --------------------------------
    def received(self, text: str) -> None:
        if not self.enabled() or len(text.encode("utf-8", "replace")) > MAX_BYTES:
            return
        self._from_phone = text
        self._copy(text)
        record_origin(text, lan_link.paired_device().get("deviceName") or "your phone")

    def _wl_copy(self, text: str) -> None:
        try:
            subprocess.run([WL_COPY], input=text.encode("utf-8"), timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.info("could not set the clipboard: %s", exc)

    # ---- out: something was copied here ---------------------------------
    def enabled(self) -> bool:
        return bool(self.settings.get_bool(KEY, False)) and bool(lan_link.paired_device().get("deviceId"))

    @staticmethod
    def _host() -> str:
        d = lan_link.paired_device()
        return d.get("address", "")

    def changed(self) -> "tuple[bool, str]":
        """The clipboard here changed: send it, unless it must not go. `(sent, why-not)`."""
        if not self.enabled():
            return False, "off"
        try:
            types = subprocess.run([WL_PASTE, "--list-types"], capture_output=True, text=True, timeout=5).stdout.split()
        except (OSError, subprocess.TimeoutExpired):
            return False, "no clipboard"
        if SECRET_TYPE in types:
            return False, "a password manager marked it secret"
        if not any(t.startswith("text/") or t in ("UTF8_STRING", "STRING", "TEXT") for t in types):
            return False, "not text"
        try:
            raw = subprocess.run([WL_PASTE, "--no-newline", "--type", "text"], capture_output=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            return False, "no clipboard"
        if not raw or len(raw) > MAX_BYTES:
            return False, "empty or too big"
        text = raw.decode("utf-8", "replace")
        if text in (self._from_phone, self._last_sent):
            return False, "came from the phone, or was just sent"
        sent, why = self._send(text)
        if sent:
            self._last_sent = text
        return sent, why

    def start(self) -> None:
        threading.Thread(target=self._watch, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._watch_proc is not None:
            self._watch_proc.terminate()

    def _watch(self) -> None:
        """`wl-paste --watch` prints a line each time the clipboard changes;
        started while the switch is on, stopped when it goes off."""
        while not self._stop.is_set():
            if not self.enabled():
                self._stop.wait(POLL_SECONDS)
                continue
            try:
                self._watch_proc = subprocess.Popen([WL_PASTE, "--watch", "echo", "changed"],
                                                    stdout=subprocess.PIPE, text=True)
            except OSError as exc:
                logger.info("cannot watch the clipboard: %s", exc)
                self._stop.wait(60)
                continue
            for _line in self._watch_proc.stdout:
                if self._stop.is_set() or not self.enabled():
                    break
                sent, why = self.changed()
                if not sent and why not in ("off", "came from the phone, or was just sent"):
                    logger.info("clipboard not sent to the phone: %s", why)
            self._watch_proc.terminate()
            self._watch_proc = None
            self._stop.wait(POLL_SECONDS)

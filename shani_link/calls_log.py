"""Every call on every line, in one list: the landline, the phone, the modem.

What GNOME Calls' history did: who, which way, whether it was missed, how long,
when - grouped by day - with call back, message, add to contacts, delete one,
clear all. The rows are written by whatever saw the call end, which is always
the service (shani-link): the landline facade, the Bluetooth phone's watcher
and the modem's watcher. The app and D-Bus clients read the same file.

Kept in `$XDG_DATA_HOME/shani-link/calls.json`, newest first, at most
`MAX_ROWS`. **Two processes write it** (the service logs a call, the app deletes
one), so every change is read-modify-write under an `flock` and lands with an
atomic rename - a reader never sees half a file, and two writers never lose
each other's row.

A missed call also tells whoever registered with `on_missed` (the service's
missed-call notification), at the moment it is logged.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Dict, List, Optional

from shani_link import paths

logger = logging.getLogger(__name__)

MAX_ROWS = 500
#: What each line is called where a person reads it.
LINE_NAMES = {"landline": "Landline", "phone": "Phone", "modem": "Mobile"}

_missed: List[Callable[[dict], None]] = []
_logged: List[Callable[[dict], None]] = []
_guard = threading.Lock()


def path() -> Path:
    return paths.data_home() / "shani-link" / "calls.json"


def _old_landline_file() -> Path:
    # through `migrated`, so a list still in Chronoa's folder is found as well
    from shani_link.landline import provision
    return provision.migrated(paths.data_home() / "shani-link" / "landline-calls.json")


@contextmanager
def _locked():
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with _guard, open(str(target) + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _read() -> List[dict]:
    try:
        rows = json.loads(path().read_text())
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    except FileNotFoundError:
        return _carry_over()
    except (OSError, ValueError):
        return []


def _carry_over() -> List[dict]:
    """The landline kept its own list before this one; its rows join once."""
    try:
        old = json.loads(_old_landline_file().read_text())
    except (OSError, ValueError):
        return []
    rows = []
    for r in old if isinstance(old, list) else []:
        if not isinstance(r, dict):
            continue
        kind = r.get("kind", "placed")
        rows.append({"id": uuid.uuid4().hex, "line": "landline", "number": str(r.get("number", "")),
                     "name": str(r.get("name", "")), "direction": "out" if kind == "placed" else "in",
                     "missed": kind == "missed", "when": int(r.get("when", 0) or 0),
                     "seconds": int(r.get("seconds", 0) or 0)})
    return rows


def _write(rows: List[dict]) -> None:
    target = path()
    fd, tmp = tempfile.mkstemp(prefix=".calls-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(rows[:MAX_ROWS], fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)                 # who called whom is private
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def rows(limit: int = 0, line: str = "") -> List[dict]:
    """Calls newest first: {id, line, number, name, direction ("in"/"out"),
    missed, when (epoch seconds, when it started), seconds (talking time)}."""
    got = sorted(_read(), key=lambda r: r.get("when", 0), reverse=True)
    if line:
        got = [r for r in got if r.get("line") == line]
    return got[:limit] if limit > 0 else got


def add(line: str, number: str, direction: str, answered: bool, seconds: int = 0,
        name: str = "", when: Optional[float] = None) -> dict:
    """Log one finished call. An incoming call nobody answered is a missed call."""
    row = {"id": uuid.uuid4().hex, "line": line, "number": str(number or ""), "name": str(name or ""),
           "direction": "in" if direction == "in" else "out",
           "missed": direction == "in" and not answered,
           "when": int(when if when is not None else time.time() - max(0, int(seconds))),
           "seconds": max(0, int(seconds)) if answered else 0}
    try:
        with _locked():
            current = _read()
            current.insert(0, row)
            _write(current)
    except OSError:
        logger.warning("could not log the call", exc_info=True)
    for callback in list(_logged) + (list(_missed) if row["missed"] else []):
        try:
            callback(row)
        except Exception:  # noqa: BLE001 - a broken notifier is not a lost call
            logger.exception("call-log listener failed")
    return row


def delete(row_id: str) -> bool:
    with _locked():
        current = _read()
        kept = [r for r in current if r.get("id") != row_id]
        if len(kept) == len(current):
            return False
        _write(kept)
        return True


def clear() -> None:
    with _locked():
        _write([])


def restore(old: List[dict]) -> None:
    """Put rows back (Undo after Clear all), keeping any logged since."""
    with _locked():
        current = _read()
        have = {r.get("id") for r in current}
        merged = current + [r for r in old if r.get("id") not in have]
        _write(sorted(merged, key=lambda r: r.get("when", 0), reverse=True))


def on_logged(callback: Callable[[dict], None]) -> Callable[[], None]:
    """`callback(row)` for every call as it is logged (the service's CallLogged
    signal, so an open history refreshes); returns the undo."""
    _logged.append(callback)
    return lambda: callback in _logged and _logged.remove(callback)


def on_missed(callback: Callable[[dict], None]) -> Callable[[], None]:
    """`callback(row)` for each missed call as it is logged; returns the undo."""
    _missed.append(callback)
    return lambda: callback in _missed and _missed.remove(callback)


# --- how a row reads ----------------------------------------------------------------

def day_label(when: float, now: Optional[float] = None) -> str:
    """"Today", "Yesterday", a weekday within the week, else the date."""
    now = time.time() if now is None else now
    day = time.localtime(when)
    today = time.localtime(now)
    days = (time.mktime(today[:3] + (0, 0, 0, 0, 0, -1)) - time.mktime(day[:3] + (0, 0, 0, 0, 0, -1))) / 86400
    days = round(days)
    if days <= 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 7:
        return time.strftime("%A", day)
    if day.tm_year == today.tm_year:
        return time.strftime("%-d %B", day)
    return time.strftime("%-d %B %Y", day)


def duration(seconds: int) -> str:
    """"45 s", "3 min 05 s", "1 h 02 min"."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min {seconds % 60:02d} s"
    return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"


def describe(row: dict) -> str:
    """The second line of a history row: what kind, on which line, when, how long."""
    if row.get("missed"):
        what = "Missed"
    else:
        what = "Incoming" if row.get("direction") == "in" else "Outgoing"
    line = row.get("line", "")
    if str(line).startswith("sip-"):        # a SIP account's name, not its id ("sip-3f2a1b")
        try:
            from shani_link import sip_accounts
            line = sip_accounts.name_for_line(line) or "SIP"
        except Exception:  # noqa: BLE001 - a row outlives the account it was logged on
            line = "SIP"
    bits = [what, LINE_NAMES.get(line, line),
            time.strftime("%H:%M", time.localtime(row.get("when", 0)))]
    if not row.get("missed") and row.get("seconds"):
        bits.append(duration(row["seconds"]))
    return " · ".join(b for b in bits if b)


def grouped(items: List[dict], now: Optional[float] = None) -> Dict[str, List[dict]]:
    """Rows under their day's heading, in the order they came (newest first)."""
    out: Dict[str, List[dict]] = {}
    for r in items:
        out.setdefault(day_label(r.get("when", 0), now), []).append(r)
    return out

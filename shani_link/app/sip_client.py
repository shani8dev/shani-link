"""SIP accounts, as the app reaches them: a client of the service.

The accounts themselves - registration, calls - run in shani-link's service
(dev.shani.Link on the session bus, /dev/shani/Link/Calls), so they ring with
every window closed, exactly like the landline (`app/line.py` is that pattern
for the Jio line). Passwords never cross this bus: the service answers with
accounts that have none, and takes new ones by JSON.

A failure the service reports comes back as a RuntimeError carrying the
service's own sentence.
"""
from __future__ import annotations

import json

BUS_NAME = "dev.shani.Link"
PATH = "/dev/shani/Link/Calls"
IFACE = "dev.shani.Link.Calls"


def _call(method: str, args=None, sig: str = "", timeout_ms: int = 10000):
    """One call to the service; the service's own sentence on failure."""
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    params = GLib.Variant(f"({sig})", tuple(args)) if sig else None
    try:
        reply = bus.call_sync(BUS_NAME, PATH, IFACE, method, params, None,
                              Gio.DBusCallFlags.NONE, timeout_ms, None)
    except GLib.Error as exc:
        msg = exc.message.split(": ", 1)[-1] if "dev.shani.Link.Error" in exc.message else \
            "The phone line service (shani-link) is not running."
        raise RuntimeError(msg) from None
    out = reply.unpack() if reply is not None else ()
    return out[0] if out else None


def _quiet(method: str, default, *a, **kw):
    try:
        return _call(method, *a, **kw)
    except RuntimeError:
        return default


# --- the accounts -----------------------------------------------------------------

def accounts() -> "list[dict]":
    """Every saved SIP account (never with a password)."""
    return json.loads(_quiet("SipAccounts", "[]") or "[]")


def add_account(fields: dict) -> dict:
    return json.loads(_call("SipAccountAdd", [json.dumps(fields)], "s"))


def update_account(account_id: str, fields: dict) -> dict:
    return json.loads(_call("SipAccountUpdate", [account_id, json.dumps(fields)], "ss"))


def remove_account(account_id: str) -> bool:
    return bool(_call("SipAccountRemove", [account_id], "s"))


# --- calls on a SIP line ----------------------------------------------------------------

def line_id(account: dict) -> str:
    return "sip-" + str(account.get("id") or "")


def dial(line: str, number: str) -> str:
    return _call("Dial", [line, number], "ss")


def answer(line: str) -> bool:
    return bool(_call("Answer", [line], "s"))


def hangup(line: str) -> bool:
    return bool(_call("Hangup", [line], "s"))


def dtmf(line: str, digit: str) -> bool:
    return bool(_quiet("SendDtmf", False, [line, digit], "ss"))

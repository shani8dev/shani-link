"""Use the phone's internet over Bluetooth (tethering), through NetworkManager.

The phone shares its mobile data as a Bluetooth network (PAN); this computer
joins it as a PAN user. NetworkManager already does the work - a connection of
type `bluetooth`, `bt-type panu`, for the phone's address - so this module only
finds or makes that connection and brings it up or down with `nmcli`:

    nmcli connection add type bluetooth bt-type panu bluetooth.bdaddr <MAC> con-name "<phone> internet"
    nmcli connection up|down uuid <uuid>

The usual failure is the phone not sharing yet, so that is what a failure says,
with where the switch is. Tests put `tests/fake_nmcli.py` first on PATH.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Optional

TIMEOUT = 40

#: What to do when the phone would not share its connection.
HELP = ("Turn on Bluetooth tethering on the phone first - on Android: Settings › Network › "
        "Hotspot & tethering › Bluetooth tethering; on an iPhone: Settings › Personal Hotspot.")


class TetherError(Exception):
    """Tethering could not be switched; the message is a sentence to show."""


def available() -> bool:
    return shutil.which("nmcli") is not None


def _nmcli(*args: str, timeout: int = TIMEOUT) -> "tuple[int, str]":
    try:
        proc = subprocess.run(["nmcli", *args], capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return 127, "NetworkManager (nmcli) is not installed"
    except subprocess.TimeoutExpired:
        return 124, f"NetworkManager did not answer in {timeout}s"
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def _fields(line: str) -> "list[str]":
    """One `nmcli -t` line: fields split on unescaped colons, `\\:` unescaped."""
    return [f.replace("\\:", ":").replace("\\\\", "\\") for f in re.split(r"(?<!\\):", line)]


def _norm(address: str) -> str:
    return (address or "").strip().upper()


def find(address: str) -> "Optional[dict]":
    """The phone's PAN connection: {name, uuid, active}, or None if there is none."""
    code, out = _nmcli("-t", "-f", "NAME,UUID,TYPE,ACTIVE", "connection", "show")
    if code != 0:
        raise TetherError(out or "NetworkManager did not answer")
    for line in out.splitlines():
        parts = _fields(line)
        if len(parts) < 4 or parts[2] != "bluetooth":
            continue
        name, uuid, _type, active = parts[:4]
        c2, props = _nmcli("-g", "bluetooth.bdaddr,bluetooth.type", "connection", "show", "uuid", uuid)
        if c2 != 0:
            continue
        values = props.splitlines() + ["", ""]
        if _norm(values[0].replace("\\:", ":")) == _norm(address) and values[1] == "panu":
            return {"name": name, "uuid": uuid, "active": active == "yes"}
    return None


def state(address: str, phone: str = "") -> dict:
    """{address, phone, on, connection} - whether this computer is using the phone's internet."""
    found = find(address) if available() else None
    return {"address": _norm(address), "phone": phone or "", "on": bool(found and found["active"]),
            "connection": found["name"] if found else ""}


def _create(address: str, phone: str) -> dict:
    name = f"{phone or 'Phone'} internet"
    code, out = _nmcli("connection", "add", "type", "bluetooth", "bt-type", "panu",
                       "bluetooth.bdaddr", _norm(address), "con-name", name)
    if code != 0:
        raise TetherError(f"Could not set up {name}: {out}")
    found = find(address)
    if found is None:
        raise TetherError(f"NetworkManager did not keep {name}")
    return found


def tether(address: str, on: bool, phone: str = "") -> str:
    """Start or stop using the phone's internet. Returns what happened, in words;
    raises `TetherError` with what to do when it could not."""
    if not _norm(address):
        raise TetherError("No phone paired over Bluetooth")
    if not available():
        raise TetherError("NetworkManager (nmcli) is not installed")
    who = phone or "the phone"
    found = find(address)
    if not on:
        if found is None or not found["active"]:
            return f"Not using {who}'s internet"
        code, out = _nmcli("connection", "down", "uuid", found["uuid"])
        if code != 0:
            raise TetherError(f"Could not stop: {out}")
        return f"Stopped using {who}'s internet"
    if found is None:
        found = _create(address, phone)
    if found["active"]:
        return f"Connected through {who}"
    code, out = _nmcli("connection", "up", "uuid", found["uuid"])
    if code != 0:
        raise TetherError(HELP)
    return f"Connected through {who}"

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


# --- the same internet over a cable, or over Wi-Fi -----------------------------

#: a USB network device: RNDIS from a phone is named usbN, or enx<MAC>
_USB_DEVICE = re.compile(r"^(?:usb\d+|enx[0-9a-f]+)$")

USB_HELP = ("Turn on USB tethering on the phone - Settings › Network › Hotspot & "
            "tethering › USB tethering - and plug it in.")


def usb_device() -> "Optional[str]":
    """The phone's USB network device, or None when there is no cable data link.

    NetworkManager lists an RNDIS phone as ethernet; the name is what tells it
    from the computer's own socket: a phone is `usb0` or `enx<MAC>`.
    """
    if not available():
        return None
    code, out = _nmcli("-t", "-f", "DEVICE,TYPE,STATE", "device", "status")
    if code != 0:
        return None
    for line in out.splitlines():
        parts = _fields(line)
        if len(parts) >= 2 and _USB_DEVICE.match(parts[0]) and parts[1] == "ethernet":
            return parts[0]
    return None


def usb(on: bool = True) -> str:
    """Use the phone's internet over a USB cable, or stop. The cable needs no
    pairing and no address: NetworkManager brings the phone's DHCP up and the
    phone becomes the router."""
    if not available():
        raise TetherError("NetworkManager (nmcli) is not installed")
    device = usb_device()
    if device is None:
        raise TetherError("No phone on a USB data connection. " + USB_HELP)
    code, out = _nmcli("device", "connect" if on else "disconnect", device)
    if code != 0:
        raise TetherError(f"Could not {'connect' if on else 'disconnect'} {device}: {out}")
    if not on:
        return f"Disconnected {device}; the phone's internet is no longer routed here."
    return (f"Connected {device} - the phone's internet is available here. "
            "The Wi-Fi may still be the default route; check in the browser.")


def usb_state() -> dict:
    """{device, on} - the phone's USB data link, and whether it is up."""
    device = usb_device()
    if device is None:
        return {"device": "", "on": False}
    code, out = _nmcli("-t", "-f", "DEVICE,STATE", "device", "status")
    on = False
    if code == 0:
        for line in out.splitlines():
            parts = _fields(line)
            if parts and parts[0] == device and len(parts) > 1:
                on = parts[1].startswith("connected")
    return {"device": device, "on": on}


def hotspot() -> str:
    """Whether this computer is sharing its internet as a Wi-Fi access point.

    The shell's hotspot tile runs `nmcli device wifi hotspot`; this reads the
    graph back, so a page can say which network a phone should join without
    turning the hotspot on from somewhere else.
    """
    if not available():
        return "NetworkManager (nmcli) is not installed."
    code, out = _nmcli("-t", "-f", "NAME,UUID,TYPE", "connection", "show", "--active")
    if code != 0:
        return "Could not read the active connections from NetworkManager."
    for line in out.splitlines():
        parts = _fields(line)
        if len(parts) < 3 or parts[2] != "802-11-wireless":
            continue
        c2, mode = _nmcli("-g", "802-11-wireless.mode", "connection", "show",
                          "uuid", parts[1])
        if c2 == 0 and mode.strip() == "ap":
            return f"This computer's hotspot is on: {parts[0]}."
    return "This computer's hotspot is off."


def wifi() -> str:
    """The Wi-Fi this computer is on, and what the phone's hotspot needs.

    The hotspot itself cannot be turned on from here - `cmd wifi` has no
    hotspot verb and the soft access point's passphrase is not readable over
    adb - so this reports the state and says what to join.
    """
    code, out = _nmcli("-t", "-f", "active,ssid", "dev", "wifi")
    if code != 0:
        return "Could not read the Wi-Fi state from NetworkManager."
    live = [line.split(":", 1)[1] for line in out.splitlines()
            if line.startswith("yes:") and ":" in line]
    mine = hotspot()
    if mine.endswith("is off."):
        if not live:
            return ("The computer is not on Wi-Fi. To use the phone's hotspot, turn it on "
                    "on the phone and join it in the Wi-Fi settings.")
        return (f"On Wi-Fi: {live[0]}. To use the phone's hotspot, turn it on on the phone "
                "and join it in the Wi-Fi settings.")
    # the computer is the access point: its own SSID is what a phone joins
    return (f"{mine} A phone that joins it uses this computer's internet.")

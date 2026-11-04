"""A phone on the same Wi-Fi, over the KDE Connect LAN protocol: the actions.

`lan_link` speaks the protocol; this is what a person (the app's Phone link tab)
or Chronoa's `lan_phone` skill does with it - find phones, make this computer
findable, pair, send a file - each answered in a sentence. Moved from that
skill, which keeps only its consent (`lan-phone-enabled`) and calls `_run`.

**One remembered address.** A phone's address moves when its DHCP lease does,
and asking for it every time is the friction that makes a capability unused, so
the last one found is kept in ~/.local/state/kdeconnect/device-address and
`devices` overwrites it when the address changes. The tab and the skill read the
same file, so they never disagree about which phone they mean.
"""
from __future__ import annotations

import logging

from shani_link import lan_link, paths

logger = logging.getLogger(__name__)

ACTIONS = ("status", "devices", "announce", "pair", "send")


def _address_file():
    return paths.state_home() / "kdeconnect" / "device-address"


def default_host() -> str:
    """The phone last found on this network, or ""."""
    path = _address_file()
    try:
        return path.read_text(encoding="utf-8").strip() if path.is_file() else ""
    except OSError:
        return ""


def remember(address: str) -> None:
    if not address:
        return
    try:
        path = _address_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(address, encoding="utf-8")
    except OSError as exc:
        logger.info("could not remember the device address: %s", exc)


def status() -> str:
    state, why = lan_link.pairing_status()
    return (f"This computer speaks the KDE Connect LAN protocol "
            f"(version {lan_link.PROTOCOL_VERSION}, UDP {lan_link.UDP_PORT}). "
            f"Pairing: {state}, {why}.")


def announce() -> str:
    """Make this computer findable by a phone's KDE Connect app."""
    name = lan_link._hostname()
    did, why = lan_link.announce(name)
    if did:
        note = f" ({why})" if why else ""
        return (f"Announced this computer as '{name}' on the local network{note}. "
                f"A phone running KDE Connect on the same Wi-Fi will now offer to pair.")
    return f"Could not announce this computer ({why}). The network may not carry broadcast."


def send(path: str, host: str = "") -> str:
    """Offer one file to the phone at `host` (default: the remembered one)."""
    path = str(path or "").strip()
    if not path:
        return ("Which file? Give its path - 'send' puts one file on the phone, and an "
                "installer (.apk) is just a file, so it is offered the same way as a picture.")
    host = str(host or "").strip() or default_host()
    if not host:
        # **Not a socket error.** An empty host handed to the socket layer is
        # `gaierror: Name or service not known` - a resolver complaint about a
        # host nobody named. A phone not found yet is a different sentence.
        return ("No device yet. Run 'devices' first - it looks for what is on the network "
                "and remembers the one answer, then 'send' has somewhere to put a file.")
    sent, why = lan_link.send_file(host, path)
    if not sent:
        return f"Not sent ({why}). If the device says it is not paired, 'pair' says what that needs from you."
    return f"{why} was offered to the device"


def pair(host: str = "", timeout: float = 60.0) -> str:
    host = str(host or "").strip() or default_host()
    if not host:
        return "No device to pair with yet. Run 'devices' first, with the KDE Connect app open on the phone."
    state, why = lan_link.pair(host, timeout=timeout)
    if state == "paired":
        return f"Paired: {why}."
    if state == "waiting":
        return (f"Asked {host} to pair, and nobody answered on the phone in time ({why}). "
                f"Open KDE Connect on it and try again.")
    return f"Not paired ({state}): {why}."


def devices(seconds=5.0) -> str:
    """What announced itself on this network in `seconds` (1-30)."""
    try:
        seconds = float(seconds) if seconds not in (None, "") else 5.0
    except (TypeError, ValueError):
        seconds = 5.0
    seconds = min(max(seconds, 1.0), 30.0)
    found = lan_link.listen_once(seconds)
    if not found:
        return (f"No device announced itself on the network in {seconds:.0f} seconds. That is a "
                f"real answer, not a failure: either nothing is running the protocol here, or "
                f"this network drops broadcast (corporate and university Wi-Fi often do).")
    if len(found) == 1:
        remember(found[0]["address"])
    lines = [f"{len(found)} device(s) on the local network:"]
    for entry in found:
        lines.append(
            f"- {entry.get('deviceName') or '(no name)'} "
            f"[{entry.get('deviceType') or 'unknown type'}] "
            f"at {entry.get('address')}, protocol {entry.get('protocolVersion')}, "
            f"control port {entry.get('tcpPort', lan_link.UDP_PORT)}")
    lines.append("'pair' pairs with the remembered one; the phone asks the person to accept.")
    return "\n".join(lines)


def _run(arguments: dict) -> str:
    """The `lan_phone` tool's arguments (no consent here: Chronoa's skill asks first)."""
    action = (arguments.get("action") or "status").strip().lower()
    if action not in ACTIONS:
        return f"action must be one of {', '.join(ACTIONS)}; {action!r} is not a phone-link action"
    if action == "send":
        return send(arguments.get("path") or "", arguments.get("_host") or arguments.get("host") or "")
    if action == "pair":
        return pair(arguments.get("host") or "")
    if action == "status":
        return status()
    if action == "announce":
        return announce()
    return devices(arguments.get("seconds"))

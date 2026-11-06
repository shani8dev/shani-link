"""The lines a call can go out on, as one list: the landline, the phone, the modem.

GNOME Calls let you pick the "origin" of a call; this is that. Each line is
asked only when somebody wants to know (the dial pad opening, a D-Bus client
asking `Lines`), never on a timer:

- `landline` - the JioFiber line through the router, when it is registered;
- `phone`    - a phone connected over Bluetooth hands-free (PipeWire telephony);
- `modem`    - this computer's own mobile modem, when it can carry voice.

Used by the service (D-Bus `dev.shani.Link.Calls`, the missed-call "Call back")
with the landline facade in-process. The app has its own dial pad logic, which
reaches the landline through the service instead.
"""
from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger(__name__)

#: id -> what a person reads
NAMES = {"landline": "Landline", "phone": "Phone", "modem": "Mobile"}


def _landline():
    from shani_link import landline
    return landline


def _phone_ready() -> bool:
    try:
        from shani_link import bt_telephony
        return bool(bt_telephony.gateways())
    except Exception:  # noqa: BLE001 - no PipeWire telephony is no phone line
        return False


def available() -> List[dict]:
    """[{id, name, ready}] for every line this computer has at all; `ready` says
    whether a call could go out on it now."""
    out = []
    ll = _landline()
    if ll.is_set_up():
        out.append({"id": "landline", "name": NAMES["landline"], "ready": bool(ll.ready())})
    phone = _phone_ready()
    if phone:
        out.append({"id": "phone", "name": NAMES["phone"], "ready": True})
    from shani_link import mm_voice
    if mm_voice.available():
        out.append({"id": "modem", "name": NAMES["modem"], "ready": True})
    from shani_link import sip_accounts
    for saved in sip_accounts.accounts():
        line = sip_accounts.line_id(saved)
        out.append({"id": line, "name": saved.get("name") or saved.get("username") or "SIP",
                    "ready": sip_accounts.ready(line)})
    return out


def _name(line: str) -> str:
    name = NAMES.get(line)
    if name:
        return name
    from shani_link import sip_accounts
    found = sip_accounts.name_for_line(line)
    if found:
        return found
    raise ValueError(f"there is no line called {line!r} (landline, phone, modem or a SIP account)")


def pick(line: str = "") -> str:
    """`line` when it is ready; with none named, the first that is ready."""
    lines = available()
    ready = [l["id"] for l in lines if l["ready"]]
    if line:
        if line in NAMES:
            if line not in ready:
                raise RuntimeError(f"the {NAMES[line].lower()} line is not connected")
            return line
        if line not in {l["id"] for l in lines}:
            raise ValueError(f"there is no line called {line!r} (landline, phone, modem or a SIP account)")
        raise RuntimeError(f"the {_name(line).lower()} line is not connected")
    if not ready:
        raise RuntimeError("no line is connected: set up the landline, connect a phone, "
                           "use a computer with a mobile modem, or add a SIP account")
    return ready[0]


def dial(line: str, number: str) -> str:
    """Place a call; returns what was dialled, in words."""
    line = pick(line)
    if line == "landline":
        return f"Calling {_landline().dial(number)} on the landline."
    if line.startswith("sip-"):
        from shani_link import sip_accounts
        return sip_accounts.dial(line, number)
    if line == "phone":
        from shani_link import bt_telephony
        return bt_telephony._run({"action": "dial", "number": number})
    from shani_link import mm_voice
    mm_voice.dial(number)
    return f"Calling {number} on the mobile modem."


def answer(line: str) -> bool:
    if line.startswith("sip-"):
        from shani_link import sip_accounts
        return sip_accounts.answer(line)
    if line == "landline":
        _landline().answer()
        return True
    if line == "phone":
        from shani_link import bt_telephony
        return bt_telephony._run({"action": "answer"}).startswith("Answered")
    from shani_link import mm_voice
    return mm_voice.answer()


def hangup(line: str) -> bool:
    if line.startswith("sip-"):
        from shani_link import sip_accounts
        return sip_accounts.hangup(line)
    if line == "landline":
        _landline().hangup()
        return True
    if line == "phone":
        from shani_link import bt_telephony
        return bt_telephony._run({"action": "hangup"}).startswith("Hung up")
    from shani_link import mm_voice
    return mm_voice.hangup()


def dtmf(line: str, digit: str) -> bool:
    if line.startswith("sip-"):
        from shani_link import sip_accounts
        return sip_accounts.dtmf(line, digit)
    if line == "landline":
        return bool(_landline().dtmf(digit))
    if line == "modem":
        from shani_link import mm_voice
        return mm_voice.dtmf(digit)
    raise RuntimeError("keypad tones through the phone are sent from the phone itself")

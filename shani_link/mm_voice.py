"""Voice calls through a laptop's own mobile modem, as GNOME Calls' "mm" provider did.

Some laptops have a WWAN card with a SIM. ModemManager (system bus,
`org.freedesktop.ModemManager1`) exposes the ones that can carry voice with an
extra interface on the modem, `Modem.Voice`:

- `CreateCall({"number": s}) -> o` makes a call object, and the call's own
  `Start()` dials it (two steps, as ModemManager defines it);
- a ringing call appears by itself (`CallAdded`), and its `Accept()` answers;
- `Hangup()` ends one call, `SendDtmf(s)` keys a digit into a live one;
- each call reports `StateChanged(old, new, reason)` and is deleted from the
  modem (`DeleteCall`) once it has ended, as GNOME Calls does, so the modem's
  list does not grow forever.

**It only appears where it exists.** `modems()` is empty on a machine with no
voice-capable modem (most), and the dial pad then offers no "Mobile" line.
Hold is not offered: ModemManager's hold (`HoldAndAccept`) only exists to swap
in a waiting call, which is not what a Hold button promises.

`ModemWatcher` runs in the service and does what the Bluetooth phone's watcher
does - the same incoming-call card (it *is* an `IncomingCallWatcher`, fed by
ModemManager's signals instead of PipeWire's), and each finished call into the
shared call history. It only subscribes to signals: nothing polls.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from shani_link import incoming_call

logger = logging.getLogger(__name__)

MM = "org.freedesktop.ModemManager1"
ROOT = "/org/freedesktop/ModemManager1"
MODEM = MM + ".Modem"
VOICE = MM + ".Modem.Voice"
CALL = MM + ".Call"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
PROPS = "org.freedesktop.DBus.Properties"

#: MMCallState
STATES = {0: "unknown", 1: "dialing", 2: "ringing-out", 3: "ringing-in", 4: "active",
          5: "held", 6: "waiting", 7: "terminated"}
#: MMCallDirection
INCOMING, OUTGOING = 1, 2
#: The switch for the modem's ringing card with no window open (Background).
SWITCH = "modem-call-enabled"


class ModemError(RuntimeError):
    """No modem, or ModemManager said no - with its reason."""


def _bus():
    """A fresh system-bus connection: these calls are rare (a person pressing
    Call), and a connection made per call honours DBUS_SYSTEM_BUS_ADDRESS, which
    is how the tests put a fake ModemManager in its place."""
    from gi.repository import Gio
    address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SYSTEM, None)
    return Gio.DBusConnection.new_for_address_sync(
        address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)


def _call(bus, path: str, iface: str, method: str, params=None, reply: Optional[str] = None,
          timeout_ms: int = 10000):
    from gi.repository import GLib
    try:
        out = bus.call_sync(MM, path, iface, method, params,
                            GLib.VariantType.new(reply) if reply else None, 0, timeout_ms, None)
    except GLib.Error as exc:
        raise ModemError(exc.message.split(": ", 1)[-1]) from None
    return out.unpack() if out is not None else ()


def modems(bus=None) -> List[Dict[str, Any]]:
    """Voice-capable modems: [{path, name, number}]. [] with no ModemManager."""
    try:
        bus = bus or _bus()
        objects = _call(bus, ROOT, OBJECT_MANAGER, "GetManagedObjects", reply="(a{oa{sa{sv}}})")[0]
    except Exception:  # noqa: BLE001 - no ModemManager, no modem: not an error
        return []
    found = []
    for path, ifaces in sorted(objects.items()):
        if VOICE not in ifaces:
            continue
        info = ifaces.get(MODEM, {})
        name = " ".join(str(info.get(k, "")).strip() for k in ("Manufacturer", "Model")).strip()
        own = list(info.get("OwnNumbers", []) or [])
        found.append({"path": path, "name": name or "Mobile modem", "number": own[0] if own else ""})
    return found


def available(bus=None) -> bool:
    return bool(modems(bus))


def _call_props(bus, call_path: str) -> dict:
    from gi.repository import GLib
    props = _call(bus, call_path, PROPS, "GetAll", GLib.Variant("(s)", (CALL,)), "(a{sv})")[0]
    return {"path": call_path, "number": str(props.get("Number", "")),
            "state": STATES.get(int(props.get("State", 0)), "unknown"),
            "direction": "in" if int(props.get("Direction", 0)) == INCOMING else "out"}


def calls(bus=None) -> List[dict]:
    """Every call the modems hold: [{path, number, state, direction}]."""
    bus = bus or _bus()
    out = []
    for m in modems(bus):
        paths = _call(bus, m["path"], VOICE, "ListCalls", reply="(ao)")[0]
        for p in paths:
            try:
                out.append(_call_props(bus, p))
            except ModemError:
                continue
    return out


def _first_modem(bus) -> str:
    found = modems(bus)
    if not found:
        raise ModemError("there is no mobile modem that can make calls")
    return found[0]["path"]


def dial(number: str, bus=None) -> str:
    """Place a call: CreateCall, then Start. Returns the call's object path."""
    from gi.repository import GLib
    number = "".join(ch for ch in (number or "") if ch.isdigit() or ch in "+*#")
    if not number:
        raise ModemError("there is no number to call")
    bus = bus or _bus()
    modem = _first_modem(bus)
    call = _call(bus, modem, VOICE, "CreateCall",
                 GLib.Variant("(a{sv})", ({"number": GLib.Variant("s", number)},)), "(o)")[0]
    _call(bus, call, CALL, "Start", timeout_ms=60000)
    return call


def _pick(bus, states) -> str:
    for c in calls(bus):
        if c["state"] in states:
            return c["path"]
    return ""


def answer(bus=None) -> bool:
    bus = bus or _bus()
    call = _pick(bus, ("ringing-in", "waiting"))
    if not call:
        return False
    _call(bus, call, CALL, "Accept")
    return True


def hangup(bus=None) -> bool:
    """End the live call (or the one ringing, or the one being dialled)."""
    bus = bus or _bus()
    call = _pick(bus, ("active", "dialing", "ringing-out", "ringing-in", "waiting", "held"))
    if not call:
        return False
    _call(bus, call, CALL, "Hangup")
    return True


def dtmf(digit: str, bus=None) -> bool:
    if not digit or any(ch not in "0123456789*#ABCD" for ch in digit):
        return False
    bus = bus or _bus()
    call = _pick(bus, ("active",))
    if not call:
        return False
    from gi.repository import GLib
    _call(bus, call, CALL, "SendDtmf", GLib.Variant("(s)", (digit,)))
    return True


class ModemWatcher(incoming_call.IncomingCallWatcher):
    """The modem's ringing card and call log, on the same thread design as the
    phone's watcher (its own main context; works with every window closed).

    The card - Answer / Decline, closed when the call ends - is the parent's;
    this only translates ModemManager into what the parent understands, and
    answers through ModemManager instead of PipeWire.
    """

    call_key = SWITCH
    call_key_default = True     # a laptop's own phone rings unless switched off

    def __init__(self, config: Any, bus_factory: Optional[Callable[[], Any]] = None,
                 system_factory: Optional[Callable[[], Any]] = None):
        super().__init__(config, bus_factory=bus_factory)
        self._system_factory = system_factory or _bus
        self._system = None
        self._live: Dict[str, dict] = {}

    def _subscribe(self, bus, flags) -> list:
        """ModemManager is on the system bus: its signals come on a second connection."""
        self._system = self._system_factory()
        sub = self._system.signal_subscribe
        self._system_subs = [
            sub(MM, VOICE, "CallAdded", None, None, flags, self._mm_added),
            sub(MM, CALL, "StateChanged", None, None, flags, self._mm_state),
        ]
        return []

    def refresh_contacts(self) -> None:
        """The phone's watcher pulls the phone's book; the modem uses the cache."""

    def _unsubscribe(self) -> None:
        for s in getattr(self, "_system_subs", []):
            self._system.signal_unsubscribe(s)

    def _mm_added(self, _conn, _sender, _path, _iface, _signal, params) -> None:
        call = str(params.unpack()[0])
        try:
            info = _call_props(self._system, call)
        except ModemError:
            return
        self._live[call] = {"number": info["number"], "direction": info["direction"],
                            "answered": None}
        if info["state"] in ("ringing-in", "waiting"):
            self.ringing(call, {"State": "incoming" if info["state"] == "ringing-in" else "waiting",
                                "LineIdentification": info["number"]})

    def _mm_state(self, _conn, _sender, path, _iface, _signal, params) -> None:
        _old, new, _reason = params.unpack()
        state = STATES.get(int(new), "unknown")
        call = str(path)
        live = self._live.setdefault(call, {"number": "", "direction": "out", "answered": None})
        if state == "active" and live["answered"] is None:
            live["answered"] = time.monotonic()
        if state not in ("ringing-in", "waiting"):
            self.ended(call)
        if state == "terminated":
            self._finish(call)

    def _finish(self, call: str) -> None:
        live = self._live.pop(call, None)
        if live is None:
            return
        if not live["number"]:
            try:
                live["number"] = _call_props(self._system, call)["number"]
            except ModemError:
                pass
        from shani_link import calls_log, contacts
        seconds = int(time.monotonic() - live["answered"]) if live["answered"] else 0
        calls_log.add("modem", live["number"], live["direction"], live["answered"] is not None,
                      seconds, name=contacts.name_for(live["number"]))
        try:            # the modem keeps every ended call until asked to forget it
            from gi.repository import GLib
            modem = call.rsplit("/Call/", 1)[0]
            modem = next((m["path"] for m in modems(self._system)), modem)
            _call(self._system, modem, VOICE, "DeleteCall", GLib.Variant("(o)", (call,)))
        except Exception:  # noqa: BLE001 - already gone
            pass

    def act(self, call: str, action: str) -> None:
        if not self._allowed(self.call_key, self.call_key_default):
            return
        try:
            _call(self._system, call, CALL, "Accept" if action == "answer" else "Hangup")
        except ModemError:
            logger.info("modem call: %s failed", action, exc_info=True)
        self.ended(call)

"""shani-link: the computer's phone line, phone and watch as one user service.

Owns `dev.shani.Link` on the session bus. Each device is an object; for now the
JioFiber landline, at /dev/shani/Link/Landline (interface dev.shani.Link.Landline):
every call the line's facade has, and an `Event(kind, info_json)` signal for
what happens on it ("incoming", "ringing", "answered", "ended", "registered",
"unregistered", ...). Anything on the desktop - the shell's call card and
dialer, Chronoa's skill and Phone page, a GNOME or Plasma panel - talks to the
line here, so it rings with every window closed and has one owner.

Two more objects serve the rest of the desktop (the shell's dialer and call
card, Chronoa's skills): /dev/shani/Link/Contacts (dev.shani.Link.Contacts:
search the address book, name a number) and /dev/shani/Link/Calls
(dev.shani.Link.Calls: the lines, the shared call history, dial / answer /
hang up on any line). JSON strings carry the records, as `Calls` already did.
/dev/shani/Link/Phone (dev.shani.Link.Phone) switches using the phone's
internet over Bluetooth (`tether`).

Video goes over a local socket instead of D-Bus (frames are big and many):
`VideoSocket()` names it; each frame is a 12-byte header (width, height,
length, big-endian u32) and that many bytes of RGB24.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import struct
import threading

from gi.repository import Gio, GLib

from shani_link import landline

logger = logging.getLogger("shani-link")

BUS_NAME = "dev.shani.Link"
PATH = "/dev/shani/Link/Landline"
IFACE = "dev.shani.Link.Landline"
ROOT_PATH = "/dev/shani/Link"
ROOT_IFACE = "dev.shani.Link"

# the service's own object: its switches, and what the devices report (the
# watch's voice button, a file arriving, ...) for whoever is listening
ROOT_XML = f"""
<node>
  <interface name="{ROOT_IFACE}">
    <method name="Settings"><arg type="s" direction="out"/></method>
    <method name="SetSetting"><arg name="key" type="s"/><arg name="on" type="b"/></method>
    <signal name="DeviceEvent"><arg name="source" type="s"/><arg name="kind" type="s"/><arg name="info_json" type="s"/></signal>
  </interface>
</node>
"""

XML = f"""
<node>
  <interface name="{IFACE}">
    <method name="IsSetUp"><arg type="b" direction="out"/></method>
    <method name="SetupStart"><arg name="host" type="s"/><arg type="s" direction="out"/></method>
    <method name="SetupFinish"><arg name="code" type="s"/><arg type="s" direction="out"/></method>
    <method name="Forget"/>
    <method name="Enabled"><arg type="b" direction="out"/></method>
    <method name="SetEnabled"><arg name="on" type="b"/></method>
    <method name="Status"><arg type="s" direction="out"/></method>
    <method name="Ready"><arg type="b" direction="out"/></method>
    <method name="Dial"><arg name="number" type="s"/><arg type="s" direction="out"/></method>
    <method name="Answer"/>
    <method name="Hangup"/>
    <method name="Dtmf"><arg name="digit" type="s"/><arg type="b" direction="out"/></method>
    <method name="SetMuted"><arg name="on" type="b"/><arg type="b" direction="out"/></method>
    <method name="Muted"><arg type="b" direction="out"/></method>
    <method name="InCall"><arg type="b" direction="out"/></method>
    <method name="Hold"><arg type="b" direction="out"/></method>
    <method name="Swap"><arg type="b" direction="out"/></method>
    <method name="Merge"><arg type="b" direction="out"/></method>
    <method name="Calls"><arg type="s" direction="out"/></method>
    <method name="History"><arg type="s" direction="out"/></method>
    <method name="ResolveName"><arg name="name" type="s"/><arg type="s" direction="out"/></method>
    <method name="CacheContacts"><arg name="book_json" type="s"/></method>
    <method name="VideoAvailable"><arg type="b" direction="out"/></method>
    <method name="VideoSocket"><arg type="s" direction="out"/></method>
    <signal name="Event"><arg name="kind" type="s"/><arg name="info_json" type="s"/></signal>
  </interface>
</node>
"""


CONTACTS_PATH = "/dev/shani/Link/Contacts"
CONTACTS_IFACE = "dev.shani.Link.Contacts"
CALLS_PATH = "/dev/shani/Link/Calls"
CALLS_IFACE = "dev.shani.Link.Calls"

PHONE_PATH = "/dev/shani/Link/Phone"
PHONE_IFACE = "dev.shani.Link.Phone"

PHONE_XML = f"""
<node>
  <interface name="{PHONE_IFACE}">
    <method name="Tether"><arg name="address" type="s"/><arg name="on" type="b"/><arg type="s" direction="out"/></method>
    <method name="TetherState"><arg name="address" type="s"/><arg type="s" direction="out"/></method>
    <method name="UsbTether"><arg name="on" type="b"/><arg type="s" direction="out"/></method>
    <method name="UsbTetherState"><arg type="s" direction="out"/></method>
    <method name="WifiTether"><arg type="s" direction="out"/></method>
    <method name="Notifications"><arg name="limit" type="i"/><arg type="s" direction="out"/></method>
    <method name="Usage"><arg name="limit" type="i"/><arg type="s" direction="out"/></method>
    <method name="Line"><arg type="s" direction="out"/></method>
    <method name="Wifi"><arg type="s" direction="out"/></method>
    <method name="Thermal"><arg type="s" direction="out"/></method>
    <method name="Location"><arg type="s" direction="out"/></method>
    <method name="Display"><arg type="s" direction="out"/></method>
  </interface>
</node>
"""

CONTACTS_XML = f"""
<node>
  <interface name="{CONTACTS_IFACE}">
    <method name="Search"><arg name="query" type="s"/><arg name="limit" type="i"/><arg type="s" direction="out"/></method>
    <method name="Lookup"><arg name="number" type="s"/><arg type="s" direction="out"/></method>
  </interface>
</node>
"""

CALLS_XML = f"""
<node>
  <interface name="{CALLS_IFACE}">
    <method name="Lines"><arg type="s" direction="out"/></method>
    <method name="Dial"><arg name="line" type="s"/><arg name="number" type="s"/><arg type="s" direction="out"/></method>
    <method name="Answer"><arg name="line" type="s"/><arg type="b" direction="out"/></method>
    <method name="Hangup"><arg name="line" type="s"/><arg type="b" direction="out"/></method>
    <method name="SendDtmf"><arg name="line" type="s"/><arg name="digit" type="s"/><arg type="b" direction="out"/></method>
    <method name="RecentCalls"><arg name="limit" type="i"/><arg type="s" direction="out"/></method>
    <method name="DeleteCall"><arg name="id" type="s"/><arg type="b" direction="out"/></method>
    <method name="ClearCalls"/>
    <method name="SipAccounts"><arg type="s" direction="out"/></method>
    <method name="SipAccountAdd"><arg name="fields_json" type="s"/><arg type="s" direction="out"/></method>
    <method name="SipAccountUpdate"><arg name="id" type="s"/><arg name="fields_json" type="s"/><arg type="s" direction="out"/></method>
    <method name="SipAccountRemove"><arg name="id" type="s"/><arg type="b" direction="out"/></method>
    <signal name="CallLogged"><arg name="row_json" type="s"/></signal>
  </interface>
</node>
"""


def _json(value) -> str:
    return json.dumps(value, default=str)


def call_view(call) -> dict:
    """One live call as JSON can carry it. The engine's `Call` is an object (its
    SIP dialog, its RTP session); `json.dumps(default=str)` turned each into a
    string like "<Call object at 0x...>", so a client could read nothing of it."""
    return {"number": getattr(call, "number", ""), "name": getattr(call, "name", ""),
            "state": getattr(call, "state", ""), "held": bool(getattr(call, "held", False)),
            "outgoing": bool(getattr(call, "outgoing", False))}


class VideoFanout:
    """Decoded pictures to every viewer connected to the socket."""

    def __init__(self, path: str):
        self.path = path
        self.clients: list[socket.socket] = []
        self.lock = threading.Lock()
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        os.chmod(path, 0o600)
        self.server.listen(4)
        threading.Thread(target=self._accept, daemon=True, name="video-accept").start()

    def _accept(self) -> None:
        while True:
            try:
                client, _ = self.server.accept()
            except OSError:
                return
            with self.lock:
                self.clients.append(client)

    def frame(self, rgb: bytes, width: int, height: int) -> None:
        packet = struct.pack(">III", width, height, len(rgb)) + rgb
        with self.lock:
            for c in list(self.clients):
                try:
                    c.sendall(packet)
                except OSError:
                    self.clients.remove(c)
                    c.close()


class Service:
    def __init__(self, conn: Gio.DBusConnection):
        from shani_link.settings import Settings
        self.conn = conn
        self.settings = Settings()
        root = Gio.DBusNodeInfo.new_for_xml(ROOT_XML)
        conn.register_object(ROOT_PATH, root.interfaces[0], self._root_call, None, None)
        runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        self.video = VideoFanout(os.path.join(runtime, "shani-link", "video.sock"))
        landline.video_sink(self.video.frame)
        landline.listen(self._on_event)
        from shani_link import sip_accounts
        sip_accounts.listen(lambda kind, **info: self.device_event("sip", kind, **info))
        node = Gio.DBusNodeInfo.new_for_xml(XML)
        conn.register_object(PATH, node.interfaces[0], self._call, None, None)
        for xml, path, handler in ((CONTACTS_XML, CONTACTS_PATH, self._contacts_call),
                                   (CALLS_XML, CALLS_PATH, self._calls_call),
                                   (PHONE_XML, PHONE_PATH, self._phone_call)):
            conn.register_object(path, Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0],
                                 handler, None, None)
        # Every logged call goes out as CallLogged, so an open history refreshes;
        # a missed one also gets its notification. Rows are logged on the lines'
        # own threads: both hand over to the main loop.
        from shani_link import calls_log
        from shani_link.missed_call import MissedCallNotifier
        self.missed = MissedCallNotifier(conn)
        calls_log.on_logged(lambda row: GLib.idle_add(self._emit_logged, row))
        calls_log.on_missed(lambda row: GLib.idle_add(lambda: (self.missed.post(row), False)[1]))

    def _emit_logged(self, row: dict) -> bool:
        self.conn.emit_signal(None, CALLS_PATH, CALLS_IFACE, "CallLogged",
                              GLib.Variant("(s)", (_json(row),)))
        return False

    def _answer(self, invocation, fn) -> None:
        try:
            out = fn()
        except Exception as exc:  # noqa: BLE001 - every failure is the caller's answer
            invocation.return_dbus_error("dev.shani.Link.Error", str(exc))
            return
        invocation.return_value(out)

    def _contacts_call(self, _conn, _sender, _path, _iface, method, params, invocation) -> None:
        from shani_link import contacts
        a = params.unpack()
        s = lambda v: GLib.Variant("(s)", (_json(v),))

        def run():
            if method == "Search":
                return s([contacts.as_json(c) for c in contacts.search(a[0], int(a[1]))])
            if method == "Lookup":
                _name, found = contacts.lookup(a[0])
                return s(contacts.as_json(found) if found is not None else {})
            raise ValueError(f"no method {method}")
        self._answer(invocation, run)

    def _calls_call(self, _conn, _sender, _path, _iface, method, params, invocation) -> None:
        from shani_link import calls_log, lines
        a = params.unpack()
        b = lambda v: GLib.Variant("(b)", (bool(v),))

        def run():
            if method == "Lines":
                return GLib.Variant("(s)", (_json(lines.available()),))
            if method == "Dial":
                return GLib.Variant("(s)", (lines.dial(a[0], a[1]),))
            if method == "Answer":
                return b(lines.answer(a[0]))
            if method == "Hangup":
                return b(lines.hangup(a[0]))
            if method == "SendDtmf":
                return b(lines.dtmf(a[0], a[1]))
            if method == "RecentCalls":
                from shani_link import contacts   # a row logged before the book was read is named now
                return GLib.Variant("(s)", (_json(contacts.named(calls_log.rows(int(a[0])))),))
            if method == "DeleteCall":
                return b(calls_log.delete(a[0]))
            if method == "ClearCalls":
                calls_log.clear()
                return None
            if method == "SipAccounts":
                from shani_link import sip_accounts
                return GLib.Variant("(s)", (_json(sip_accounts.accounts()),))
            if method == "SipAccountAdd":
                from shani_link import sip_accounts
                return GLib.Variant("(s)", (_json(sip_accounts.add_account(json.loads(a[0] or "{}"))),))
            if method == "SipAccountUpdate":
                from shani_link import sip_accounts
                return GLib.Variant("(s)", (_json(sip_accounts.update_account(a[0], json.loads(a[1] or "{}"))),))
            if method == "SipAccountRemove":
                from shani_link import sip_accounts
                return b(sip_accounts.remove_account(a[0]))
            raise ValueError(f"no method {method}")
        self._answer(invocation, run)

    def _phone_call(self, _conn, _sender, _path, _iface, method, params, invocation) -> None:
        """The phone's internet over Bluetooth (`tether`), and what the phone
        reports about itself over adb. An empty address is the phone paired over
        Bluetooth; its name comes from BlueZ."""
        from shani_link import phone_bluez, tether
        a = params.unpack()

        def phone(address: str) -> "tuple[str, str]":
            paired = phone_bluez.phones()
            if not address:
                hit = next((p for p in paired if p[2]), paired[0] if paired else None)
                if hit is None:
                    raise ValueError("No phone is paired over Bluetooth")
                return hit[0], hit[1]
            return address, next((n for ad, n, _c in paired if ad.upper() == address.upper()), "")

        def run():
            if method in ("Notifications", "Usage", "Line", "Wifi", "Thermal", "Location", "Display"):
                # The reads are adb's, not Bluetooth's: a cable or Wi-Fi
                # debugging. The Android page has no shell by design, so this is
                # where they are reachable - over the bus, for Chronoa's skills
                # and anything else.
                from shani_link import android
                if os.environ.get("SHANI_LINK_NO_DEVICES"):
                    # the service tests' bus has no phone, and must not borrow
                    # the developer's
                    raise ValueError("no Android device is connected on this test bus")
                serial, problem = android.ready_serial()
                if problem:
                    raise ValueError(problem)
                limit = a[0] if a and isinstance(a[0], int) else None
                said = {"Notifications": lambda: android.notifications(serial, limit or 20),
                        "Usage": lambda: android.usage(serial, limit or 10),
                        "Line": lambda: android.telephony(serial),
                        "Wifi": lambda: android.wifi(serial),
                        "Thermal": lambda: android.thermal(serial),
                        "Location": lambda: android.location(serial),
                        "Display": lambda: android.display(serial)}[method]()
                return GLib.Variant("(s)", (said,))
            if method == "Tether":
                address, name = phone(a[0])
                return GLib.Variant("(s)", (tether.tether(address, bool(a[1]), name),))
            if method == "TetherState":
                address, name = phone(a[0])
                return GLib.Variant("(s)", (_json(tether.state(address, name)),))
            if method == "UsbTether":
                return GLib.Variant("(s)", (tether.usb(bool(a[0])),))
            if method == "UsbTetherState":
                return GLib.Variant("(s)", (_json(tether.usb_state()),))
            if method == "WifiTether":
                return GLib.Variant("(s)", (tether.wifi(),))
            raise ValueError(f"no method {method}")
        self._answer(invocation, run)

    def device_event(self, source: str, kind: str, **info) -> None:
        GLib.idle_add(lambda: self.conn.emit_signal(None, ROOT_PATH, ROOT_IFACE, "DeviceEvent",
                                                    GLib.Variant("(sss)", (source, kind, _json(info)))) and False)

    def _root_call(self, _conn, _sender, _path, _iface, method, params, invocation) -> None:
        if method == "Settings":
            invocation.return_value(GLib.Variant("(s)", (_json(self.settings.all()),)))
        elif method == "SetSetting":
            key, on = params.unpack()
            self.settings.set_bool(key, on)
            invocation.return_value(None)
        else:
            invocation.return_dbus_error("dev.shani.Link.Error", f"no method {method}")

    def start_devices(self) -> None:
        """What runs with no window open: each one does nothing until its own
        switch is on (settings.py), exactly as when it ran inside Chronoa."""
        from shani_link.incoming_call import IncomingCallWatcher
        from shani_link.obex_receive import ObexReceiver
        from shani_link.watch_companion import WatchCompanion
        from shani_link.ble_peripheral import RemoteService
        from shani_link.map_notify import MessageWatcher
        from shani_link.mm_voice import ModemWatcher
        from shani_link.lan_receive import LanReceiver
        cfg = self.settings
        self.devices = [IncomingCallWatcher(cfg), ModemWatcher(cfg), ObexReceiver(cfg),
                        WatchCompanion(cfg, on_voice=lambda start: self.device_event("watch", "voice", start=start)),
                        RemoteService(cfg), MessageWatcher(cfg)]
        from shani_link.clipboard_sync import ClipboardSync
        clipboard = ClipboardSync(cfg)
        self.devices += [clipboard, LanReceiver(cfg, clipboard=clipboard)]
        if cfg.get_bool("bluetooth-media-remote-enabled", True):
            from shani_link.avrcp_target import AvrcpTarget
            self.devices.append(AvrcpTarget())
        for d in self.devices:
            try:
                d.start()
            except Exception:  # noqa: BLE001 - one device that cannot start is not the service
                logger.exception("could not start %s", type(d).__name__)

    # line events arrive on the engine's threads: the signal goes out on the main loop
    def _on_event(self, kind: str, **info) -> None:
        GLib.idle_add(self._emit, kind, _json(info))

    def _emit(self, kind: str, info: str) -> bool:
        self.conn.emit_signal(None, PATH, IFACE, "Event", GLib.Variant("(ss)", (kind, info)))
        return False

    def _call(self, _conn, _sender, _path, _iface, method, params, invocation) -> None:
        args = params.unpack()
        try:
            out = self._dispatch(method, args)
        except Exception as exc:  # noqa: BLE001 - every failure is the caller's answer
            invocation.return_dbus_error("dev.shani.Link.Error", str(exc))
            return
        invocation.return_value(out)

    def _dispatch(self, method: str, a: tuple):
        b = lambda v: GLib.Variant("(b)", (bool(v),))
        s = lambda v: GLib.Variant("(s)", (str(v),))
        if method == "IsSetUp":
            return b(landline.is_set_up())
        if method == "SetupStart":
            return s(landline.setup_start(a[0]))
        if method == "SetupFinish":
            out = landline.setup_finish(a[0])
            landline.start()
            return s(out)
        if method == "Forget":
            landline.stop()
            landline.forget()
            return None
        if method == "Enabled":
            return b(landline.enabled())
        if method == "SetEnabled":
            landline.set_enabled(a[0])
            return None
        if method == "Status":
            return s(landline.status())
        if method == "Ready":
            return b(landline.ready())
        if method == "Dial":
            return s(landline.dial(a[0]))
        if method == "Answer":
            landline.answer()
            return None
        if method == "Hangup":
            landline.hangup()
            return None
        if method == "Dtmf":
            return b(landline.dtmf(a[0]))
        if method == "SetMuted":
            return b(landline.set_muted(a[0]))
        if method == "Muted":
            return b(landline.muted())
        if method == "InCall":
            return b(landline.in_call())
        if method == "Hold":
            return b(landline.hold())
        if method == "Swap":
            return b(landline.swap())
        if method == "Merge":
            return b(landline.merge())
        if method == "Calls":
            return s(_json([call_view(c) for c in landline.calls()]))
        if method == "History":
            return s(_json(landline.history()))
        if method == "ResolveName":
            return s(landline.resolve_name(a[0]))
        if method == "CacheContacts":
            landline.cache_contacts(json.loads(a[0] or "[]"))
            return None
        if method == "VideoAvailable":
            return b(landline.video_available())
        if method == "VideoSocket":
            return s(self.video.path)
        raise ValueError(f"no method {method}")


def main() -> None:
    logging.basicConfig(level=os.environ.get("SHANI_LINK_LOG", "INFO"),
                        format="%(name)s: %(message)s")
    loop = GLib.MainLoop()
    holder = {}

    def acquired(conn, _name):
        holder["service"] = Service(conn)
        landline.start()          # nothing happens unless the line is set up and on
        from shani_link import sip_accounts
        sip_accounts.start_all()  # every enabled SIP account registers itself, quietly
        if not os.environ.get("SHANI_LINK_NO_DEVICES"):      # the tests' private bus has no BlueZ
            holder["service"].start_devices()
        logger.info("on the session bus as %s", BUS_NAME)

    def lost(_conn, _name):
        logger.error("%s is taken or the bus went away", BUS_NAME)
        loop.quit()

    Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                     acquired, None, lost)
    loop.run()


if __name__ == "__main__":
    main()

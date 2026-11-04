"""shani-link: the computer's phone line, phone and watch as one user service.

Owns `dev.shani.Link` on the session bus. Each device is an object; for now the
JioFiber landline, at /dev/shani/Link/Landline (interface dev.shani.Link.Landline):
every call the line's facade has, and an `Event(kind, info_json)` signal for
what happens on it ("incoming", "ringing", "answered", "ended", "registered",
"unregistered", ...). Anything on the desktop - the shell's call card and
dialer, Chronoa's skill and Phone page, a GNOME or Plasma panel - talks to the
line here, so it rings with every window closed and has one owner.

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


def _json(value) -> str:
    return json.dumps(value, default=str)


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
        self.conn = conn
        runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        self.video = VideoFanout(os.path.join(runtime, "shani-link", "video.sock"))
        landline.video_sink(self.video.frame)
        landline.listen(self._on_event)
        node = Gio.DBusNodeInfo.new_for_xml(XML)
        conn.register_object(PATH, node.interfaces[0], self._call, None, None)

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
            return s(_json(landline.calls()))
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
        logger.info("on the session bus as %s", BUS_NAME)

    def lost(_conn, _name):
        logger.error("%s is taken or the bus went away", BUS_NAME)
        loop.quit()

    Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                     acquired, None, lost)
    loop.run()


if __name__ == "__main__":
    main()

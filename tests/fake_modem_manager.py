"""A stand-in ModemManager with one voice-capable modem, for the tests' private bus.

Run as a program: `python3 fake_modem_manager.py ADDRESS [--no-voice]`. It owns
org.freedesktop.ModemManager1 on the bus at ADDRESS and answers what shani-link
asks of the real one: ObjectManager.GetManagedObjects, Modem.Voice (CreateCall,
ListCalls, DeleteCall; CallAdded) and each Call (Start, Accept, Hangup,
SendDtmf; properties; StateChanged). Calls change state the way a real modem's
do: Start -> dialing then active, Accept -> active, Hangup -> terminated.

A test drives it through its own interface, dev.shani.FakeModemManager:
Ring(number) makes an incoming call; Log() is every method called on it, as
JSON. (shani-shell's dev/fake-modem.py is a pipe stub for its own dev session,
not a ModemManager, so this is written fresh.)
"""
import json
import sys

from gi.repository import Gio, GLib

MM = "org.freedesktop.ModemManager1"
ROOT = "/org/freedesktop/ModemManager1"
MODEM_PATH = ROOT + "/Modem/0"

XML = """<node>
<interface name="org.freedesktop.DBus.ObjectManager">
  <method name="GetManagedObjects"><arg type="a{oa{sa{sv}}}" direction="out"/></method>
</interface>
<interface name="org.freedesktop.ModemManager1.Modem.Voice">
  <method name="CreateCall"><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
  <method name="ListCalls"><arg type="ao" direction="out"/></method>
  <method name="DeleteCall"><arg type="o" direction="in"/></method>
  <signal name="CallAdded"><arg type="o"/></signal>
  <signal name="CallDeleted"><arg type="o"/></signal>
</interface>
<interface name="org.freedesktop.ModemManager1.Call">
  <method name="Start"/><method name="Accept"/><method name="Hangup"/>
  <method name="SendDtmf"><arg type="s" direction="in"/></method>
  <signal name="StateChanged"><arg type="i"/><arg type="i"/><arg type="u"/></signal>
</interface>
<interface name="org.freedesktop.DBus.Properties">
  <method name="GetAll"><arg type="s" direction="in"/><arg type="a{sv}" direction="out"/></method>
</interface>
<interface name="dev.shani.FakeModemManager">
  <method name="Ring"><arg type="s" direction="in"/><arg type="o" direction="out"/></method>
  <method name="Log"><arg type="s" direction="out"/></method>
</interface>
</node>"""


class Fake:
    def __init__(self, bus, voice=True):
        self.bus, self.voice = bus, voice
        self.node = Gio.DBusNodeInfo.new_for_xml(XML)
        self.calls = {}           # path -> {number, state, direction}
        self.next = 0
        self.log = []
        self.regs = {}
        for path, ifaces in ((ROOT, ("org.freedesktop.DBus.ObjectManager", "dev.shani.FakeModemManager")),
                             (MODEM_PATH, ("org.freedesktop.ModemManager1.Modem.Voice",))):
            for name in ifaces:
                bus.register_object(path, self.node.lookup_interface(name), self.on_call, None, None)

    def _new_call(self, number, state, direction):
        path = f"{MODEM_PATH}/Call/{self.next}"
        self.next += 1
        self.calls[path] = {"number": number, "state": state, "direction": direction}
        self.regs[path] = [self.bus.register_object(path, self.node.lookup_interface(n), self.on_call, None, None)
                           for n in ("org.freedesktop.ModemManager1.Call", "org.freedesktop.DBus.Properties")]
        self.bus.emit_signal(None, MODEM_PATH, "org.freedesktop.ModemManager1.Modem.Voice", "CallAdded",
                             GLib.Variant("(o)", (path,)))
        return path

    def _state(self, path, new):
        old = self.calls[path]["state"]
        self.calls[path]["state"] = new
        self.bus.emit_signal(None, path, "org.freedesktop.ModemManager1.Call", "StateChanged",
                             GLib.Variant("(iiu)", (old, new, 0)))

    def on_call(self, _conn, _sender, path, iface, method, params, invocation):
        args = params.unpack()
        self.log.append([path, method, list(args) if not isinstance(args, dict) else args])
        ret = None
        if method == "GetManagedObjects":
            modem = {"org.freedesktop.ModemManager1.Modem": {
                "Manufacturer": GLib.Variant("s", "Quectel"), "Model": GLib.Variant("s", "EM05"),
                "OwnNumbers": GLib.Variant("as", ["+919000000000"])}}
            if self.voice:
                modem["org.freedesktop.ModemManager1.Modem.Voice"] = {
                    "Calls": GLib.Variant("ao", list(self.calls))}
            ret = GLib.Variant("(a{oa{sa{sv}}})", ({MODEM_PATH: modem},))
        elif method == "CreateCall":
            number = args[0]["number"]
            ret = GLib.Variant("(o)", (self._new_call(number, 0, 2),))
        elif method == "ListCalls":
            ret = GLib.Variant("(ao)", (list(self.calls),))
        elif method == "DeleteCall":
            self.calls.pop(args[0], None)
            for reg in self.regs.pop(args[0], []):
                self.bus.unregister_object(reg)
        elif method == "Start":
            self._state(path, 1)
            self._state(path, 4)
        elif method == "Accept":
            self._state(path, 4)
        elif method == "Hangup":
            self._state(path, 7)
        elif method == "GetAll":
            c = self.calls[path]
            ret = GLib.Variant("(a{sv})", ({"Number": GLib.Variant("s", c["number"]),
                                            "State": GLib.Variant("i", c["state"]),
                                            "Direction": GLib.Variant("i", c["direction"])},))
        elif method == "Ring":
            ret = GLib.Variant("(o)", (self._new_call(args[0], 3, 1),))
        elif method == "Log":
            ret = GLib.Variant("(s)", (json.dumps(self.log),))
        invocation.return_value(ret)


def main():
    address = sys.argv[1]
    bus = Gio.DBusConnection.new_for_address_sync(
        address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
    Fake(bus, voice="--no-voice" not in sys.argv)
    bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "RequestName",
                  GLib.Variant("(su)", (MM, 4)), None, 0, 2000, None)
    print("ready", flush=True)
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()

"""A missed call's notification: who, on which line, and Call back / Message.

A stand-in notification server on a private dbus-daemon, never the session bus.
"""
import os
import subprocess
import threading
import time

import pytest

gi = pytest.importorskip("gi")
from gi.repository import Gio, GLib  # noqa: E402

from shani_link import calls_log, contacts  # noqa: E402
from shani_link.missed_call import MissedCallNotifier, NOTIFY  # noqa: E402

XML = """<node><interface name="org.freedesktop.Notifications">
  <method name="Notify"><arg type="s" direction="in"/><arg type="u" direction="in"/>
    <arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/>
    <arg type="as" direction="in"/><arg type="a{sv}" direction="in"/><arg type="i" direction="in"/>
    <arg type="u" direction="out"/></method>
</interface></node>"""


def _connect(address):
    return Gio.DBusConnection.new_for_address_sync(
        address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)


@pytest.fixture()
def server():
    proc = subprocess.Popen(["dbus-daemon", "--session", "--fork", "--print-address", "--print-pid"],
                            stdout=subprocess.PIPE, text=True)
    address, pid = proc.stdout.readline().strip(), proc.stdout.readline().strip()
    proc.wait()
    notes = []
    held = {}
    ready = threading.Event()

    # The server answers on a thread of its own: the notifier's Notify is a
    # blocking call from this thread, which could not be answered here.
    def serve():
        ctx = GLib.MainContext.new()
        ctx.push_thread_default()
        srv = _connect(address)

        def on_call(_c, _s, _p, _i, method, params, invocation):
            notes.append(params.unpack())
            invocation.return_value(GLib.Variant("(u)", (len(notes),)))
        srv.register_object(NOTIFY[1], Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], on_call, None, None)
        srv.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "RequestName",
                      GLib.Variant("(su)", (NOTIFY[0], 4)), None, 0, 2000, None)
        held["srv"], held["loop"] = srv, GLib.MainLoop.new(ctx, False)
        ready.set()
        held["loop"].run()
    threading.Thread(target=serve, daemon=True).start()
    ready.wait(5)
    srv = held["srv"]
    yield address, srv, notes
    held["loop"].quit()
    os.kill(int(pid), 15)


def _pump(cond, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not cond():
        GLib.MainContext.default().iteration(False)
        time.sleep(0.01)
    return cond()


def _press(srv, nid, key):
    srv.emit_signal(None, NOTIFY[1], NOTIFY[2], "ActionInvoked", GLib.Variant("(us)", (nid, key)))
    srv.flush_sync(None)


def test_a_missed_call_names_the_caller_and_offers_call_back_and_message(server):
    address, srv, notes = server
    contacts.book().save(contacts.Contact(full_name="Amma", phones=[("cell", "09800000001")]))
    dialled, launched = [], []
    try:
        notifier = MissedCallNotifier(_connect(address), dial=lambda l, n: dialled.append((l, n)),
                                      launch=launched.append)
        undo = calls_log.on_missed(notifier.post)
        calls_log.add("landline", "+919800000001", "in", False)
        undo()
        assert _pump(lambda: notes)
        _app, _id, icon, title, body, actions, hints, _t = notes[0]
        assert title == "Missed call: Amma" and "Landline" in body and "+919800000001" in body
        assert actions == ["call-back", "Call back", "message", "Message"]
        assert hints["category"] == "call.unanswered" and icon == "call-missed-symbolic"
        _press(srv, 1, "call-back")
        assert _pump(lambda: dialled) and dialled == [("landline", "+919800000001")]
        nid = notifier.post({"line": "phone", "number": "+912", "when": 0})
        _press(srv, nid, "message")
        assert _pump(lambda: launched) and launched == [["--message", "+912"]]
    finally:
        contacts.book().delete([c.uid for c in contacts.book().all()])


def test_call_back_on_a_line_that_is_gone_opens_the_dial_pad_instead(server):
    address, srv, _notes = server
    launched = []

    def refuse(_line, _number):
        raise RuntimeError("the landline is not connected")
    notifier = MissedCallNotifier(_connect(address), dial=refuse, launch=launched.append)
    nid = notifier.post({"line": "landline", "number": "+913", "when": 0})
    _press(srv, nid, "call-back")
    assert _pump(lambda: launched) and launched == [["--dial", "+913"]]


def test_a_withheld_number_has_no_buttons_that_cannot_work(server):
    address, _srv, notes = server
    notifier = MissedCallNotifier(_connect(address), dial=lambda *a: None, launch=lambda a: None)
    notifier.post({"line": "modem", "number": "", "when": 0})
    assert _pump(lambda: notes)
    assert notes[0][3] == "Missed call: Unknown caller" and notes[0][5] == []

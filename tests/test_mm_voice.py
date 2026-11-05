"""Calls through the computer's own mobile modem, against a fake ModemManager.

The fake (fake_modem_manager.py) runs as its own process on a private
dbus-daemon that stands in for the system bus; the notification server is a
stand-in on the same bus. Nothing reaches a real modem or the real buses.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
from gi.repository import Gio, GLib  # noqa: E402

from shani_link import calls_log, contacts, mm_voice  # noqa: E402

HERE = Path(__file__).resolve().parent
NOTIFY = "org.freedesktop.Notifications"
NOTIFY_PATH = "/org/freedesktop/Notifications"
NOTIFY_XML = """<node><interface name="org.freedesktop.Notifications">
  <method name="Notify"><arg type="s" direction="in"/><arg type="u" direction="in"/>
    <arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/>
    <arg type="as" direction="in"/><arg type="a{sv}" direction="in"/><arg type="i" direction="in"/>
    <arg type="u" direction="out"/></method>
  <method name="CloseNotification"><arg type="u" direction="in"/></method>
</interface></node>"""


def _connect(address):
    return Gio.DBusConnection.new_for_address_sync(
        address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)


def _start(extra=()):
    proc = subprocess.Popen(["dbus-daemon", "--session", "--fork", "--print-address", "--print-pid"],
                            stdout=subprocess.PIPE, text=True)
    address, pid = proc.stdout.readline().strip(), proc.stdout.readline().strip()
    proc.wait()
    fake = subprocess.Popen([sys.executable, str(HERE / "fake_modem_manager.py"), address, *extra],
                            stdout=subprocess.PIPE, text=True)
    assert fake.stdout.readline().strip() == "ready"
    return address, pid, fake


@pytest.fixture()
def modem(monkeypatch):
    address, pid, fake = _start()
    monkeypatch.setenv("DBUS_SYSTEM_BUS_ADDRESS", address)
    yield address
    fake.terminate()
    fake.wait()
    os.kill(int(pid), 15)


def _log(address):
    bus = _connect(address)
    out = bus.call_sync(mm_voice.MM, mm_voice.ROOT, "dev.shani.FakeModemManager", "Log", None,
                        GLib.VariantType.new("(s)"), 0, 3000, None)
    return [(p, m, a) for p, m, a in json.loads(out.unpack()[0])]


def _ring(address, number):
    bus = _connect(address)
    return bus.call_sync(mm_voice.MM, mm_voice.ROOT, "dev.shani.FakeModemManager", "Ring",
                         GLib.Variant("(s)", (number,)), GLib.VariantType.new("(o)"), 0, 3000,
                         None).unpack()[0]


def test_a_voice_modem_is_found_and_named(modem):
    found = mm_voice.modems()
    assert found == [{"path": "/org/freedesktop/ModemManager1/Modem/0", "name": "Quectel EM05",
                      "number": "+919000000000"}]
    assert mm_voice.available()


def test_a_modem_that_cannot_carry_voice_is_not_a_line(monkeypatch):
    address, pid, fake = _start(["--no-voice"])
    monkeypatch.setenv("DBUS_SYSTEM_BUS_ADDRESS", address)
    try:
        assert mm_voice.modems() == [] and not mm_voice.available()
        with pytest.raises(mm_voice.ModemError):
            mm_voice.dial("100")
    finally:
        fake.terminate()
        os.kill(int(pid), 15)


def test_no_modem_manager_at_all_is_no_line_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv("DBUS_SYSTEM_BUS_ADDRESS", f"unix:path={tmp_path}/nothing")
    assert mm_voice.modems() == []


def test_dialling_creates_the_call_then_starts_it(modem):
    call = mm_voice.dial("+91 98000-00001")
    log = _log(modem)
    created = [a for _p, m, a in log if m == "CreateCall"]
    assert created and created[0][0]["number"] == "+919800000001"
    assert (call, "Start") in [(p, m) for p, m, _a in log]
    assert mm_voice.calls()[0]["state"] == "active"


def test_a_ringing_call_is_answered_keyed_and_hung_up(modem):
    call = _ring(modem, "+919800000002")
    assert [c["state"] for c in mm_voice.calls()] == ["ringing-in"]
    assert mm_voice.dtmf("5") is False, "a tone into a call nobody answered"
    assert mm_voice.answer()
    assert mm_voice.dtmf("5")
    assert mm_voice.hangup()
    methods = [(p, m, a) for p, m, a in _log(modem) if p == call]
    assert [m for _p, m, _a in methods if m != "GetAll"] == ["Accept", "SendDtmf", "Hangup"]
    assert ["5"] in [a for _p, m, a in methods if m == "SendDtmf"]


class Notifier:
    def __init__(self, address):
        self.bus = _connect(address)
        self.log = []
        node = Gio.DBusNodeInfo.new_for_xml(NOTIFY_XML)
        self.bus.register_object(NOTIFY_PATH, node.interfaces[0], self.on_call, None, None)
        self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                           "RequestName", GLib.Variant("(su)", (NOTIFY, 4)), None, 0, 2000, None)

    def on_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        self.log.append((method, params.unpack()))
        invocation.return_value(GLib.Variant("(u)", (9,)) if method == "Notify" else None)

    def wait_for(self, cond, seconds=5.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            GLib.MainContext.default().iteration(False)
            if cond():
                return True
            time.sleep(0.02)
        return cond()


class Config:
    def __init__(self, **keys):
        self.keys = keys

    def get_bool(self, key, default=False):
        return self.keys.get(key, default)


def test_the_modem_rings_with_the_same_card_and_logs_the_call(modem):
    contacts.book().save(contacts.Contact(full_name="Ravi", phones=[("cell", "09800000003")]))
    calls_log.clear()
    notes = Notifier(modem)
    watcher = mm_voice.ModemWatcher(Config(), bus_factory=lambda: _connect(modem),
                                    system_factory=lambda: _connect(modem))
    watcher.start()
    try:
        call = _ring(modem, "+919800000003")
        assert notes.wait_for(lambda: [p for m, p in notes.log if m == "Notify"]), notes.log
        note = [p for m, p in notes.log if m == "Notify"][0]
        assert note[3] == "Incoming call: Ravi" and note[5] == ["answer", "Answer", "decline", "Decline"]
        notes.bus.emit_signal(None, NOTIFY_PATH, NOTIFY, "ActionInvoked", GLib.Variant("(us)", (9, "decline")))
        notes.bus.flush_sync(None)
        assert notes.wait_for(lambda: calls_log.rows()), "the declined call never reached the history"
        row = calls_log.rows()[0]
        assert (row["line"], row["number"], row["direction"], row["missed"], row["name"]) == \
            ("modem", "+919800000003", "in", True, "Ravi")
        assert (call, "Hangup") in [(p, m) for p, m, _a in _log(modem)]
        assert notes.wait_for(lambda: [call] in [a for _p, m, a in _log(modem) if m == "DeleteCall"]), \
            "an ended call was left on the modem"
    finally:
        watcher.stop()
        contacts.book().delete([c.uid for c in contacts.book().all()])


def test_the_modems_card_stays_away_when_switched_off(modem):
    notes = Notifier(modem)
    watcher = mm_voice.ModemWatcher(Config(**{mm_voice.SWITCH: False}), bus_factory=lambda: _connect(modem),
                                    system_factory=lambda: _connect(modem))
    watcher.start()
    try:
        _ring(modem, "+919800000004")
        assert not notes.wait_for(lambda: [p for m, p in notes.log if m == "Notify"], 1.0)
    finally:
        watcher.stop()

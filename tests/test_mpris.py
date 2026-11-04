"""MPRIS players (shani_link/mpris.py) against a fake player on a *private* bus.

The player is a small Gio program implementing the Player methods and logging
each call, on a dbus-daemon this test starts and stops by its own recorded PID.
DBUS_SESSION_BUS_ADDRESS points at that daemon for the whole test, so the
person's real players are never seen, let alone moved. Ported from Shani
Chronoa's tests/test_media_seek.py when its `media_control` skill came to use
this module (the skill's wording is still tested there).
"""
import os
import shutil
import subprocess
import sys
import textwrap
import time

import pytest

from shani_link import mpris

pytestmark = pytest.mark.skipif(not shutil.which("dbus-daemon"), reason="needs dbus-daemon")

PLAYER = textwrap.dedent('''
    import os, sys
    from gi.repository import Gio, GLib
    LOG = sys.argv[1]
    CAN_SEEK = os.environ.get("FAKE_CAN_SEEK", "1") == "1"
    TRACK = os.environ.get("FAKE_TRACK", "/org/fake/track/7")
    REFUSE = os.environ.get("FAKE_REFUSE", "")
    pos = [int(os.environ.get("FAKE_POS", "60000000"))]
    XML = """<node>
      <interface name="org.mpris.MediaPlayer2"><property name="Identity" type="s" access="read"/></interface>
      <interface name="org.mpris.MediaPlayer2.Player">
        <method name="Seek"><arg name="Offset" type="x" direction="in"/></method>
        <method name="SetPosition"><arg name="TrackId" type="o" direction="in"/>
                                   <arg name="Position" type="x" direction="in"/></method>
        <method name="Next"/><method name="Previous"/><method name="Play"/><method name="Pause"/>
        <method name="PlayPause"/><method name="Stop"/>
        <property name="PlaybackStatus" type="s" access="read"/>
        <property name="Metadata" type="a{sv}" access="read"/>
        <property name="Position" type="x" access="read"/>
        <property name="CanSeek" type="b" access="read"/>
      </interface></node>"""
    def log(line):
        with open(LOG, "a") as f:
            f.write(line + "\\n")
    def call(conn, sender, path, iface, method, params, inv):
        log(method + " " + params.print_(True))
        if method == REFUSE:
            inv.return_dbus_error("org.mpris.MediaPlayer2.Error", "not now")
            return
        if method == "Seek":
            pos[0] = max(0, pos[0] + params.unpack()[0])
        elif method == "SetPosition" and params.unpack()[0] == TRACK:
            pos[0] = params.unpack()[1]
        inv.return_value(None)
    def get(conn, sender, path, iface, prop):
        if prop == "Identity": return GLib.Variant("s", "Fake Player")
        if prop == "PlaybackStatus": return GLib.Variant("s", "Playing")
        if prop == "CanSeek": return GLib.Variant("b", CAN_SEEK)
        if prop == "Position": return GLib.Variant("x", pos[0])
        meta = {"xesam:title": GLib.Variant("s", "Song"), "xesam:artist": GLib.Variant("as", ["Band", "Other"])}
        if TRACK:
            meta["mpris:trackid"] = GLib.Variant("o", TRACK)
        return GLib.Variant("a{sv}", meta)
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    info = Gio.DBusNodeInfo.new_for_xml(XML)
    for iface in info.interfaces:
        bus.register_object("/org/mpris/MediaPlayer2", iface, call, get, None)
    Gio.bus_own_name_on_connection(bus, "org.mpris.MediaPlayer2.fakeplayer.instance42", 0, None, None)
    GLib.MainLoop().run()
''')

NAME = "org.mpris.MediaPlayer2.fakeplayer.instance42"


@pytest.fixture
def private_bus(tmp_path, monkeypatch):
    daemon = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address=1",
                               f"--address=unix:path={tmp_path}/bus"],
                              stdout=subprocess.PIPE, text=True)
    address = daemon.stdout.readline().strip()
    assert address
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", address)
    started = []

    def start_player(**env):
        script = tmp_path / "player.py"
        script.write_text(PLAYER)
        log = tmp_path / "calls.log"
        proc = subprocess.Popen([sys.executable, str(script), str(log)],
                                env=dict(os.environ, DBUS_SESSION_BUS_ADDRESS=address, **env))
        started.append(proc)
        for _ in range(100):
            if mpris.players():
                break
            time.sleep(0.05)
        assert mpris.players() == [NAME]
        return lambda: log.read_text().splitlines() if log.exists() else []

    yield start_player
    for proc in started + [daemon]:   # recorded PIDs only
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    daemon.stdout.close()


def test_what_the_player_says_about_itself(private_bus):
    private_bus()
    assert mpris.current() == NAME
    assert mpris.identity(NAME) == "Fake Player"
    assert mpris.status(NAME) == "Playing"
    assert mpris.title_artist(NAME) == ("Song", "Band")
    assert mpris.now_playing(NAME) == "Band, Other - Song"
    assert mpris.can_seek(NAME) is True
    assert mpris.position(NAME) == 60_000_000


def test_seek_moves_by_the_offset_both_ways(private_bus):
    calls = private_bus()
    assert mpris.seek(NAME, 30_000_000) == (True, "")
    assert mpris.position(NAME) == 90_000_000
    assert mpris.seek(NAME, -10_000_000) == (True, "")
    assert mpris.position(NAME) == 80_000_000
    assert calls() == ["Seek (int64 30000000,)", "Seek (int64 -10000000,)"]


def test_restart_sets_position_zero_on_the_current_track(private_bus):
    calls = private_bus()
    assert mpris.restart(NAME) == (True, "")
    assert mpris.position(NAME) == 0
    assert calls() == ["SetPosition (objectpath '/org/fake/track/7', int64 0)"]


def test_restart_without_a_track_id_seeks_back_past_the_start(private_bus):
    calls = private_bus(FAKE_TRACK="")
    assert mpris.restart(NAME) == (True, "")
    assert calls() == ["Seek (int64 -61000000,)"]


def test_a_player_that_cannot_seek_says_so(private_bus):
    private_bus(FAKE_CAN_SEEK="0")
    assert mpris.can_seek(NAME) is False


def test_a_refusal_is_returned_in_the_players_words(private_bus):
    private_bus(FAKE_REFUSE="Seek")
    done, why = mpris.seek(NAME, 5_000_000)
    assert not done and "not now" in why


def test_control_presses_the_named_method(private_bus):
    calls = private_bus()
    assert mpris.control("previous") == "previous: Band, Other - Song"
    assert mpris.control("rewind") == "Unknown media action: rewind"
    assert calls() == ["Previous ()"]


def test_no_bus_is_no_players_not_an_error():
    # conftest.py points the session bus nowhere
    assert mpris.players() == []
    assert mpris.position("org.mpris.MediaPlayer2.none") is None
    assert mpris.identity("org.mpris.MediaPlayer2.none") == "none"


def test_the_bus_named_now_is_asked_not_the_first_one_reached(private_bus, tmp_path, monkeypatch):
    """Gio's singleton is whichever bus the process reached first, for as long as
    anything holds it (the service and the app do; in Chronoa's suite an earlier
    test did, and every player test after it found no players). A second private
    bus (with no player on it) must be what is asked once it is named."""
    from gi.repository import Gio
    private_bus()
    held = Gio.bus_get_sync(Gio.BusType.SESSION, None)   # what a long-lived process keeps
    assert mpris.players() == [NAME]
    other = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address=1",
                              f"--address=unix:path={tmp_path}/other"], stdout=subprocess.PIPE, text=True)
    try:
        monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", other.stdout.readline().strip())
        assert mpris.players() == []
    finally:
        held.close_sync(None)
        other.terminate()
        other.wait(timeout=5)
        other.stdout.close()

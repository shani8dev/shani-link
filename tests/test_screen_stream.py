"""The screen stream's own decisions: which road, what size, and what it does
when the picture stops.

No portal and no phone: adb is a fake `adb` first on PATH, and the parts tested
here are the ones the server consults. The server itself needs a desktop (a
PipeWire stream and a portal session to answer), which is exactly why every
decision it makes was moved out of it.
"""
import os
import stat

import pytest

from shani_link import screen_stream as ss


# --- the size the phone gets -------------------------------------------------

def test_fit_to_phone_turns_caps_and_makes_even():
    # 1080x2400 is portrait; the desktop goes in the long way round, and the
    # long side is what the cap is applied to
    assert ss.fit_to_phone(1080, 2400) == (1920, 864)
    # an override size that is not a whole multiple still lands on even pixels
    assert ss.fit_to_phone(1080, 2340) == (1920, 886)
    # nothing to do on a size that is already small and even
    assert ss.fit_to_phone(640, 360, max_long_side=2560) == (640, 360)
    # odd pixels are dropped, not rounded up: VP8 wants even dimensions
    assert ss.fit_to_phone(1001, 601, max_long_side=2000) == (1000, 600)
    # a phone that lies about being 1x1 still gets a usable frame
    assert ss.fit_to_phone(1, 1) == (2, 2)


# --- which road --------------------------------------------------------------

def test_choose_prefers_adb(monkeypatch):
    monkeypatch.setattr(ss, "phone_serial", lambda: ("R5CT10", ""))
    monkeypatch.setattr(ss, "bluetooth_address", lambda: "192.168.44.2")
    assert ss.choose() == ("usb", f"127.0.0.1:{ss.PORT}")


def test_a_phone_on_wi_fi_is_its_own_road(monkeypatch):
    """Wireless debugging is adb, not a cable: same tunnel, same loopback."""
    monkeypatch.setattr(ss, "phone_serial", lambda: ("192.168.1.20:41000", ""))
    assert ss.choose() == ("wifi", f"127.0.0.1:{ss.PORT}")


def test_without_adb_bluetooth_is_still_offered(monkeypatch):
    monkeypatch.setattr(ss, "phone_serial", lambda: ("", "no Android device is connected"))
    monkeypatch.setattr(ss, "bluetooth_address", lambda: "192.168.44.2")
    assert ss.choose() == ("bluetooth", f"192.168.44.2:{ss.PORT}")


def test_a_cable_wins_over_a_wireless_phone(monkeypatch):
    """Faster, and it does not go away when the Wi-Fi does."""
    from shani_link import android
    monkeypatch.setattr(android, "_devices", lambda: [("R5CT10", "device", "Pixel 8"),
                                                      ("192.168.1.20:41000", "device", "Pixel 8")])
    assert ss.phone_serial() == ("R5CT10", "")


def test_two_phones_on_cables_are_a_question_for_the_person(monkeypatch):
    from shani_link import android
    monkeypatch.setattr(android, "_devices", lambda: [("A", "device", ""), ("B", "device", "")])
    serial, problem = ss.phone_serial()
    assert serial == "" and "unplug one" in problem, problem


def test_the_page_can_settle_the_road_for_the_stream(monkeypatch):
    """What the page told the person and what the stream does have to agree."""
    monkeypatch.setattr(ss, "phone_serial", lambda: ("", "no phone"))
    monkeypatch.setattr(ss, "bluetooth_address", lambda: "192.168.44.2")
    assert ss.pick_transport("auto") == "bluetooth"
    monkeypatch.setenv("SHANI_LINK_SCREEN_TRANSPORT", "wifi")
    assert ss.pick_transport("auto") == "wifi"
    assert ss.pick_transport("usb") == "usb"


def test_a_wireless_phone_with_no_bluetooth_says_what_is_missing(monkeypatch):
    monkeypatch.setattr(ss, "phone_serial", lambda: ("", "no Android device is connected"))
    monkeypatch.setattr(ss, "bluetooth_address", lambda: None)
    transport, why = ss.choose()
    assert transport == ""
    assert "no Android device" in why and "Bluetooth" in why, why


# --- the tunnel, against a fake adb ------------------------------------------

FAKE_ADB = r"""#!/bin/sh
echo "$*" >> "$ADB_CALLS"
if [ "${FAIL_REVERSE:-0}" = "1" ] && [ "$1" = "reverse" ]; then
  echo "error: more than one device/emulator" >&2; exit 1
fi
case "$1 $2" in
  "devices -l") printf 'List of devices attached\nR5CT10\tdevice usb:1-1 model:Pixel_8\n' ;;
  "devices") printf 'List of devices attached\nR5CT10\tdevice usb:1-1 model:Pixel_8\n' ;;
  "reverse tcp:8090") echo "8090" ;;
  "reverse --remove") echo "" ;;
  "shell settings") case "$3 $4" in
      "get global") echo "${STAY_ON:-1}" ;;
      "put global") echo "" ;;
      *) exit 2 ;;
    esac ;;
  "shell dumpsys") printf 'Current Battery Service state:\n  AC powered: false\n  USB powered: true\n  level: 76\n' ;;
  "shell wm") printf 'Physical size: 1080x2400\nOverride size: 1080x2340\n' ;;
  *) echo "unexpected: $*" >&2; exit 1 ;;
esac
"""


@pytest.fixture
def adb(tmp_path, monkeypatch):
    """A fake `adb` first on PATH; returns the file its calls are appended to."""
    calls = tmp_path / "calls"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    binary = bindir / "adb"
    binary.write_text(FAKE_ADB)
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("ADB_CALLS", str(calls))
    return calls


def _battery(adb, tmp_path, text):
    """Point the fake adb's `dumpsys battery` at another answer."""
    path = tmp_path / "bin" / "adb"
    script = path.read_text().replace(
        "Current Battery Service state:\\n  AC powered: false\\n  USB powered: true\\n  level: 76\\n",
        text)
    path.write_text(script)


def test_adb_link_opens_the_tunnel_and_keeps_the_phone_awake(adb):
    with ss.adb_link(ss.PORT) as (ok, note):
        assert ok, note
        assert note == "", note
    calls = adb.read_text().splitlines()
    assert calls[0] == "reverse tcp:8090 tcp:8090", calls
    # the phone says it is on USB, so the USB bit is the one set; the AC bit is kept
    assert "shell settings put global stay_on_while_plugged_in 3" in calls, calls
    assert calls[-2:] == ["shell settings put global stay_on_while_plugged_in 1",
                          "reverse --remove tcp:8090"], calls


def test_a_phone_on_a_wireless_charger_gets_the_wireless_bit(adb, tmp_path, monkeypatch):
    monkeypatch.setenv("STAY_ON", "0")
    _battery(adb, tmp_path, 'Current Battery Service state:\\n  Wireless powered: true\\n  level: 88\\n')
    with ss.adb_link(ss.PORT) as (ok, _note):
        assert ok
    assert "shell settings put global stay_on_while_plugged_in 4" in adb.read_text(), \
        "a Qi pad is the wireless bit, not the USB one"


def test_a_phone_on_its_battery_is_told_it_may_dim(adb, tmp_path):
    _battery(adb, tmp_path, 'Current Battery Service state:\\n  AC powered: false\\n  level: 42\\n')
    with ss.adb_link(ss.PORT) as (ok, note):
        assert ok
        assert "may dim" in note, note
    calls = adb.read_text().splitlines()
    assert not any("stay_on_while_plugged_in" in call and "put" in call for call in calls), calls


def test_a_phone_that_never_set_stay_awake_is_left_alone(adb, monkeypatch):
    monkeypatch.setenv("STAY_ON", "null")
    with ss.adb_link(ss.PORT) as (ok, _note):
        assert ok
    calls = adb.read_text().splitlines()
    assert not any("stay_on_while_plugged_in" in call and "put" in call for call in calls), calls
    assert calls[-1] == "reverse --remove tcp:8090"


def test_a_tunnel_that_will_not_open_says_so_and_changes_nothing(adb, monkeypatch):
    monkeypatch.setenv("FAIL_REVERSE", "1")
    with ss.adb_link(ss.PORT) as (ok, why):
        assert not ok and "tunnel" in why, why
    calls = adb.read_text().splitlines()
    assert not any("stay_on" in call for call in calls), calls
    assert not any("--remove" in call for call in calls), calls


def test_the_tunnel_is_undone_even_when_the_stream_raises(adb):
    with pytest.raises(RuntimeError):
        with ss.adb_link(ss.PORT) as (ok, _note):
            assert ok
            raise RuntimeError("the encoder exploded")
    assert adb.read_text().splitlines()[-1] == "reverse --remove tcp:8090"


def test_this_machine_lists_the_phone_as_ready_through_the_engine(adb):
    """The page's "is there a phone?" goes through `android`, not a second parser."""
    assert ss.phone_serial() == ("R5CT10", "")
    assert ss.adb_ready() == (True, "")


# --- when the picture stops ---------------------------------------------------

def test_recovery_restarts_once_then_stops():
    recovery = ss.Recovery()
    restart, said = recovery.stopped()
    assert restart and "(1 of 1)" in said, said
    restart, said = recovery.stopped()
    assert not restart and "twice" in said, said


def test_a_frame_in_between_resets_the_count():
    """Twice in a row, not twice in an afternoon."""
    recovery = ss.Recovery()
    assert recovery.stopped()[0]
    recovery.playing()
    assert recovery.stopped()[0]


def test_a_share_the_person_ended_is_never_restarted():
    recovery = ss.Recovery(restarts=99)
    restart, said = recovery.stopped(session_closed=True)
    assert not restart and "ended" in said and "Start" in said, said


# --- codecs ------------------------------------------------------------------

def test_mjpeg_frames_are_bounded_parts():
    body = b"\xff\xd8jpeg"
    frame = ss.Mjpeg().frame(body)
    assert frame.startswith(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 6\r\n\r\n")
    assert frame.endswith(b"\r\n") and frame.rstrip(b"\r\n").endswith(body)


def test_the_webm_road_writes_the_container_as_it_comes():
    assert ss.WebM().frame(b"abc") == b"abc"


def test_the_pipeline_is_the_size_we_asked_for_and_the_codec_we_chose():
    """One pipeline description for both roads: the caps are the size, the tail the codec."""
    webm = ss.pipeline_description(7, 42, 1920, 1080, 24, 4_000_000, ss.WebM())
    assert "pipewiresrc fd=7 path=42" in webm
    assert "width=1920,height=1080,framerate=24/1" in webm
    assert "vp8enc" in webm and "webmmux streamable=true" in webm
    mjpeg = ss.pipeline_description(7, 42, 1920, 1080, 24, 4_000_000, ss.Mjpeg(quality=55))
    assert "jpegenc quality=55" in mjpeg and "vp8enc" not in mjpeg


def test_a_stream_without_a_session_never_claims_one_ended():
    assert ss.Stream(3, 4).session_closed() is False


def test_an_unknown_codec_is_a_refusal_that_names_the_ones_there_are():
    with pytest.raises(ValueError, match="mjpeg"):
        ss.create_codec("h265")


def test_a_desktop_without_a_portal_says_which_package():
    """A raw GDBus traceback in the log tells nobody what to install."""
    no_portal = ('GDBus.Error:org.freedesktop.DBus.Error.UnknownMethod: No such interface '
                 '“org.freedesktop.portal.ScreenCast” on object at path /org/freedesktop/portal/desktop')
    said = ss._portal_problem(RuntimeError(no_portal))
    assert "xdg-desktop-portal" in said, said
    assert "refused" in ss._portal_problem(RuntimeError("the person said no"))


def test_a_missing_element_is_named_for_each_road(monkeypatch):
    """The reason has to name the package's own element, not the portal's refusal."""
    import gi
    gi.require_version("Gst", "1.0")
    pytest.importorskip("gi")
    from gi.repository import Gst
    Gst.init(None)
    real = Gst.ElementFactory.find
    monkeypatch.setattr(Gst.ElementFactory, "find",
                        lambda name: None if name == "jpegenc" else real(name))
    ok, why = ss.gstreamer_ready("mjpeg")
    assert not ok and "jpegenc" in why, why

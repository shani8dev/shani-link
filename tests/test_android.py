"""The adb engine (shani_link/android.py), against a fake `adb` first on PATH.

Moved from Shani Chronoa's tests/test_qr_and_android.py with the engine; the
consent test stayed there with the `android_device` skill.
"""
import os
import stat
from pathlib import Path

import pytest

from shani_link import android as ad

FAKE_ADB = r"""#!/bin/sh
[ "$1" = "-s" ] && shift 2
case "$1 $2" in
  "devices -l") printf 'List of devices attached\nR58N123 device usb:1-1 product:x model:Pixel_8 device:shiba\n' ;;
  "shell getprop") case "$3" in ro.product.manufacturer) echo Google;; ro.product.model) echo "Pixel 8";;
                   ro.build.version.release) echo 15;; ro.build.version.security_patch) echo 2026-09-05;; esac ;;
  "shell df") printf 'Filesystem Size Used Avail Use%% Mounted\n/dev/fuse 110G 40G 70G 37%% /storage/emulated\n' ;;
  "shell pm") printf 'package:com.whatsapp\npackage:org.mozilla.firefox\n' ;;
  "shell wm") if [ "$3" = "size" ]; then
                printf 'Physical size: 1080x2400\nOverride size: 1080x2340\n'
              fi ;;
  "shell cmd") case "$3 $4" in
      "location is-location-enabled") echo false ;;
      *) exit 2 ;;
    esac ;;
  "shell dumpsys") case "$3" in
      battery) printf 'Current Battery Service state:\n  AC powered: false\n  USB powered: true\n  level: 76\n' ;;
      notification) printf 'NotificationRecord(0x1 pkg=com.whatsapp user=0)\n      android.title=String (Alice)\n      android.text=String (on my way)\n    NotificationRecord(0x2 pkg=com.google.android.dialer user=0)\n      android.title=String (Missed call)\n' ;;
      telephony.registry) printf '  mVoiceRegState=0(IN_SERVICE)\n  mOperatorAlphaLong=Jio True5G\n  getRilVoiceRadioTechnology=20(NR_SA)\n  mSignalStrength=SignalStrength:{ ssRsrp = -95 ssSinr = 15 }\n' ;;
      wifi) printf 'mWifiInfo SSID: "ShaniNet", RSSI: -58, Link speed: 390Mbps, Frequency: 5180MHz' ;;
      thermalservice) printf 'Cached temperatures:\n\tTemperature{mValue=62.606, mType=0, mName=CPU, mStatus=0}\n\tTemperature{mValue=47.079, mType=3, mName=SKIN, mStatus=0}\n' ;;
      usagestats) printf '      package=com.google.android.youtube totalTimeUsed="40:28" totalTimeVisible="40:41" appLaunchCount=12\n      package=com.whatsapp totalTimeUsed="00:05" totalTimeVisible="00:05" appLaunchCount=3\n      package=com.android.launcher3 totalTimeUsed="00:00" totalTimeVisible="00:00"\n' ;;
      display) printf '    DisplayDeviceInfo{"Built-in Screen": uniqueId="x", 1080 x 2460, modeId 2, renderFrameRate 120.00001, density 480, hdrCapabilities HdrCapabilities{mSupportedHdrTypes=[]}}\n' ;;
      *) exit 2 ;;
    esac ;;
  "exec-out screencap") printf '\211PNG\r\n\032\nfake' ;;
  "shell am") echo "Starting: Intent { act=android.intent.action.VIEW dat=$7 }" ;;
  push*) echo "1 file pushed" ;;
  *) echo "unexpected: $*" >&2; exit 1 ;;
esac
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture
def fake_adb(tmp_path, monkeypatch, home):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    adb = bindir / "adb"
    adb.write_text(FAKE_ADB)
    adb.chmod(adb.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    return home


def test_info_screenshot_apps_push_open(fake_adb):
    home = fake_adb
    assert "R58N123 Pixel 8 (ready)" in ad._run({"action": "devices"})
    info = ad._run({"action": "info"})
    assert "Google Pixel 8, Android 15" in info and "battery 76%, charging over USB" in info and "70G free" in info
    shot = ad._run({"action": "screenshot", "output": "~/p.png"})
    assert shot.startswith("Saved") and (home / "p.png").read_bytes().startswith(b"\x89PNG")
    assert "already exists" in ad._run({"action": "screenshot", "output": "~/p.png"})
    assert "com.whatsapp" in ad._run({"action": "apps", "query": "whats"})
    assert "No matching apps" in ad._run({"action": "apps", "query": "nothing-like-it"})
    (home / "f.txt").write_text("x")
    assert "Copied f.txt" in ad._run({"action": "push", "path": "~/f.txt"})
    assert "/sdcard/" in ad._run({"action": "push", "path": "~/f.txt", "to": "/system/bin"})
    assert "Opened https://shani.dev" in ad._run({"action": "open_url", "url": "https://shani.dev"})
    assert "http" in ad._run({"action": "open_url", "url": "javascript:alert(1)"})


def test_the_screenshot_goes_to_pictures_by_default(fake_adb):
    out = ad.screenshot("R58N123")
    assert out.startswith("Saved the phone's screen to " + str(fake_adb / "Pictures" / "phone-"))
    assert len(list((fake_adb / "Pictures").glob("phone-*.png"))) == 1


def test_the_stream_gets_one_ready_phone_and_the_size_it_sees(fake_adb):
    """The two questions a program asks rather than a person: which phone, and how big."""
    assert ad.ready_serial() == ("R58N123", "")
    # the override is what the person sees, so it wins over the physical panel
    assert ad.display_size("R58N123") == (1080, 2340)


def test_a_ready_phone_says_whether_it_is_on_a_cable_or_on_wi_fi(fake_adb, monkeypatch):
    """adb connect makes a serial of host:port, so the colon is the difference."""
    assert ad.ready_devices() == [("R58N123", "usb")]
    monkeypatch.setattr(ad, "_devices",
                        lambda: [("192.168.1.20:41000", "device", "Pixel 8")])
    assert ad.ready_devices() == [("192.168.1.20:41000", "wifi")]
    monkeypatch.setattr(ad, "_devices", lambda: [("X1", "unauthorized", "")])
    assert ad.ready_devices() == []


def test_the_stay_awake_bit_is_the_one_the_phone_is_charging_from(fake_adb):
    # dumpsys says USB here
    assert ad.powered_by("R58N123") == ("USB", 2)


def test_a_phone_on_a_wireless_charger_gets_the_wireless_bit(monkeypatch):
    monkeypatch.setattr(ad, "_shell",
                        lambda *_a: "Current Battery Service state:\n  Wireless powered: true\n")
    assert ad.powered_by("X") == ("Wireless", 4)


def test_a_phone_on_its_battery_has_no_stay_awake_bit(monkeypatch):
    monkeypatch.setattr(ad, "_shell",
                        lambda *_a: "Current Battery Service state:\n  AC powered: false\n")
    assert ad.powered_by("X") == ("", 0)


def test_a_phone_that_will_not_say_its_size_gets_none(monkeypatch):
    monkeypatch.setattr(ad, "_shell", lambda *_a: "null")
    assert ad.display_size("R58N123") is None
    monkeypatch.setattr(ad, "_shell", lambda *_a: "Physical size: junk")
    assert ad.display_size("R58N123") is None


def test_paths_outside_home_are_refused_for_the_tool(fake_adb, tmp_path):
    out = ad._run({"action": "screenshot", "output": str(tmp_path / "elsewhere.png")})
    assert out.startswith("Not touching") and not (tmp_path / "elsewhere.png").exists()
    assert "No path was given" in ad._run({"action": "push"})


def test_the_verbs_take_any_path_a_person_chose(fake_adb, tmp_path):
    """The app passes the file a person picked; no home rule in its way."""
    picked = tmp_path / "picked.txt"
    picked.write_text("x")
    assert ad.push("R58N123", picked) == "Copied picked.txt to the phone (/sdcard/Download/)."


def test_no_device_says_how(fake_adb, monkeypatch):
    monkeypatch.setattr(ad, "_devices", lambda: [])
    assert "USB debugging" in ad._run({"action": "info"})
    monkeypatch.setattr(ad, "_devices", lambda: [("X1", "unauthorized", "")])
    assert "allow USB debugging" in ad._run({"action": "info"})
    monkeypatch.setattr(ad, "_devices", lambda: [("A", "device", "a"), ("B", "device", "b")])
    assert "say which with serial" in ad._run({"action": "info"})


def test_no_adb_and_bad_actions_are_sentences(monkeypatch):
    assert "must be one of" in ad._run({"action": "shell"})
    monkeypatch.setattr(ad.shutil, "which", lambda name: None)
    assert ad._run({"action": "devices"}) == ad.NO_ADB
    assert ad.do(ad.info, "R58N123") == ad.NO_ADB


def test_a_timeout_is_said_not_raised(monkeypatch):
    import subprocess
    monkeypatch.setattr(ad.shutil, "which", lambda name: "/usr/bin/adb")

    def slow(*_a, **_k):
        raise subprocess.TimeoutExpired("adb", 60)
    monkeypatch.setattr(ad, "_adb", slow)
    assert ad.do(ad.info, "R58N123") == "The phone did not answer in time."
    assert ad._run({"action": "devices"}) == "The phone did not answer in time."


def test_human_size():
    assert ad.human_size(12) == "12 B" and ad.human_size(2048) == "2.0 KiB"
    assert Path(ad.default_screenshot()).suffix == ".png"


def test_usage_is_per_app_and_busiest_first(fake_adb):
    said = ad.usage("R58N123")
    assert "youtube: 40 h 41 min" in said and "whatsapp: 5 min" in said, said
    assert "launcher" not in said, "an app with no screen time is not usage"


def test_the_location_switch_is_read_as_a_sentence(fake_adb):
    assert ad.location("R58N123") == "The phone's location is off."


def test_the_display_is_size_rate_density_and_hdr(fake_adb):
    said = ad.display("R58N123")
    assert "1080x2460" in said and "120 Hz" in said, said
    assert "density 480" in said and "no HDR" in said, said

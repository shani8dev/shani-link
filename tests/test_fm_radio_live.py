"""Live FM: rtl_fm piped into pw-play, run against fakes of both.

The fakes are shell scripts on PATH: `rtl_fm` writes its argv and a known
burst of "audio" to stdout, then waits like a receiver does; `pw-play` writes
its argv and pid, then copies stdin to a file. So the test sees the exact
command lines, that what rtl_fm wrote reached pw-play through the pipe, and
that Stop ends both processes. No dongle, no PipeWire.
"""
import json
import os
import subprocess
import time

import pytest

from shani_link import fm_radio as F

AUDIO = "FM-AUDIO-0123456789"


def _stub(bindir, name, body):
    path = bindir / name
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(0o755)


def _wait(cond, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


@pytest.fixture
def radio(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "log"
    log.mkdir()
    _stub(bindir, "rtl_fm", f'''printf '%s\\n' "$@" > {log}/rtl_fm.args
echo "Found 1 device(s)" >&2
printf '{AUDIO}'
exec /bin/sleep 30''')
    _stub(bindir, "pw-play", f'''printf '%s\\n' "$@" > {log}/pw-play.args
echo $$ > {log}/pw-play.pid
exec /bin/cat > {log}/played''')
    _stub(bindir, "pw-dump", f'''pid=$(/bin/cat {log}/pw-play.pid)
echo '[{{"id": 77, "type": "PipeWire:Interface:Node", "info": {{"props": {{"object.id": 77, "application.process.id": '$pid', "media.name": "FM radio"}}}}}}]' ''')
    _stub(bindir, "wpctl", f'printf "%s " "$@" >> {log}/wpctl.args')
    # only the fakes: a real pw-play or wpctl must never be reached from here
    monkeypatch.setenv("PATH", str(bindir))
    yield log
    F.stop()


def _args(log, name):
    return (log / f"{name}.args").read_text().split("\n")[:-1]


def test_tune_plays_rtl_fm_through_pw_play_and_stop_ends_both(radio):
    log = radio
    said = F.run({"action": "tune", "frequency": "98.3"})
    assert said.startswith("Playing 98.3 MHz"), said
    assert _args(log, "rtl_fm") == ["-M", "wbfm", "-f", "98300000", "-r", "48000", "-"]
    play = _args(log, "pw-play")
    # raw s16 mono at the rate rtl_fm resamples to: one rate on both sides
    assert play[:9] == ["--raw", "--format", "s16", "--rate", "48000", "--channels", "1",
                        "--media-role", "Music"]
    assert play[play.index("--volume") + 1] == "1.00"
    assert 'media.name = "FM radio 98.3 MHz"' in play[play.index("-P") + 1]
    assert play[-1] == "-", "pw-play reads stdin"
    # what rtl_fm wrote came out of pw-play's stdin: the pipe, not a file
    assert _wait(lambda: (log / "played").exists() and (log / "played").read_text() == AUDIO)
    procs = list(F._PLAYING["procs"])
    assert F.playing() == 98.3e6
    assert F.run({"action": "stop"}) == "Stopped 98.3 MHz."
    assert all(p.poll() is not None for p in procs), "both children ended and reaped"
    assert F.playing() is None
    assert F.run({"action": "stop"}) == "The radio is not playing."


def test_tuning_again_replaces_the_station(radio):
    log = radio
    F.run({"action": "tune", "frequency": "91.1"})
    first = list(F._PLAYING["procs"])
    said = F.run({"action": "tune", "frequency": "104.8 MHz"})
    assert said.startswith("Playing 104.8 MHz"), said
    assert all(p.poll() is not None for p in first), "the old station stopped"
    assert _args(log, "rtl_fm")[3] == "104800000"


def test_volume_is_the_streams_own_through_wpctl(radio):
    log = radio
    assert "not playing" in F.run({"action": "volume", "volume": "40"})
    F.run({"action": "tune", "frequency": "98.3", "volume": "60"})
    play = _args(log, "pw-play")
    assert play[play.index("--volume") + 1] == "0.60"
    assert F.run({"action": "volume", "volume": "40"}) == "Radio volume 40%."
    assert (log / "wpctl.args").read_text().split() == ["set-volume", "77", "0.40"]
    assert "outside" in F.run({"action": "volume", "volume": "140"})
    assert "not a volume" in F.run({"action": "volume", "volume": "loud"})


def test_a_dongle_that_is_not_there_is_said_not_played(radio, tmp_path):
    _stub(tmp_path / "bin", "rtl_fm", 'echo "usb_open error -3" >&2; exit 1')
    said = F.run({"action": "tune", "frequency": "98.3"})
    assert "rtl_fm stopped at once: usb_open error -3" in said
    assert F.playing() is None and not F._PLAYING


def test_missing_tools_and_bad_frequencies_tune_nothing(radio, tmp_path):
    assert "outside the FM band" in F.run({"action": "tune", "frequency": "150"})
    os.unlink(tmp_path / "bin" / "pw-play")
    said = F.run({"action": "tune", "frequency": "98.3"})
    assert "`pw-play` is not installed (package: pipewire)" in said
    assert not (radio / "rtl_fm.args").exists(), "nothing started"


def test_hardware_names_pw_play(radio):
    assert "pw-play is installed" in F.hardware()


def test_the_pw_dump_fake_is_json(radio):
    """Guard on the fake itself: a broken stub would make the volume test vacuous."""
    (radio / "pw-play.pid").write_text("123\n")
    out = subprocess.run(["pw-dump"], capture_output=True, text=True).stdout
    assert json.loads(out)[0]["info"]["props"]["application.process.id"] == 123

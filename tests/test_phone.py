"""The phone surface: KDE Connect, with no phone needed - the link is faked at
its CLI seams.

"No phone link", "no paired phone" and "paired but unreachable" are three
answers, and each must come back as itself.
"""

import stat

import pytest

from shani_link import phone as ph


def _stub(bindir, name, script):
    p = bindir / name
    p.write_text("#!/bin/sh\n" + script)
    p.chmod(p.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def kde(tmp_path, monkeypatch):
    """A KDE desktop with a fake `kdeconnect-cli`.

    `list` is what the link has seen (`-l`), `up` what answers now (`-a`):
    writing `up` is the phone replying, leaving it out is a phone that is
    paired but out of reach.
    """
    d = tmp_path / "bin"
    d.mkdir()
    calls = tmp_path / "calls"
    _stub(d, "kdeconnect-cli", f'''echo "$@" >> {calls}
case "$*" in
  "-l --id-name-only") cat {tmp_path}/list 2>/dev/null || echo "abc123 Pixel 8";;
  "-a --id-only") cat {tmp_path}/up 2>/dev/null;;
  *) ;;
esac''')
    _stub(d, "busctl", 'case "$*" in *charge*) echo "i 15";; *isCharging*) echo "b false";; esac')
    monkeypatch.setenv("PATH", f"{d}:/usr/bin:/bin")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    return tmp_path, calls


def test_a_paired_phone_that_answers_is_reachable(kde):
    tmp_path, _ = kde
    (tmp_path / "up").write_text("abc123\n")
    assert ph.devices() == [ph.Device("abc123", "Pixel 8", True, True)]


def test_a_paired_phone_out_of_reach_is_kept_but_not_claimed(kde):
    # no `up` file: the link knows the phone, but it is not answering
    assert ph.devices() == [ph.Device("abc123", "Pixel 8", False, True)]


def test_a_link_with_no_paired_phone_has_no_devices(kde):
    tmp_path, _ = kde
    (tmp_path / "list").write_text("")
    assert ph.devices() == []


def test_no_link_installed_is_not_no_phone(kde, monkeypatch):
    """No KDE Connect, no GSConnect and no Bluetooth phone is this computer
    having no link - which the Devices page says differently from "no phone"."""
    tmp_path, _ = kde
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    monkeypatch.setattr(ph, "_gsconnect_daemon", lambda: None)

    from shani_link import phone_bluez
    monkeypatch.setattr(phone_bluez, "phones", lambda: [])

    with pytest.raises(ph.PhoneUnavailable) as caught:
        ph.devices()
    assert "no phone link is installed" in str(caught.value)


def test_battery_is_the_links_percent_and_never_a_guess(kde):
    assert ph.battery(ph.Device("abc123", "Pixel 8", True, True)) == (15, False)

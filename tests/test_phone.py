"""The phone surface: GSConnect/KDE Connect, with no phone needed - the link is faked at its CLI and Python seams.

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
    d = tmp_path / "bin"
    d.mkdir()
    calls = tmp_path / "calls"
    _stub(d, "kdeconnect-cli", f'''echo "$@" >> {calls}
case "$*" in
  "-l --id-name-only") echo "abc123 Pixel 8";;
  "-a --id-only") cat {tmp_path}/up 2>/dev/null;;
  *) ;;
esac''')
    _stub(d, "busctl", 'case "$*" in *charge*) echo "i 15";; *isCharging*) echo "b false";; esac')
    monkeypatch.setenv("PATH", f"{d}:/usr/bin:/bin")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    monkeypatch.setattr(skill, "_consent", lambda c: (True, ""))
    return tmp_path, calls











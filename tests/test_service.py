"""The D-Bus service, for real: shani-link on a private session bus (never the
person's own), asked through gdbus as any client would."""
import json, os, shutil, subprocess, sys, textwrap

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
pytestmark = pytest.mark.skipif(not shutil.which("dbus-run-session") or not shutil.which("gdbus"),
                                reason="needs dbus-run-session and gdbus")


def on_bus(script, tmp_path):
    """Run `script` (sh) on a fresh session bus with the service started in it."""
    env = dict(os.environ, XDG_CONFIG_HOME=str(tmp_path / "config"), XDG_RUNTIME_DIR=str(tmp_path / "run"),
               PYTHONPATH=ROOT)
    os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
    inner = textwrap.dedent(f"""
        {sys.executable} -c 'from shani_link.service import main; main()' &
        i=0; until gdbus introspect --session -d dev.shani.Link -o /dev/shani/Link/Landline >/dev/null 2>&1 || [ $i -ge 50 ]; do sleep 0.1; i=$((i+1)); done
        call() {{ gdbus call --session -d dev.shani.Link -o /dev/shani/Link/Landline -m dev.shani.Link.Landline."$@"; }}
    """) + script
    out = subprocess.run(["dbus-run-session", "--", "sh", "-c", inner], capture_output=True, text=True,
                         env=env, timeout=60)
    return out.stdout + out.stderr


def test_answers_as_any_client_would(tmp_path):
    out = on_bus("call IsSetUp; call Enabled; call Status; call InCall; call Calls\n", tmp_path)
    assert out.count("(false,)") >= 3, out
    assert "not set up on this computer" in out
    assert "('[]',)" in out


def test_errors_are_dbus_errors_not_crashes(tmp_path):
    out = on_bus("call Dial 123; call IsSetUp\n", tmp_path)
    assert "dev.shani.Link.Error" in out or "GDBus.Error" in out, out
    assert "(false,)" in out, "the service died after a failed call: " + out


def test_video_socket_is_private_and_served(tmp_path):
    out = on_bus('p=$(call VideoSocket | sed "s/(\\x27\\(.*\\)\\x27,)/\\1/"); stat -c "%a %F" "$p"\n', tmp_path)
    assert "600 socket" in out, out

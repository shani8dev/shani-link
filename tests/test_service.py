"""The D-Bus service, for real: shani-link on a private session bus (never the
person's own), asked through gdbus as any client would."""
import json, os, shutil, subprocess, sys, textwrap

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
pytestmark = pytest.mark.skipif(not shutil.which("dbus-run-session") or not shutil.which("gdbus"),
                                reason="needs dbus-run-session and gdbus")


def on_bus(script, tmp_path):
    """Run `script` (sh) on a fresh session bus with the service started in it."""
    env = dict(os.environ, XDG_CONFIG_HOME=str(tmp_path / "config"), XDG_RUNTIME_DIR=str(tmp_path / "run"), SHANI_LINK_NO_DEVICES="1",
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


def test_switches_are_read_and_set_over_the_bus(tmp_path):
    out = on_bus('''gdbus call --session -d dev.shani.Link -o /dev/shani/Link -m dev.shani.Link.SetSetting watch-weather-enabled true
gdbus call --session -d dev.shani.Link -o /dev/shani/Link -m dev.shani.Link.Settings
''', tmp_path)
    assert '\\"watch-weather-enabled\\": true' in out or '"watch-weather-enabled": true' in out, out
    assert (tmp_path / "config" / "shani-link" / "settings.json").exists()


def test_a_live_call_crosses_the_bus_as_its_fields_not_a_repr():
    """`Calls` carried `str(Call)` - "<shani_link.landline.ua.Call object at ...>" -
    so the app's Hold/Swap/Merge buttons could not tell a held call from an
    active one. Each call is now its number, name, state and whether it is held."""
    sys.path.insert(0, ROOT)
    from shani_link.landline.ua import Call
    from shani_link.service import call_view
    call = Call("id1", "tag", True, "+911234")
    call.state, call.held, call.name = "active", True, "Amma"
    view = call_view(call)
    assert json.loads(json.dumps(view)) == {"number": "+911234", "name": "Amma", "state": "active",
                                            "held": True, "outgoing": True}


def _gdbus(path, method, *args):
    iface = {"/dev/shani/Link/Contacts": "dev.shani.Link.Contacts",
             "/dev/shani/Link/Calls": "dev.shani.Link.Calls",
             "/dev/shani/Link/Phone": "dev.shani.Link.Phone"}[path]
    return f"gdbus call --session -d dev.shani.Link -o {path} -m {iface}.{method} " + " ".join(args) + "\n"


def test_contacts_are_searched_and_numbers_named_over_the_bus(tmp_path, monkeypatch):
    """The shell's dialer and Chronoa name a number the same way the app does."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    sys.path.insert(0, ROOT)
    from shani_link import contacts
    contacts.book().save(contacts.Contact(full_name="Asha Rao", given="Asha", family="Rao",
                                          phones=[("cell", "+919800000001")],
                                          emails=[("work", "asha@example.org")]))
    out = on_bus(_gdbus("/dev/shani/Link/Contacts", "Search", "asha", "10")
                 + _gdbus("/dev/shani/Link/Contacts", "Lookup", "09800000001")
                 + _gdbus("/dev/shani/Link/Contacts", "Lookup", "12345"), tmp_path)
    assert out.count('Asha Rao') >= 2, out
    assert '"source": "local"' in out and "asha@example.org" in out
    assert "('{}',)" in out, "an unknown number must come back as {} - " + out


def test_recent_calls_cross_the_bus_and_can_be_deleted_and_cleared(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    sys.path.insert(0, ROOT)
    from shani_link import calls_log
    keep = calls_log.add("landline", "+911", "in", False)
    drop = calls_log.add("modem", "+912", "out", True, seconds=20)
    out = on_bus(_gdbus("/dev/shani/Link/Calls", "DeleteCall", drop["id"])
                 + _gdbus("/dev/shani/Link/Calls", "RecentCalls", "10")
                 + _gdbus("/dev/shani/Link/Calls", "Lines")
                 + _gdbus("/dev/shani/Link/Calls", "Dial", "modem", "100")
                 + _gdbus("/dev/shani/Link/Calls", "ClearCalls")
                 + _gdbus("/dev/shani/Link/Calls", "RecentCalls", "10"), tmp_path)
    assert "(true,)" in out and keep["id"] in out and drop["id"] not in out, out
    assert '"missed": true' in out
    assert "('[]',)" in out, "no line here, and the cleared history, are both []: " + out
    assert "dev.shani.Link.Error" in out and "not connected" in out, out
    assert calls_log.rows() == []


def test_the_phones_internet_is_switched_over_the_bus(tmp_path, monkeypatch):
    """The shell and Chronoa turn Bluetooth tethering on and off as the app does,
    against tests/fake_nmcli.py first on PATH."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "nmcli").symlink_to(os.path.join(ROOT, "tests", "fake_nmcli.py"))
    state = tmp_path / "nmcli.json"
    state.write_text(json.dumps({"connections": []}))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_NMCLI_STATE", str(state))
    phone = "/dev/shani/Link/Phone"
    out = on_bus(_gdbus(phone, "TetherState", "AA:00:00:00:00:01")
                 + _gdbus(phone, "Tether", "AA:00:00:00:00:01", "true")
                 + _gdbus(phone, "TetherState", "AA:00:00:00:00:01")
                 + _gdbus(phone, "Tether", "AA:00:00:00:00:01", "false")
                 + _gdbus(phone, "Tether", "''", "true"), tmp_path)
    assert '"on": false' in out and '"on": true' in out, out
    assert "Connected through the phone" in out and "Stopped using" in out, out
    assert "No phone is paired over Bluetooth" in out, out
    calls = json.loads(state.read_text())["calls"]
    assert ["connection", "add", "type", "bluetooth", "bt-type", "panu", "bluetooth.bdaddr",
            "AA:00:00:00:00:01", "con-name", "Phone internet"] in calls, calls


def test_recent_calls_are_named_from_the_phones_cached_book(tmp_path, monkeypatch):
    """A call logged before the phone's contacts were read has no name in the
    file; a client of RecentCalls (the shell, Chronoa) still gets "Amma"."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    sys.path.insert(0, ROOT)
    from shani_link import calls_log, landline
    calls_log.add("phone", "+91 98220 11111", "in", True, seconds=30)
    landline.cache_contacts([("Amma", ("+919822011111",))])
    out = on_bus(_gdbus("/dev/shani/Link/Calls", "RecentCalls", "10"), tmp_path)
    assert '"name": "Amma"' in out or '\\"name\\": \\"Amma\\"' in out, out
    assert calls_log.rows()[0]["name"] == "", "named when read, the file left as it was"

"""The Phone link tab, and the state it could not be driven into.

Moved from Shani Chronoa's tests/test_phone_link_panel.py with the page; its
consent cases (`lan-phone-enabled`) and registry cases did not come along:
they were about the assistant's switch and Chronoa's sidebar.

**Why this file exists beyond the panel.** `_PhoneLinkView` exposed
`_refresh_list()` on its page but the state that method read (`self._devices`)
was not reachable, so a caller setting `page._devices = [...]` wrote an attribute
on the *page* that the *view* never looked at - and the panel sat on its empty
state forever. A panel that cannot be driven into a state it can be in is a panel
whose that-state behaviour has no witness. `show_devices()` is the seam, and it
is one method rather than a settable attribute because two writes that must agree
("set this, then refresh") is two places to fall out of step.

Built in a subprocess on the private Broadway display conftest.py started (a GDK
backend is chosen when GTK initialises), with its own state directory.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

from _gui import needs_display

PKG = pathlib.Path(__file__).resolve().parents[1]
pytestmark = needs_display

_HARNESS = r'''
import json, sys
sys.path.insert(0, %(pkg)r)
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
Adw.init()

from shani_link.app import phone_link


class App:
    activated = []

    def activate_action(self, name, value):
        App.activated.append([name, value.unpack()])


def walk(widget, out=None):
    """Every string in a tree - labels, and `Adw.ActionRow` titles/subtitles,
    which are properties rather than child widgets and are invisible to a
    child-only walk."""
    if out is None:
        out = []
    if isinstance(widget, Gtk.Label):
        out.append(widget.get_label())
    if hasattr(widget, "get_title"):
        out.append(widget.get_title())
    if hasattr(widget, "get_subtitle"):
        out.append(widget.get_subtitle())
    child = widget.get_first_child()
    while child is not None:
        walk(child, out)
        child = child.get_next_sibling()
    return out


def kinds(widget, out=None):
    if out is None:
        out = []
    out.append(type(widget).__name__)
    child = widget.get_first_child()
    while child is not None:
        kinds(child, out)
        child = child.get_next_sibling()
    return out


def buttons(widget, out=None):
    if out is None:
        out = []
    if isinstance(widget, Gtk.Button):
        out.append(widget.get_label())
    child = widget.get_first_child()
    while child is not None:
        buttons(child, out)
        child = child.get_next_sibling()
    return out


def say(**kw):
    print("RESULT " + json.dumps(kw), flush=True)


case = sys.argv[1]

if case == "empty":
    page = phone_link.build(App())
    say(kinds=kinds(page), texts=walk(page), buttons=buttons(page))

elif case == "devices":
    page = phone_link.build(App())
    page.show_devices([{"name": "Pixel 9", "address": "192.168.1.44",
                        "deviceType": "phone"},
                       {"name": "Galaxy", "address": "192.168.1.51"}])
    say(texts=walk(page), buttons=buttons(page))

elif case == "pressed_finds_nothing":
    # the honest empty answer, reached the way a person reaches it.
    # **The loop has to be pumped.** `_off_thread` finishes on
    # `GLib.idle_add`, and a harness that only ever builds widgets never runs
    # the main loop - so the worker would finish, queue its callback, and the
    # page would sit on "Listening on the network." forever. Measured, and the
    # first version of this test asserted against exactly that.
    from gi.repository import GLib
    import time as _t
    from shani_link import lan_link
    lan_link.listen_once = lambda seconds: (_t.sleep(0.2), [])[1]   # never the real network
    page = phone_link.build(App())
    page.find.emit("clicked")
    context = GLib.MainContext.default()
    deadline = _t.time() + 15
    while _t.time() < deadline:
        while context.pending():
            context.iteration(False)
        if page.find.get_sensitive() and page.find_note.get_label() != "Listening on the network.":
            break
        _t.sleep(0.15)
    say(note=page.find_note.get_label(),
        list_is_empty="No phone found yet" in " ".join(walk(page)))

elif case == "pairing_not_implemented":
    page = phone_link.build(App())
    say(texts=walk(page), buttons=buttons(page))

elif case == "paired":
    from shani_link import lan_link
    lan_link._remember_pairing({"deviceId": "f" * 32, "deviceName": "Pixel 9"},
                               "192.168.1.44", 1716)
    page = phone_link.build(App())
    say(texts=walk(page), buttons=buttons(page))

elif case == "registry":
    from shani_link import lan_link
    say(pairing=lan_link.pairing_status()[0],
        # the panel's own claim, next to the module's
        claims_not_implemented=phone_link.NOT_PAIRED)

elif case == "pair_button":
    page = phone_link.build(App())
    def find_button(w, label):
        if isinstance(w, Gtk.Button) and w.get_label() == label:
            return w
        c = w.get_first_child()
        while c is not None:
            hit = find_button(c, label)
            if hit is not None:
                return hit
            c = c.get_next_sibling()
    find_button(page, "Pair a phone").emit("clicked")
    say(activated=App.activated)

elif case in ("announce", "announce_fails"):
    # the button, pressed, with the network stood in for: `lan_link.announce`
    # is what `lan_phone.announce` sends through
    from gi.repository import GLib
    import time as _t
    from shani_link import lan_link
    asked = []
    if case == "announce":
        lan_link.announce = lambda name, tcp_port=1716: (asked.append(name), (True, ""))[1]
    else:
        lan_link.announce = lambda name, tcp_port=1716: (asked.append(name), (False, "no route"))[1]
    page = phone_link.build(App())
    page.announce.emit("clicked")
    context = GLib.MainContext.default()
    deadline = _t.time() + 15
    while _t.time() < deadline:
        while context.pending():
            context.iteration(False)
        if page.announce.get_sensitive():
            break
        _t.sleep(0.05)
    say(note=page.find_note.get_label(), asked=asked, buttons=buttons(page))

else:
    say(error="unknown case " + case)
''' % {"pkg": str(PKG)}


@pytest.fixture
def broadway():
    return os.environ.get("BROADWAY_DISPLAY", "")


def _run(broadway, case, tmp_path):
    script = tmp_path / f"harness-{case}.py"
    script.write_text(_HARNESS)
    env = dict(os.environ)
    env.update(PYTHONDONTWRITEBYTECODE="1", GSETTINGS_BACKEND="memory",
               XDG_STATE_HOME=str(tmp_path / "state"),
               XDG_DATA_HOME=str(tmp_path / "data"))
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    done = subprocess.run([sys.executable, str(script), case],
                          capture_output=True, text=True, timeout=180, env=env)
    for line in done.stdout.splitlines():
        if line.startswith("RESULT"):
            return json.loads(line[6:])
    pytest.fail(f"the harness produced nothing:\n{done.stdout[-900:]}\n"
                f"{done.stderr[-900:]}")


def shown(out) -> str:
    return " | ".join(t for t in out.get("texts", []) if t)


def clicked(out) -> str:
    return " | ".join(b for b in out.get("buttons", []) if b)


class TestItSaysWhatIsNotThere:
    def test_not_paired_leads_with_the_way_to_pair(self, broadway, tmp_path):
        """**First, not after somebody tries** - and with the button that
        does it, not a sentence about a missing feature."""
        out = _run(broadway, "pairing_not_implemented", tmp_path)
        assert "No phone paired yet" in shown(out), shown(out)
        assert "Pair a phone" in out["buttons"], out["buttons"]

    def test_paired_names_the_phone_and_offers_forget(self, broadway, tmp_path):
        out = _run(broadway, "paired", tmp_path)
        assert any("Pixel 9" in t and "192.168.1.44" in t for t in out["texts"]), out["texts"]
        assert "Forget this phone" in out["buttons"], out["buttons"]

    def test_the_panels_claim_and_the_modules_agree(self, broadway, tmp_path):
        out = _run(broadway, "registry", tmp_path)
        assert out["pairing"] == "not paired", out
        assert out["claims_not_implemented"] == "No phone paired yet", out


class TestTheDeviceState:
    """The state the first version of this panel could not be driven into."""

    def test_a_device_that_answered_is_shown_with_its_address(self, broadway, tmp_path):
        out = _run(broadway, "devices", tmp_path)
        assert "error" not in out, out.get("error")
        assert "Pixel 9" in shown(out), shown(out)
        assert "192.168.1.44" in shown(out), shown(out)
        assert "Galaxy" in shown(out), shown(out)

    def test_the_address_is_the_subtitle_and_it_is_selectable(self, broadway, tmp_path):
        out = _run(broadway, "devices", tmp_path)
        # `Adw.ActionRow`'s subtitle, which `common.row()` sets - and which a
        # child-only walk cannot see, which is how the first version of this
        # test passed against an empty panel.
        assert any(t == "192.168.1.44" for t in out["texts"]), out["texts"]

    def test_each_device_offers_the_one_thing_worth_doing(self, broadway, tmp_path):
        out = _run(broadway, "devices", tmp_path)
        assert clicked(out).count("Send a file") == 2, clicked(out)

    def test_the_empty_state_is_an_empty_state(self, broadway, tmp_path):
        out = _run(broadway, "empty", tmp_path)
        assert "No phone found yet" in shown(out), shown(out)
        assert "Look for phones" in clicked(out), clicked(out)
        assert "Send a file" not in clicked(out), (
            "a device row where no device answered is a control that cannot act")

    def test_the_empty_page_is_a_real_empty_state_not_a_blank_box(self, broadway, tmp_path):
        out = _run(broadway, "empty", tmp_path)
        assert "StatusPage" in out["kinds"], out["kinds"][:8]


class TestThePressSaysWhatHappened:
    """A button that does nothing with no sentence is the dead-control class."""

    def test_pressing_look_answers_honestly(self, broadway, tmp_path):
        """Discovery with nothing around is an answer, on the page, and it is
        called out as the thing it is."""
        out = _run(broadway, "pressed_finds_nothing", tmp_path)
        assert "No phone answered in" in (out["note"] or ""), out["note"]
        assert out["list_is_empty"] is True, out


class TestPairing:
    def test_pair_a_phone_opens_connect_a_device_on_kde_connect(self, broadway, tmp_path):
        out = _run(broadway, "pair_button", tmp_path)
        assert out["activated"] == [["connect-device", "kdeconnect"]], out


class TestMakeThisComputerFindable:
    """The skill's `announce`, now a button: a phone's KDE Connect app lists this
    computer and offers to pair from its side."""

    def test_the_button_announces_and_says_what_the_phone_will_do(self, broadway, tmp_path):
        out = _run(broadway, "announce", tmp_path)
        assert "Make this computer findable" in out["buttons"], out["buttons"]
        assert len(out["asked"]) == 1, out
        assert out["note"].startswith(f"Announced this computer as '{out['asked'][0]}'"), out["note"]
        assert "will now offer to pair" in out["note"], out["note"]

    def test_a_network_that_carries_nothing_is_said_in_words(self, broadway, tmp_path):
        out = _run(broadway, "announce_fails", tmp_path)
        assert out["note"] == ("Could not announce this computer (no route). "
                               "The network may not carry broadcast."), out["note"]

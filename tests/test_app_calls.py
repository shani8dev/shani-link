"""The Calls tab's history, dial pad and lines; and tel: links opening the dial pad.

The tabs are built on the private Broadway display with the phone link and the
audio route stubbed at the names the page calls; the call history and the
contacts live in a temporary XDG_DATA_HOME. The tel: test builds the whole
window in a child process with nothing of the machine reachable, as
test_app_window.py does, and checks that nothing was dialled.
"""
import json
import os
import subprocess
import sys
import time

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from _gui import needs_display, pump, texts  # noqa: E402

pytestmark = needs_display
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class App:
    switches = None


@pytest.fixture()
def panel(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    from gi.repository import Adw, Gtk
    Adw.init()
    from shani_link.app import phone
    monkeypatch.setattr(phone.PhonePanel, "show_route", lambda self: None)
    monkeypatch.setattr(phone.PhonePanel, "find_phone", lambda self: None)
    p = phone.PhonePanel(App())
    p.page = p.build()
    p.window = Gtk.Window(child=p.page)
    pump(seconds=0.3)
    return p


def _seed():
    from shani_link import calls_log, contacts
    contacts.book().save(contacts.Contact(full_name="Amma", phones=[("cell", "09800000001")]))
    now = time.time()
    calls_log.add("landline", "+919800000001", "in", False, when=now - 86400)
    calls_log.add("modem", "+914444444444", "out", True, seconds=125, when=now - 60)


def _history_rows(p):
    out = []

    def walk(w):
        if hasattr(w, "call_row"):
            out.append(w)
        c = w.get_first_child()
        while c is not None:
            walk(c)
            c = c.get_next_sibling()
    walk(p.calls_history.body)
    return out


def _menu(row):
    from gi.repository import Gtk

    def walk(w):
        if isinstance(w, Gtk.MenuButton):
            return w
        c = w.get_first_child()
        while c is not None:
            found = walk(c)
            if found:
                return found
            c = c.get_next_sibling()
    return walk(row)


def test_one_history_for_every_line_grouped_by_day_and_named_from_contacts(panel):
    _seed()
    panel.load_history()
    words = texts(panel.calls_history.body)
    assert words.index("Today") < words.index("Yesterday"), words
    rows = _history_rows(panel)
    assert [r.get_title() for r in rows] == ["+914444444444", "Amma"]
    assert rows[0].get_subtitle().startswith("Outgoing · Mobile") and "2 min 05 s" in rows[0].get_subtitle()
    assert rows[1].get_subtitle().startswith("Missed · Landline")


def test_call_back_uses_the_line_the_call_was_on(panel):
    _seed()
    panel.load_history()
    placed = []
    panel.call = lambda number, line="": placed.append((number, line))
    from gi.repository import Gtk
    missed = _history_rows(panel)[1]

    def first_button(w):
        if isinstance(w, Gtk.Button) and (w.get_tooltip_text() or "").startswith("Call "):
            return w
        c = w.get_first_child()
        while c is not None:
            b = first_button(c)
            if b:
                return b
            c = c.get_next_sibling()
    first_button(missed).emit("clicked")
    assert placed == [("+919800000001", "landline")]


def test_the_rows_menu_messages_adds_to_contacts_and_deletes(panel):
    _seed()
    panel.load_history()
    unknown = _history_rows(panel)[0]
    actions = _menu(unknown).actions
    assert set(actions) == {"Send a message", "Add to contacts", "Add to an existing contact",
                            "Delete from the history"}
    assert "Add to contacts" not in _menu(_history_rows(panel)[1]).actions, "Amma is already yours"
    actions["Send a message"]()
    assert panel.stack.get_visible_child_name() == "messages" and panel.to.get_text() == "+914444444444"
    actions["Add to contacts"]()
    assert panel.stack.get_visible_child_name() == "contacts"
    assert panel.contacts_view.stack.get_visible_child_name() == "edit"
    assert [e.get_text() for _d, e, _l in panel.contacts_view.f_phones] == ["+914444444444"]
    actions["Add to an existing contact"]()
    assert panel.contacts_view.pick_bar.get_visible()
    actions["Delete from the history"]()
    assert [r.get_title() for r in _history_rows(panel)] == ["Amma"]


def test_clear_all_can_be_undone(panel):
    from shani_link import calls_log
    _seed()
    panel.load_history()
    panel.calls_history.clear_all()
    assert calls_log.rows() == [] and _history_rows(panel) == []
    assert panel.last_toast.get_title() == "Call history cleared"
    panel.last_toast.emit("button-clicked")
    assert len(calls_log.rows()) == 2 and len(_history_rows(panel)) == 2


def test_the_dial_pad_suggests_contacts_by_their_letters(panel):
    from shani_link import contacts
    contacts.book().save(contacts.Contact(full_name="Amma", phones=[("cell", "09800000001")]))
    panel.number.set_text("266")                     # A-M-M
    assert panel.suggest.get_visible()
    row = panel.suggest.get_first_child()
    assert row.get_title() == "Amma" and row.suggestion == "09800000001"
    row.emit("activated")
    assert panel.number.get_text() == "09800000001"
    assert panel.match.get_text() == "Amma" and not panel.suggest.get_visible()


def test_holding_zero_gives_plus_and_not_plus_and_zero(panel):
    panel.number.set_text("")
    panel._long_zero()
    panel._key_clicked("0")              # the release after the hold
    panel._key_clicked("9")
    assert panel.number.get_text() == "+9"


def test_paste_takes_the_number_out_of_a_tel_link(panel):
    from gi.repository import Gdk
    Gdk.Display.get_default().get_clipboard().set("tel:+1-201-555-0123")
    panel.number.set_text("")
    panel.paste()
    assert pump(lambda: panel.number.get_text() == "+12015550123", 2), panel.number.get_text()
    from shani_link.app.calls_view import dialable
    assert dialable("+91 (98) 765-43210") == "+919876543210" and dialable("call me") == ""


def test_the_chosen_line_is_remembered_and_used(panel, monkeypatch):
    from shani_link import mm_voice
    from shani_link.app import calls_view, phone
    panel.line_choice.set_selected(panel.line_ids.index("modem"))
    assert calls_view.default_line() == "modem"
    dialled = []
    monkeypatch.setattr(mm_voice, "dial", lambda n: dialled.append(n) or "/call/1")
    monkeypatch.setattr(phone.PhonePanel, "_follow_modem_call", lambda self, path: None)
    panel.call("+914444444444")
    assert pump(lambda: dialled and panel.incall_who.get_visible(), 2)
    assert dialled == ["+914444444444"] and "Mobile" in panel.incall_who.get_text()
    panel.line_choice.set_selected(panel.line_ids.index("landline"))
    panel.call("+914444444444")
    assert "landline is not connected" in panel.call_note.get_text()
    again = phone.PhonePanel(App())
    again.build()
    assert again.selected_line() == "landline"


def test_the_contacts_tab_is_the_address_book(panel):
    from shani_link.app.contacts_view import ContactsView
    assert isinstance(panel.contacts_view, ContactsView)
    panel.book = [("Bank", ("18001234",))]
    panel.show_contacts()
    assert [c.display_name() for c in panel.contacts_view.shown()] == ["Bank"]
    assert panel.name_for("18001234") == "Bank"


def test_history_drawn_before_the_phone_book_arrives_is_named_once_it_does(panel, monkeypatch, tmp_path):
    """A number only in the phone's contacts: the history is drawn on connect,
    the phone's book arrives seconds later (PBAP), and the row must then read
    "Amma" - with no service to write the cache file through."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    from shani_link import calls_log
    from shani_link import phone as ph
    calls_log.add("phone", "+91 98220 11111", "in", True, seconds=60)
    panel.load_history()
    assert [r.get_title() for r in _history_rows(panel)] == ["+91 98220 11111"]
    monkeypatch.setattr(ph, "contacts", lambda _dev: [("Amma", ("+91 98220 11111",))])
    panel.load_contacts()
    pump(lambda: [r.get_title() for r in _history_rows(panel)] == ["Amma"], 5)
    assert [r.get_title() for r in _history_rows(panel)] == ["Amma"]
    # and every other lookup in the app, the page's own book aside
    from shani_link import contacts
    assert contacts.name_for("09822011111") == "Amma"


HARNESS = r'''
import json, sys, time
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk
Adw.init()
from shani_link import phone as ph
from shani_link.app import line, window as W
from shani_link.app.switches import Switches
dialled = []
line.dial = lambda n: dialled.append(("landline", n))
ph.dial = lambda dev, n: dialled.append(("phone", n))

def pump(seconds):
    ctx = GLib.MainContext.default(); end = time.monotonic() + seconds
    while time.monotonic() < end:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.01)

win = W.LinkWindow(switches=Switches(bus=False), page="watch")
win.present()
win.open_dial(sys.argv[1])
pump(1.0)
panel = win.built["phone"].panel
print("RESULT " + json.dumps({"page": win.current, "tab": panel.stack.get_visible_child_name(),
                              "number": panel.number.get_text(), "note": panel.call_note.get_text(),
                              "dialled": dialled}))
'''


@pytest.mark.parametrize("uri,number,note", [
    ("tel:+1-201-555-0123;ext=12", "+12015550123", "extension, 12"),
    ("tel:863-1234;phone-context=+1-914-555", "+19145558631234", "Press Call"),
    ("sip:alice@example.com", "", "SIP address"),
])
def test_a_tel_link_lands_on_the_dial_pad_and_is_never_dialled(tmp_path, uri, number, note):
    (tmp_path / "bin").mkdir()
    env = dict(os.environ, PYTHONPATH=ROOT, PATH=str(tmp_path / "bin"), HOME=str(tmp_path / "home"),
               XDG_CONFIG_HOME=str(tmp_path / "config"), XDG_DATA_HOME=str(tmp_path / "data"),
               XDG_CACHE_HOME=str(tmp_path / "cache"),
               DBUS_SESSION_BUS_ADDRESS=f"unix:path={tmp_path}/no-bus",
               DBUS_SYSTEM_BUS_ADDRESS=f"unix:path={tmp_path}/no-bus", GSETTINGS_BACKEND="memory")
    script = tmp_path / "harness.py"
    script.write_text(HARNESS)
    done = subprocess.run([sys.executable, str(script), uri], capture_output=True, text=True,
                          timeout=120, env=env, cwd=str(tmp_path))
    line = next((ln for ln in done.stdout.splitlines() if ln.startswith("RESULT ")), "")
    assert line, done.stderr[-3000:]
    got = json.loads(line[7:])
    assert (got["page"], got["tab"], got["number"]) == ("phone", "calls", number), got
    assert note in got["note"] and got["dialled"] == [], got


def test_the_command_line_takes_dial_and_message():
    from shani_link.app import _parse
    assert _parse(["--dial", "tel:123"]) == {"dial": "tel:123"}
    assert _parse(["--dial"]) == {"dial": ""}
    assert _parse(["--dial=tel:1", "--message", "+91"]) == {"dial": "tel:1", "message": "+91"}


def test_the_desktop_file_handles_tel_links_without_dialling():
    text = open(os.path.join(ROOT, "data", "applications", "dev.shani.Link.Dial.desktop")).read()
    assert "MimeType=x-scheme-handler/tel;" in text
    assert "Exec=shani-link-app --dial %u" in text and "NoDisplay=true" in text

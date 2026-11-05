"""The Phone page for somebody who knows nothing technical.

No phone yet: one kind sentence and one button, no red "Needs attention" naming
GSConnect, and no call buttons on a computer that cannot make a call. A phone
connected: the call bar, the audio switch (for a Bluetooth phone), and the
tabs' controls. Texts are addressed by picking a contact, not by typing.

Built on the private Broadway display with the phone link stubbed at the names
the page calls (`shani_link.phone`); the history and contacts live in a
temporary XDG_DATA_HOME.
"""
import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from _gui import needs_display, pump, texts  # noqa: E402

pytestmark = needs_display


class App:
    switches = None

    def __init__(self):
        self.actions = []

    def activate_action(self, name, value=None):
        self.actions.append((name, value.unpack() if value is not None else None))


def _build(monkeypatch, tmp_path, devices, which="bluetooth"):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    from gi.repository import Adw, Gtk
    Adw.init()
    from shani_link import phone as ph
    from shani_link.app import phone

    def listed():
        if devices is None:
            raise ph.PhoneUnavailable("no phone link is installed (GSConnect on GNOME, KDE Connect "
                                      "on Plasma) and no phone is paired over Bluetooth")
        return devices
    monkeypatch.setattr(ph, "devices", listed)
    monkeypatch.setattr(ph, "backend", lambda: which)
    monkeypatch.setattr(ph, "battery", lambda _d: (80, False))
    monkeypatch.setattr(ph, "contacts", lambda _d: [("Amma", ("+91 98220 11111",))])
    monkeypatch.setattr(ph, "conversations", lambda _d: [])
    monkeypatch.setattr(phone.PhonePanel, "show_route", lambda self: None)
    p = phone.PhonePanel(App())
    p.page = p.build()
    p.window = Gtk.Window(child=p.page)
    p.window.present()
    pump(lambda: p.status_recorder.status() != "unknown", 5)
    pump(seconds=0.3)
    return p


def _shown(widget):
    """Is `widget` on screen: visible, and every parent visible."""
    while widget is not None:
        if not widget.get_visible():
            return False
        widget = widget.get_parent()
    return True


def test_no_phone_is_said_kindly_with_one_button(monkeypatch, tmp_path):
    p = _build(monkeypatch, tmp_path, None, which=None)
    words = texts(p.status_box)
    assert "No phone connected yet" in words, words
    assert not any("Needs attention" in w or "GSConnect" in w or "KDE Connect" in w for w in words), words
    from gi.repository import Gtk

    def buttons(w):
        if isinstance(w, Gtk.Button) and w.get_label():
            yield w
        c = w.get_first_child()
        while c is not None:
            yield from buttons(c)
            c = c.get_next_sibling()
    connect = [b for b in buttons(p.status_box)]
    assert [b.get_label() for b in connect] == ["Connect your phone"]
    connect[0].emit("clicked")
    assert p.app.actions == [("connect-device", "")]


def test_no_phone_no_call_buttons(monkeypatch, tmp_path):
    """Answer, hang up and "Call audio: This PC / Phone" with nothing to call
    on are controls that cannot work: hidden until there is a line."""
    p = _build(monkeypatch, tmp_path, None, which=None)
    assert not _shown(p.answer_button) and not _shown(p.end_button)
    assert not _shown(p.on_pc), "the audio switch is for a phone's call"
    assert not _shown(p.line_row), "no choice of line when there is no line"
    assert "connect your phone" in p.call_note.get_text().lower()


def test_no_phone_the_tabs_say_connect_your_phone(monkeypatch, tmp_path):
    p = _build(monkeypatch, tmp_path, None, which=None)
    for name in ("messages", "music", "files"):
        assert _shown(getattr(p, f"_{name}_missing")) or not _shown(p.stack), name
        assert not getattr(p, f"_{name}_body").get_visible(), name
    assert p.receive_row.get_visible(), "receiving from the phone is a switch, not a phone action"


def test_a_connected_bluetooth_phone_brings_the_call_bar(monkeypatch, tmp_path):
    from shani_link import phone as ph
    p = _build(monkeypatch, tmp_path, [ph.Device("AA", "Pixel 8", True, True)])
    words = texts(p.status_box)
    assert any("Pixel 8 is connected" in w for w in words), words
    assert any("battery 80%" in w for w in words), words
    assert _shown(p.answer_button) and _shown(p.end_button)
    assert _shown(p.on_pc), "a Bluetooth phone's call can be heard here or on the phone"
    assert p._messages_body.get_visible() and not p._messages_missing.get_visible()


def test_a_phone_app_phone_has_no_audio_switch(monkeypatch, tmp_path):
    from shani_link import phone as ph
    p = _build(monkeypatch, tmp_path, [ph.Device("AA", "Pixel 8", True, True)], which="kdeconnect")
    assert _shown(p.end_button)
    assert not _shown(p.on_pc), "KDE Connect does not carry the call's audio to this computer"


def test_a_paired_phone_out_of_reach_says_what_to_do(monkeypatch, tmp_path):
    from shani_link import phone as ph
    p = _build(monkeypatch, tmp_path, [ph.Device("AA", "Pixel 8", False, True)])
    words = " ".join(texts(p.status_box))
    assert "Pixel 8 is not connected" in words and "Turn on Bluetooth" in words
    assert not _shown(p.end_button)


def test_a_text_is_addressed_by_picking_a_contact(monkeypatch, tmp_path):
    from shani_link import contacts
    from shani_link import phone as ph
    p = _build(monkeypatch, tmp_path, [ph.Device("AA", "Pixel 8", True, True)])
    contacts.book().save(contacts.Contact(full_name="Priya Iyer", phones=[("cell", "+91 99870 33210")]))
    pump(lambda: p.book, 3)
    offered = p.recipients()
    assert ("Priya Iyer", "+91 99870 33210") in offered and ("Amma", "+91 98220 11111") in offered
    assert p.recipients("pri") == [("Priya Iyer", "+91 99870 33210")]
    p.to_picker.fill()
    rows = []
    c = p.to_picker.listing.get_first_child()
    while c is not None:
        if hasattr(c, "recipient"):
            rows.append(c)
        c = c.get_next_sibling()
    priya = next(r for r in rows if r.recipient[0] == "Priya Iyer")
    priya.emit("activated")
    assert p.to.get_text() == "+91 99870 33210"

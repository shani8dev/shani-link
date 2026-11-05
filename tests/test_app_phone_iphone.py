"""The Phone page with an iPhone connected: no calls or texts through it (they
aren't possible on Linux), what does work said plainly (Bluetooth audio, files
over USB in Files), and everything that does not depend on the phone kept - the
saved contacts, the landline and the modem."""
import html

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from _gui import needs_display, pump, texts  # noqa: E402
from test_app_phone_page import _build, _shown  # noqa: E402

pytestmark = needs_display


def _iphone(monkeypatch, tmp_path, name="Asha's iPhone", modalias="", lines=None):
    from shani_link import mm_voice, phone_bluez
    from shani_link import phone as ph
    from shani_link.app import line
    asked = []
    monkeypatch.setattr(phone_bluez, "modalias", lambda _a: modalias)
    monkeypatch.setattr(line, "is_set_up", lambda: bool(lines and "landline" in lines))
    monkeypatch.setattr(mm_voice, "available", lambda: bool(lines and "modem" in lines))
    p = _build(monkeypatch, tmp_path, [ph.Device("AA:00:00:00:00:09", name, True, True)])
    # after the build: anything asked of the phone for contacts or texts is recorded
    monkeypatch.setattr(ph, "contacts", lambda _d: asked.append("contacts") or [])
    monkeypatch.setattr(ph, "conversations", lambda _d: asked.append("conversations") or [])
    pump(seconds=0.5)
    p.asked = asked
    return p


def test_an_iphone_offers_no_calls_or_texts_and_says_what_works(monkeypatch, tmp_path):
    p = _iphone(monkeypatch, tmp_path)
    words = html.unescape(" ".join(texts(p.status_box)))   # row getters hand back markup
    assert "What works with an iPhone" in words, words
    assert "Bluetooth audio" in words and "Files" in words and "aren't possible on Linux" in words
    assert not _shown(p.answer_button) and not _shown(p.end_button), "no call bar for an iPhone"
    assert not _shown(p.on_pc)
    assert "phone" not in p.lines
    assert not p._messages_body.get_visible() and p._messages_iphone.get_visible()
    assert not p._files_body.get_visible() and p._files_iphone.get_visible()
    assert p._music_body.get_visible(), "its music plays here over Bluetooth"
    assert "aren't possible" in p.call_note.get_text()
    p.call("+91 98220 11111", "phone")
    assert "Not called" in p.call_note.get_text()


def test_an_iphone_known_only_by_its_apple_vendor_id(monkeypatch, tmp_path):
    p = _iphone(monkeypatch, tmp_path, name="Kitchen", modalias="bluetooth:v004Cp7102d0F40")
    assert p._iphone and not _shown(p.end_button)
    assert "What works with an iPhone" in " ".join(texts(p.status_box))


def test_saved_contacts_landline_and_modem_stay(monkeypatch, tmp_path):
    from shani_link import contacts
    p = _iphone(monkeypatch, tmp_path, lines=("landline", "modem"))
    assert p.lines == ["landline", "modem"], p.lines
    assert _shown(p.line_row), "the landline and the modem are still there to choose"
    contacts.book().save(contacts.Contact(full_name="Priya Iyer", phones=[("cell", "+91 99870 33210")]))
    assert ("Priya Iyer", "+91 99870 33210") in p.recipients()
    p.find_phone()
    pump(lambda: p.status_recorder.status() != "unknown", 5)
    pump(seconds=0.5)
    assert p.asked == [], f"the iPhone was asked for {p.asked}"


def test_an_android_phone_still_calls_and_texts(monkeypatch, tmp_path):
    p = _iphone(monkeypatch, tmp_path, name="Pixel 8")
    assert not p._iphone and _shown(p.end_button) and "phone" in p.lines
    assert p._messages_body.get_visible() and not p._messages_iphone.get_visible()
    assert "What works with an iPhone" not in " ".join(texts(p.status_box))

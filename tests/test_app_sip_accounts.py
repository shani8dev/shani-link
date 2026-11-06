"""The SIP accounts manager on the Calls page, with the service stubbed at
`app/sip_client` (the name the view calls, as the landline UI tests do)."""
import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

from shani_link.app import sip_accounts_view, sip_client  # noqa: E402

from _gui import needs_display  # noqa: E402

pytestmark = needs_display


class _Panel:
    def __init__(self):
        self.toasts = []

    def toast(self, title, button="", on_button=None):
        self.toasts.append((title, button, on_button))

    def reload_lines(self):
        self.reloaded = True


def _drain():
    ctx = GLib.MainContext.default()
    for _ in range(50):
        if not ctx.pending():
            break
        ctx.iteration(False)


def test_accounts_are_listed_and_added_through_the_dialog(monkeypatch):
    saved = []
    monkeypatch.setattr(sip_client, "accounts", lambda: list(saved))
    monkeypatch.setattr(sip_client, "add_account",
                        lambda fields: saved.append({**fields, "id": "abc123",
                                                     "name": fields.get("name") or
                                                     f"{fields['username']}@{fields['domain']}"}))
    monkeypatch.setattr(sip_client, "update_account",
                        lambda i, fields: saved[0].update(fields))
    monkeypatch.setattr(sip_client, "remove_account", lambda i: saved.clear() or True)
    panel = _Panel()
    box = sip_accounts_view.AccountsBox(panel)
    box.reload()
    assert box.rows == []

    dialog = sip_accounts_view.AccountDialog(panel, None, box.reload)
    dialog.fields["username"].set_text("bob")
    dialog.fields["password"].set_text("shh-secret")
    dialog.fields["domain"].set_text("sip.example.com")
    dialog._save()
    assert saved and saved[0]["username"] == "bob" and saved[0]["transport"] == "udp"
    box.reload()
    assert [r["name"] for r in box.rows] == ["bob@sip.example.com"]
    assert saved[0]["password"] == "shh-secret"       # kept by the service, never shown
    _drain()


def test_an_account_with_a_bad_field_says_so_in_words(monkeypatch):
    monkeypatch.setattr(sip_client, "accounts", lambda: [])

    def refuse(fields):
        raise RuntimeError("The username is missing - your provider's account name.")

    monkeypatch.setattr(sip_client, "add_account", refuse)
    panel = _Panel()
    dialog = sip_accounts_view.AccountDialog(panel, None, lambda: None)
    dialog.fields["username"].set_text("")
    dialog._save()
    assert dialog.error.get_visible()
    assert "username" in dialog.error.get_text().lower()
    _drain()

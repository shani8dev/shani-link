"""Shani Link, the app: the phone, the watch and the devices, for a person.

`shani-link-app` (bin/) runs `main()`. The pages are in this package; the
device plumbing they use is `shani_link` itself, and the background services
are `shani_link.service` (`shani-link`), which this app talks to over D-Bus.
"""
from __future__ import annotations

import sys

APP_ID = "dev.shani.Link.App"


def _parse(argv: "list[str]") -> dict:
    """`--page=NAME`, `--connect[=ROUTE]` (ROUTE: bluetooth, android, kdeconnect),
    `--dial[=URI]` (a tel: link or a number, onto the dial pad - never dialled)
    and `--message[=NUMBER]` (the Messages tab, addressed)."""
    out: dict = {}
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg.startswith("--page="):
            out["page"] = arg.split("=", 1)[1]
        elif arg == "--page" and args:
            out["page"] = args.pop(0)
        elif arg.startswith("--connect="):
            out["connect"] = arg.split("=", 1)[1]
        elif arg == "--connect":
            out["connect"] = args.pop(0) if args and not args[0].startswith("-") else ""
        elif arg.split("=", 1)[0] in ("--dial", "--message"):
            key = arg[2:].split("=", 1)[0]
            if "=" in arg:
                out[key] = arg.split("=", 1)[1]
            else:
                # `Exec=shani-link-app --dial %u` passes nothing when opened from
                # the menu: then it is the dial pad, empty
                out[key] = args.pop(0) if args and not args[0].startswith("--") else ""
    return out


def make_app():
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio

    class LinkApp(Adw.Application):
        def __init__(self) -> None:
            super().__init__(application_id=APP_ID,
                             flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
            self.window = None

        def _window(self, page: str = ""):
            from shani_link.app.window import LinkWindow
            if self.window is None:
                self.window = LinkWindow(application=self, page=page or "phone")
            elif page:
                self.window.show_page(page)
            return self.window

        def do_activate(self) -> None:
            self._window().present()

        def do_command_line(self, command_line) -> int:
            opts = _parse(list(command_line.get_arguments())[1:])
            window = self._window(opts.get("page", ""))
            window.present()
            if "connect" in opts:
                window.open_connect(opts["connect"])
            if "dial" in opts:
                window.open_dial(opts["dial"])
            if "message" in opts:
                window.open_message(opts["message"])
            return 0

    return LinkApp()


def main(argv: "list[str] | None" = None) -> int:
    return make_app().run(sys.argv if argv is None else argv)

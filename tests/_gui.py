"""Helpers for the app's GTK tests (the display itself is set up in conftest.py)."""
import os
import time

import pytest

#: True when conftest.py started a private Broadway display for this run.
HAS_DISPLAY = os.environ.get("GDK_BACKEND") == "broadway" and bool(os.environ.get("BROADWAY_DISPLAY"))

#: For a test that builds widgets: skipped, not failed, with no headless display.
needs_display = pytest.mark.skipif(not HAS_DISPLAY, reason="no gtk4-broadwayd: no private display "
                                   "to draw on, and the person's own is never used")


def pump(until=lambda: False, seconds: float = 5.0) -> bool:
    """Run the GLib main loop until `until()` or the time is up."""
    from gi.repository import GLib
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while ctx.pending():
            ctx.iteration(False)
        if until():
            return True
        time.sleep(0.01)
    return bool(until())


def texts(widget) -> "list[str]":
    """Every label, title and subtitle under `widget`, in tree order."""
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk
    out = []

    def walk(w):
        if isinstance(w, Gtk.Label):
            out.append(w.get_label())
        if isinstance(w, Adw.PreferencesRow):
            out.append(w.get_title())
            if isinstance(w, Adw.ActionRow):
                out.append(w.get_subtitle() or "")
        if isinstance(w, Gtk.Button) and w.get_label():
            out.append(w.get_label())
        if isinstance(w, Adw.StatusPage):
            out.extend([w.get_title() or "", w.get_description() or ""])
        child = w.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()
    walk(widget)
    return [t for t in out if t]

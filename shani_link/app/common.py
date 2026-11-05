"""Shared Adwaita scaffolding for Shani Link's pages.

Ported from Shani Chronoa's `gui/surfaces/common.py`, keeping only what the
device pages use. What did not come along, on purpose: the assistant's consent
banners ("Let Chronoa use ..."). Shani Link is used by a person directly, so a
page that reaches a device does it because the person opened the page; what
runs in the background without a click has a plain switch on the Background
page instead (`shani_link.settings`).

**Adw, but never at the cost of opening.** Every helper has a plain-GTK answer:
a page that degrades looks plain; a page that raises takes the window with it.
"""

from __future__ import annotations

import logging
import os
import re
import textwrap
import unicodedata
from typing import Any, Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")

from gi.repository import Adw, GLib, Gtk  # type: ignore  # noqa: E402

logger = logging.getLogger(__name__)

_ADW_INITIALISED = False

#: A wrapping label with no width cap decides how wide the page is; capped,
#: it wraps at a comfortable measure that also fits a narrow window.
MAX_WRAP_CHARS = 56


# --- small English, re-made here rather than imported from Chronoa ----------

def plural(count: int, word: str, prefix: str = "") -> str:
    """"1 file", "2 files"; `word` may spell an irregular plural as "entry/entries"."""
    singular, _, many = word.partition("/")
    if count == 1:
        shown = singular if many else word
    elif many:
        shown = many
    elif word.endswith("y") and not word.endswith(("ay", "ey", "iy", "oy", "uy")):
        shown = word[:-1] + "ies"
    elif word.endswith(("s", "x", "z", "ch", "sh")):
        shown = word + "es"
    else:
        shown = word + "s"
    return f"{count} {prefix} {shown}" if prefix else f"{count} {shown}"


def escape(text: str) -> str:
    """Escape the characters Pango markup treats specially."""
    return GLib.markup_escape_text(str(text))


def _joins(ch: str) -> bool:
    """A code point that belongs to the cluster before it."""
    code = ord(ch)
    return (unicodedata.combining(ch) != 0 or ch == "‍"
            or 0xFE00 <= code <= 0xFE0F or 0x1F3FB <= code <= 0x1F3FF
            or 0xE0020 <= code <= 0xE007F)


def _clusters(text: str) -> "list[str]":
    """Grapheme-ish clusters: a base character with its marks, and a ZWJ
    sequence ("family" emoji) kept whole."""
    out: "list[str]" = []
    for ch in text:
        if out and (_joins(ch) or out[-1].endswith("‍")):
            out[-1] += ch
        else:
            out.append(ch)
    return out


def _width(cluster: str) -> int:
    base = cluster[0]
    if unicodedata.east_asian_width(base) in ("W", "F") or ord(base) >= 0x1F000:
        return 2
    return 1


def clip(text: str, width: int, ellipsis: str = "...") -> str:
    """`text` cut to `width` columns on a cluster boundary, with an ellipsis.

    A hard slice can cut an emoji sequence after its joiner or a letter before
    its accent, which draws the missing-glyph box; this never does.
    """
    if width <= 0:
        return ""
    clusters = _clusters(text)
    if sum(_width(c) for c in clusters) <= width:
        return text
    budget = width - len(ellipsis)
    if budget < 1:
        budget, ellipsis = width, ""
    out, used = [], 0
    for c in clusters:
        w = _width(c)
        if used + w > budget:
            break
        out.append(c)
        used += w
    return "".join(out) + ellipsis


# --- layout ----------------------------------------------------------------

def wrap_label(label: "Gtk.Label", chars: int = MAX_WRAP_CHARS) -> "Gtk.Label":
    """Let `label` wrap, and stop it deciding how wide the page is.

    The text is also pre-broken with real newlines, because GTK4 measures a
    wrapped label at the allocated width but paints at `max_width_chars`, and
    the painted lines then overlap what is under them.
    """
    label.set_wrap(True)
    label.set_max_width_chars(chars)
    text = label.get_label()
    if text and "\n" not in text:
        label.set_label("\n".join(
            textwrap.fill(paragraph, width=chars)
            for paragraph in text.split("\n\n")))
    return label


def adw_ready() -> bool:
    """True when libadwaita is importable *and* initialised."""
    global _ADW_INITIALISED
    if not _ADW_INITIALISED:
        try:
            Adw.init()
        except Exception:  # noqa: BLE001
            return False
        _ADW_INITIALISED = True
    return True


#: Widest a page's content is laid out, in pixels.
PANEL_READING_PX = 820


def _clamped(child: Gtk.Widget, width: int = PANEL_READING_PX) -> Gtk.Widget:
    clamp = Adw.Clamp(maximum_size=width, tightening_threshold=width)
    clamp.set_child(child)
    return clamp


def surface(title: str, subtitle: str = "",
            max_width: int = PANEL_READING_PX,
            ) -> "tuple[Gtk.Widget, Callable[[Gtk.Widget], None]]":
    """A page body and the function that puts a child in it.

    Returns `(page, set_content)`. Unlike Chronoa's, the page carries **no
    header bar of its own**: the app's window owns the one header bar, and a
    page may be one tab of another (Devices holds four), so a header per page
    would stack bars. The page is a vertical box: the subtitle, then the content.
    `page.title` is kept for whoever shows it.
    """
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, vexpand=True)
    page.title = title
    inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, vexpand=True)
    if subtitle:
        note = Gtk.Label(label=subtitle)
        note.add_css_class("dim-label")
        note.add_css_class("surface-subtitle")
        wrap_label(note, 80)
        note.set_xalign(0.0)
        note.set_halign(Gtk.Align.START)
        note.set_margin_top(8)
        note.set_margin_start(12)
        note.set_margin_end(12)
        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, vexpand=True)
        shell.append(note)
        shell.append(inner)
        page.append(_clamped(shell, max_width) if adw_ready() else shell)
    else:
        page.append(_clamped(inner, max_width) if adw_ready() else inner)

    def set_content(child: Gtk.Widget) -> None:
        inner.append(child)

    return page, set_content


# --- health -----------------------------------------------------------------

STATUS_OK = "ok"
STATUS_ATTENTION = "attention"
STATUS_UNKNOWN = "unknown"
#: Switched off by a choice, not by a fault - grey, not red.
STATUS_OFF = "off"

STATUS_WORDS = {
    STATUS_OK: "Ready",
    STATUS_ATTENTION: "Needs attention",
    # Plain words: "Could not determine" read as an error to people who only
    # wanted to know whether their phone was there.
    STATUS_UNKNOWN: "Not known yet",
    STATUS_OFF: "Turned off",
}

STATUS_CLASSES = {
    STATUS_OK: "status-ok",
    STATUS_ATTENTION: "status-attention",
    STATUS_UNKNOWN: "status-unknown",
    STATUS_OFF: "status-off",
}

STATUS_ROW_CSS = "status-row"
STATUS_WORD_CSS = "status-word"
STATUS_DOT_PX = 10

#: The colours the status vocabulary paints with, installed by the app.
CSS = b"""
.status-row.status-ok .status-dot, .status-row.status-ok .status-word { color: rgba(34,197,94,0.9); }
.status-row.status-attention .status-dot, .status-row.status-attention .status-word { color: #ef4444; }
.status-row.status-unknown .status-dot, .status-row.status-unknown .status-word { color: rgba(245,158,11,0.95); }
.status-row.status-off .status-dot, .status-row.status-off .status-word { color: rgba(140,140,150,0.95); }
.status-dot { background-color: currentColor; border-radius: 5px; min-width: 10px; min-height: 10px; padding: 0; }
"""


def status_dot() -> "Gtk.DrawingArea":
    """The coloured dot. A drawing area, because an empty label is as tall as
    its font and the dot would be a bar."""
    dot = Gtk.DrawingArea()
    dot.set_content_width(STATUS_DOT_PX)
    dot.set_content_height(STATUS_DOT_PX)
    dot.add_css_class("status-dot")
    dot.set_valign(Gtk.Align.CENTER)
    return dot


class StatusRecorder:
    """One page's own health, made once and read in two places (the row on the
    page and `page.status()`), so the two cannot disagree."""

    def __init__(self, initial: str = STATUS_UNKNOWN) -> None:
        if initial not in STATUS_WORDS:
            raise ValueError(f"unknown initial status {initial!r}")
        self._status = initial

    def row(self, status: str, summary: str, detail: str = "") -> Gtk.Widget:
        self._status = status
        return status_row(status, summary, detail)

    def set(self, status: str) -> str:
        if status not in STATUS_WORDS:
            raise ValueError(f"unknown status {status!r}; expected one of {sorted(STATUS_WORDS)}")
        self._status = status
        return status

    def status(self) -> str:
        return self._status


def home_paths(text: Any) -> str:
    """`text` with this person's home folder written `~`."""
    text = "" if text is None else str(text)
    home = os.path.expanduser("~").rstrip("/")
    if not home or home == "/" or home not in text:
        return text
    return re.sub(re.escape(home) + r"(?=/|$|[\s'\"),.;:])", "~", text)


def _hover_full(widget: Gtk.Widget, *texts: str) -> None:
    full = " - ".join(t for t in texts if t)
    if full and home_paths(full) != full and not widget.get_tooltip_text():
        widget.set_tooltip_text(full)


def _first_label(widget: Gtk.Widget) -> "Optional[Gtk.Label]":
    if isinstance(widget, Gtk.Label):
        return widget
    child = widget.get_first_child()
    while child is not None:
        found = _first_label(child)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def status_row(status: str, summary: str, detail: str = "") -> Gtk.Widget:
    """A coloured dot, a fixed word, and the sentence that explains it.

    The vocabulary is closed: a fifth word would draw an uncoloured dot.
    """
    if status not in STATUS_WORDS:
        raise ValueError(f"unknown status {status!r}; the vocabulary is {sorted(STATUS_WORDS)}")
    word = STATUS_WORDS[status]
    full = summary if not detail else f"{summary} - {detail}"
    spoken = home_paths(full)
    if adw_ready():
        row = Adw.ActionRow()
        row.set_title(word)
        row.set_subtitle(spoken)
        row.add_css_class(STATUS_ROW_CSS)
        row.add_css_class(STATUS_CLASSES[status])
        row.add_prefix(status_dot())
        label = _first_label(row)
        if label is not None:
            label.add_css_class(STATUS_WORD_CSS)
        _hover_full(row, full)
        return row
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    box.add_css_class(STATUS_ROW_CSS)
    box.add_css_class(STATUS_CLASSES[status])
    box.append(status_dot())
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    head = Gtk.Label(label=word, xalign=0.0)
    head.add_css_class(STATUS_WORD_CSS)
    text.append(head)
    note = Gtk.Label(label=spoken, xalign=0.0, wrap=True)
    wrap_label(note)
    note.add_css_class("dim-label")
    text.append(note)
    box.append(text)
    return box


# --- rows and groups ----------------------------------------------------------

def group(title: str = "", description: str = "") -> Gtk.Widget:
    """A titled group of rows, or a plain box when Adw is not there."""
    if adw_ready():
        widget = Adw.PreferencesGroup(title=title)
        if description:
            widget.set_description(description)
        return widget
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    if title:
        heading = Gtk.Label(label=title)
        heading.add_css_class("heading")
        box.append(heading)
    if description:
        note = Gtk.Label(label=description)
        note.add_css_class("dim-label")
        wrap_label(note)
        box.append(note)
    return box


def row(title: str, subtitle: str = "", suffix: Optional[Gtk.Widget] = None,
        activatable: bool = False, escape: bool = True) -> Gtk.Widget:
    """One row: a title, an optional explanation, an optional control.

    Both title and subtitle are escaped here: `Adw.ActionRow` parses them as
    Pango markup, and "Files & Docs" is an ordinary name.
    """
    full_title, full_subtitle = title, subtitle
    title, subtitle = home_paths(title), home_paths(subtitle)
    if adw_ready():
        widget = Adw.ActionRow(
            title=(GLib.markup_escape_text(title) if escape else title) if title else "",
            subtitle=(GLib.markup_escape_text(subtitle) if escape else subtitle) if subtitle else "")
        if suffix is not None:
            widget.add_suffix(suffix)
        if activatable:
            widget.set_activatable(True)
        _hover_full(widget, full_title, full_subtitle)
        return widget
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    name = Gtk.Label(label=title, xalign=0.0)
    text.append(name)
    if subtitle:
        detail = Gtk.Label(label=subtitle, xalign=0.0)
        wrap_label(detail)
        detail.add_css_class("dim-label")
        text.append(detail)
    box.append(text)
    if suffix is not None:
        box.append(suffix)
    return box


def switch_row(title: str, subtitle: str = "", active: bool = False,
               on_changed: Optional[Callable[[bool], None]] = None) -> Gtk.Widget:
    """A row whose suffix is a switch. `active` is applied before the handler
    is connected, so drawing the row does not write the setting back."""
    toggle = Gtk.Switch(valign=Gtk.Align.CENTER, active=bool(active))
    if on_changed is not None:
        toggle.connect("notify::active", lambda _s, _p: on_changed(toggle.get_active()))
    widget = row(title, subtitle, suffix=toggle)
    if adw_ready() and isinstance(widget, Adw.ActionRow):
        widget.set_activatable_widget(toggle)
    widget.update_property([Gtk.AccessibleProperty.LABEL], [title])
    widget.toggle = toggle
    return widget


def connect_button(app: Any, route: str = "", label: str = "Connect a device") -> Gtk.Button:
    """A button that opens Connect a device, on `route` if given."""
    button = Gtk.Button(label=label, halign=Gtk.Align.CENTER)
    button.add_css_class("pill")
    button.add_css_class("suggested-action")

    def clicked(*_a) -> None:
        activate = getattr(app, "activate_action", None)
        if callable(activate):
            activate("connect-device", GLib.Variant("s", route))
    button.connect("clicked", clicked)
    return button


def empty_state(icon: str, title: str, description: str = "",
                child: Optional[Gtk.Widget] = None,
                *, inline: bool = False) -> Gtk.Widget:
    """"There is nothing here", said as a page rather than an empty rectangle.

    `inline=True` builds a box that can be squeezed: `Adw.StatusPage` insists on
    about 300px and is clipped to a slice of its icon when given less.
    """
    if adw_ready() and not inline:
        page = Adw.StatusPage(icon_name=icon, title=title, description=description)
        if child is not None:
            page.set_child(child)
        return page
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.set_valign(Gtk.Align.CENTER)
    box.set_vexpand(True)
    box.set_margin_top(36)
    box.set_margin_bottom(36)
    image = Gtk.Image.new_from_icon_name(icon)
    image.set_pixel_size(64)
    box.append(image)
    heading = Gtk.Label(label=title)
    heading.add_css_class("title-4")
    wrap_label(heading)
    box.append(heading)
    if description:
        note = Gtk.Label(label=description)
        wrap_label(note)
        note.set_justify(Gtk.Justification.CENTER)
        note.add_css_class("dim-label")
        box.append(note)
    if child is not None:
        box.append(child)
    return box


def banner(text: str, button_label: str = "",
           on_button: Optional[Callable[[], None]] = None,
           button_tooltip: str = "") -> Gtk.Widget:
    """A one-line notice above the content, with an optional action.

    Revealed here (an `Adw.Banner` draws nothing until revealed). With a button
    it is a plain box: `Adw.Banner` has no signal to connect a callable to.
    """
    if adw_ready() and not (button_label and on_button is not None):
        widget = Adw.Banner(title=text)
        widget.set_revealed(True)
        return widget
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    box.add_css_class("surface-banner")
    box.set_margin_start(12)
    box.set_margin_end(12)
    box.set_margin_top(6)
    box.set_margin_bottom(6)
    label = Gtk.Label(label=text)
    wrap_label(label)
    label.set_xalign(0.0)
    label.set_hexpand(True)
    box.append(label)
    if button_label and on_button is not None:
        button = Gtk.Button(label=button_label, valign=Gtk.Align.CENTER)
        button.connect("clicked", lambda _b: on_button())
        tip = button_tooltip or f"{button_label}: {text}"
        button.set_tooltip_text(tip)
        button.update_property([Gtk.AccessibleProperty.LABEL], [tip])
        box.append(button)
    return box


def loading(text: str) -> Gtk.Widget:
    """A spinner and what is being waited for - in place of a status row that
    says "not known yet" while a read is still running."""
    box = Gtk.Box(spacing=10, margin_top=6, margin_bottom=6, margin_start=12, margin_end=12)
    spinner = Gtk.Spinner(spinning=True)
    box.append(spinner)
    label = Gtk.Label(label=text, xalign=0.0)
    label.add_css_class("dim-label")
    box.append(label)
    box.loading_text = text
    return box


def details(title: str, text: str) -> Gtk.Widget:
    """The technical words a page has, folded away under `title` - there for
    whoever asks for them, not in front of everyone else."""
    expander = Gtk.Expander(label=title, margin_start=12, margin_end=12)
    label = Gtk.Label(label=home_paths(text), xalign=0.0, wrap=True, selectable=True)
    label.set_max_width_chars(80)
    label.add_css_class("dim-label")
    label.add_css_class("caption")
    label.set_margin_top(6)
    expander.set_child(label)
    expander.details_text = text
    return expander


def scrolled(child: Gtk.Widget, max_content_height: int = 0) -> Gtk.Widget:
    window = Gtk.ScrolledWindow(vexpand=True)
    window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    if max_content_height:
        window.set_max_content_height(max_content_height)
        window.set_propagate_natural_height(True)
    window.set_child(child)
    return window


def page_body(margin: int = 0) -> Gtk.Box:
    """The vertical box a page puts its groups into."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
    if margin:
        box.set_margin_top(margin)
        box.set_margin_bottom(margin)
    return box


def clear(container: Gtk.Widget) -> None:
    """Empty a container (GTK4 has no `foreach`). The sibling is read before
    the removal, which clears it."""
    child = container.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        container.remove(child)
        child = following


def in_thread(work: Callable[[], Any], done: Callable[[Any], None], name: str = "shani-link-app") -> None:
    """Run `work` off the main thread and hand its result (or exception) to
    `done` on the GTK loop. Nothing slow - a phone, a watch, adb - runs on the
    loop that draws the window."""
    import threading

    def run() -> None:
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001 - shown, not raised
            logger.warning("%s: %s", name, exc, exc_info=True)
            result = exc
        GLib.idle_add(lambda: (done(result), False)[1])
    threading.Thread(target=run, daemon=True, name=name).start()

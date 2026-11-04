"""The Paired tab: the phone and the devices paired with the phone link.

This tab adds no transport. Everything on it comes out of `shani_link.phone`:
GSConnect's D-Bus ObjectManager on GNOME, `kdeconnect-cli` plus `busctl` for the
battery on Plasma - `backend()`, `devices()`, `battery()`. Moved here from
Shani Chronoa, minus the assistant's `phone-control-enabled` consent and the
assistant's `phone` trigger.

**Three different things can be true here, and the page says which.** "No
service, no paired device and no reachable device are three different answers"
(`phone.py`), so each gets its own message and none is an empty list:

| state | what it means |
|---|---|
| `no phone link` | neither GSConnect nor KDE Connect is installed |
| `link not answering` | a backend is installed but will not answer |
| `no device paired` | the link answered and has nothing paired |

**What a device row says, and where each word came from.** `phone.Device` is
`id, name, reachable, paired` - there is no device-*type* field, so this page
does not claim to know whether a thing is a handset, a tablet or a laptop.
Battery is asked only of a reachable device: an unreachable phone's last
reading is not a current one. **Every row is stamped with when it was read.**

**Escaping is load-bearing, and both row shapes want opposite treatment.**
`Adw.PreferencesRow.use-markup` defaults to True and `Adw.StatusPage`'s
`description` is markup too, so a device named `Ann & <b>Bob</b>` renders as
nothing at all if handed raw; the no-Adw fallback is a plain `Gtk.Label`, where
escaping would show a literal `&amp;`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk  # noqa: E402

from shani_link import phone as ph  # noqa: E402
from shani_link.app import common  # noqa: E402
from shani_link.app.common import plural

logger = logging.getLogger(__name__)

TITLE = "Paired"
ICON = "network-wireless-symbolic"
SUBTITLE = ("The phone and the devices paired with this machine, through the "
            "desktop's own phone link.")

#: The four states in which there is no list to show, plus the one there is.
#: Declared rather than inferred from the wording, so a caller can tell "one of
#: ours" from a state this panel invented.
STATE_PERMITTED = "permitted"
STATE_NO_LINK = "no phone link"
STATE_LINK_DOWN = "link not answering"
STATE_NONE_PAIRED = "no device paired"

STATES = (
    STATE_PERMITTED,
    STATE_NO_LINK,
    STATE_LINK_DOWN,
    STATE_NONE_PAIRED,
)

#: state -> the headline it is shown under, and the body under that. One pair per
#: state, so the three messages this panel is judged on cannot drift apart from
#: each other or from the state names above.
HEADLINE = {
    STATE_NO_LINK: "No phone link is installed",
    STATE_LINK_DOWN: "The phone link did not answer",
    STATE_NONE_PAIRED: "No device is paired",
}

#: state -> the status word it is, beside `State.health`. `STATE_PERMITTED` is
#: absent deliberately: it is not a gate, it is the absence of one, and the
#: health of a panel that has devices to show comes from that count instead.
HEALTH = {
    STATE_NO_LINK: common.STATUS_UNKNOWN,
    STATE_LINK_DOWN: common.STATUS_UNKNOWN,
    STATE_NONE_PAIRED: common.STATUS_UNKNOWN,
}

#: state -> one clause for the status row, and **not** `BODY`.
#:
#: The row and the empty state below it are two places saying one thing, and the
#: obvious implementation - put the body in both - prints the same paragraph twice
#: on the same screen, which is what a rendered window showed before this line
#: existed: the status row carried all four lines of explanation, then the empty
#: state carried them again, with the route banner wedged between.
#:
#: So the row gets the *reason the word is that word* and the empty state gets the
#: full explanation. One clause above, one paragraph below, and the paragraph is
#: read once.
HEALTH_SUMMARY = {
    STATE_NO_LINK: "neither GSConnect nor kdeconnect-cli is installed here",
    STATE_LINK_DOWN: "a backend is installed but would not answer",
    STATE_NONE_PAIRED: "the link answered, with nothing paired to it",
}

BODY = {
    STATE_NO_LINK: (
        "This machine has neither backend phone.py knows about: no GSConnect (the "
        "GNOME Shell extension, which also needs its 'gjs' runtime) and no "
        "kdeconnect-cli (Plasma). That is a machine with no phone link, not a "
        "machine with no phone."
    ),
    STATE_LINK_DOWN: (
        "A backend is installed but would not answer, which is a different fact "
        "from no backend and a different fact again from a paired device. On GNOME "
        "that usually means the GSConnect extension is switched off."
    ),
    STATE_NONE_PAIRED: (
        "The link answered and has nothing paired to it. Pair a phone in "
        "GSConnect or KDE Connect, or with kdeconnect-cli on Plasma, and it "
        "appears here."
    ),
}

#: Css class on every row, so a test finds rows by walking the built widget
#: rather than off a list this module stashed on itself. See AGENTS.md: a count
#: read back out of the thing being counted is how fifteen assertions once passed
#: against a window that rendered nothing.
ROW_CSS = "devices-row"
#: On the device rows alone, so "one row per paired device" can be counted without
#: also counting the backend row and the trigger row, which share `ROW_CSS`.
DEVICE_CSS = "devices-device"
WHEN_CSS = "devices-when"

#: What each backend is called to a person, rather than by the module's own slug.
BACKEND_TITLE = {
    "gsconnect": "GSConnect (GNOME)",
    "kdeconnect": "KDE Connect (Plasma)",
}

#: Longest error text shown. `phone.py` truncates its own messages; this is for
#: the ones it raises from a subprocess, and the cut says how much is missing
#: rather than letting a truncated answer read as the whole one.
MAX_DETAIL = 200

@dataclass(frozen=True)
class State:
    """The panel's own answer when there is no list to show."""

    state: str
    why: str = ""

    @property
    def health(self) -> str:
        """Which of the three status words this state is.

        **A property on the state rather than a word chosen at each call site**,
        because the word is the claim and the state is the fact - and a second
        place to decide it is a second thing that can disagree. `build()` reads
        it to record the sidebar's dot and to draw the row at the top of the
        gate, and those two then cannot differ: both come from here.

        Three of the four states are "could not determine". That is deliberate
        and it is the honest reading: none of them is a fault in the machine or
        in the pairing, and none of them is a healthy reading either - in each
        case the panel simply has no answer about the phone, which is not the
        same as "nothing is wrong with the phone".
        """
        return HEALTH.get(self.state, common.STATUS_UNKNOWN)

    @property
    def headline(self) -> str:
        return HEADLINE.get(self.state, self.state)

    @property
    def body(self) -> str:
        """The generic explanation for this state, plus whatever *this* instance
        was refused for.

        The generic part alone is not enough, and losing the specific reason is
        how "these settings cannot answer" and "the key says no" end up printed
        identically - two different facts about the machine wearing one sentence.
        """
        generic = BODY.get(self.state, "")
        if not self.why or self.why in generic:
            return generic or self.why or self.headline
        return f"{generic}\n\nThis time: {self.why}" if generic else self.why


def _text(value: Any) -> str:
    """`value` escaped for the row shape `common.row()` is about to build.

    `common.row()` branches on `common.adw_ready()` - an `Adw.ActionRow` when it
    is true, a plain `Gtk.Box` of `Gtk.Label`s when it is not - and that is the
    same condition used here rather than a proxy for it, because the two shapes
    want opposite treatments (see the module docstring). `common.empty_state()`
    branches on the same condition for the same reason.
    """
    return common.escape(str(value)) if common.adw_ready() else str(value)


def _row(title: Any, subtitle: Any = "") -> Gtk.Widget:
    """One row, filled in at construction and never patched afterwards.

    `Gtk.Box` has no `set_subtitle` and no `add` (measured on GTK 4), so building
    the row and then setting its subtitle raises on the fallback path - which is
    exactly the shape a headless builder gets.
    """
    # **Not `_text()`:** `common.row()` escapes both fields now, and
    # escaping twice is what made a device named `Ann & Bob` render as
    # `Ann &amp; Bob` on screen.
    widget = common.row(title, subtitle)
    widget.add_css_class(ROW_CSS)
    return widget


def _stamp(read_at: float) -> Gtk.Widget:
    """The label saying when a row's reading was taken."""
    label = Gtk.Label(xalign=1.0)
    label.add_css_class(WHEN_CSS)
    label.add_css_class("dim-label")
    label.set_valign(Gtk.Align.CENTER)
    label.set_text(time.strftime("%H:%M:%S", time.localtime(read_at)))
    return label


def _backend() -> Optional[str]:
    """`phone.backend()`, or None if there is none.

    `backend()` is a `which` over `kdeconnect-cli` plus a glob for GSConnect's
    daemon - no D-Bus and no subprocess - so a failure here is a filesystem that
    could not be searched, and reporting that as "no phone link" would be the
    confident wrong answer this panel exists to avoid. Such a failure is reported
    as an undetermined backend and the read is attempted anyway.
    """
    try:
        return ph.backend()
    except Exception as exc:  # noqa: BLE001 - the traceback, not an empty list
        logger.warning("devices surface: phone.backend() failed", exc_info=True)
        return f"undetermined ({type(exc).__name__}: {str(exc)[:MAX_DETAIL]})"


def _paired() -> Tuple[List[Any], Optional[State]]:
    """The paired devices, or the state saying why there are none.

    Unpaired entries are dropped rather than listed: `phone.devices()` returns
    everything the link has ever seen, and a row for a device the user has
    unpaired is not one of yours any more.
    """
    try:
        every = ph.devices()
    except ph.PhoneUnavailable as exc:
        return [], State(STATE_LINK_DOWN, str(exc)[:MAX_DETAIL])
    except Exception as exc:  # noqa: BLE001 - "failed" and "not answering" differ
        return [], State(STATE_LINK_DOWN,
                         f"{type(exc).__name__}: {str(exc)[:MAX_DETAIL]}")
    return [d for d in every if getattr(d, "paired", False)], None


def _kind(device: Any) -> str:
    """What the link actually reports about this device - not its type.

    `phone.Device` has no type field, so a row claiming "phone", "tablet" or
    "laptop" would be inventing one.
    """
    if not getattr(device, "paired", False):
        return "known to the link but not paired"
    if getattr(device, "reachable", False):
        return "paired, reachable right now"
    return "paired, not reachable (off, out of range, or on another network)"


def _battery(device: Any) -> str:
    """`phone.battery()` in words, including every way it can decline.

    Asked only of a reachable device, for the reason in the module docstring, and
    only where the backend reports one: `battery()` returns `None` on GSConnect
    and wherever `busctl` is absent, and no reading is not 0%.
    """
    if not getattr(device, "reachable", False):
        return "battery: not read, the device is not reachable right now"
    try:
        reading = ph.battery(device)
    except Exception as exc:  # noqa: BLE001 - a battery is not a reason to fail a row
        return (f"battery: could not be read "
                f"({type(exc).__name__}: {str(exc)[:MAX_DETAIL]})")
    if not reading:
        return "battery: this backend does not report one"
    percent, charging = reading[0], bool(reading[1])
    return f"battery: {percent}%{' and charging' if charging else ''}"


def _add_row(container: Gtk.Widget, row: Gtk.Widget) -> None:
    """Put `row` into a group built by `common.group()`.

    `Adw.PreferencesGroup` is a `Gtk.ListBox` and takes `add`; the plain
    `Gtk.Box` the no-Adw path returns takes `append` on GTK4, where `add` no
    longer exists (measured). One branch rather than a capabilities check,
    because the box is a box and the list box is not.
    """
    adder = getattr(container, "add", None)
    if callable(adder):
        adder(row)
    else:
        container.append(row)


def _revealed(notice: Gtk.Widget) -> Gtk.Widget:
    """Make a banner actually visible.

    `Adw.Banner` starts hidden - `revealed` is FALSE until something sets it - so
    a caller that appends one and stops has put nothing on screen while every
    assertion about the banner's *text* still passes. The plain-GTK box has no
    such property, so this is a no-op there rather than a special case.
    """
    setter = getattr(notice, "set_revealed", None)
    if callable(setter):
        setter(True)
    return notice


def _device_row(device: Any, read_at: float) -> Gtk.Widget:
    """One paired device: name, kind, battery, and when it was read.

    The name is the device's own and is untrusted text - it came off a D-Bus
    ObjectManager or `kdeconnect-cli` - which is why it goes through `_row`'s
    escaping rather than straight into a label.
    """
    name = getattr(device, "name", "") or getattr(device, "id", "") or "(unnamed)"
    subtitle = "\n".join((
        "kind: " + _kind(device),
        _battery(device),
        "last known at " + time.strftime("%H:%M:%S", time.localtime(read_at)),
    ))
    widget = common.row(name, subtitle, suffix=_stamp(read_at))
    widget.add_css_class(ROW_CSS)
    widget.add_css_class(DEVICE_CSS)
    return widget


def build(app) -> Gtk.Widget:
    """The paired devices, read once when the tab is built."""
    page, set_content = common.surface(TITLE, SUBTITLE)
    #: One recorder for the panel's health, whether it reaches the rows below or
    #: returns an empty state at one of four gates. Every one of those returns
    #: used to hand back a page with no status at all - which is precisely the
    #: information the sidebar dot needs, since each of them is a different
    #: answer: not permitted, no link, could not read, and nothing paired.
    recorder = common.StatusRecorder()

    def gated(state: "State") -> Gtk.Widget:
        """An empty state, with the status row that matches its own word."""
        column = common.page_body(18)
        column.set_margin_top(12)
        # `recorder.row` - not `common.status_row`: this is the write that
        # decides the sidebar's dot, and a plain row here would leave the dot
        # reading whatever the previous panel said. The summary is the one-clause
        # reason, not the body - see `HEALTH_SUMMARY` for why the two places this
        # panel says the same thing must not say it identically.
        column.append(recorder.row(state.health, state.headline,
                                   HEALTH_SUMMARY.get(state.state, "")))
        column.append(common.empty_state(
            ICON, _text(state.headline), _text(state.body),
            child=common.connect_button(app)))
        return common.scrolled(column)

    which = _backend()
    if which is None:
        # No backend at all: the panel cannot tell whether anything is paired,
        # so this is "could not determine", not "nothing paired". Those read the
        # same in an empty state and mean opposite things in a dot.
        set_content(gated(State(STATE_NO_LINK)))
        page.status = recorder.status
        return page

    read_at = time.time()
    devices, problem = _paired()
    if problem is not None:
        set_content(gated(problem))
        page.status = recorder.status
        return page
    if not devices:
        # The link answered and there is genuinely nothing paired. That is a
        # clean answer about an empty thing, not a fault - but it is also not
        # "ready", so it is the one state where neither is the whole truth.
        set_content(gated(State(STATE_NONE_PAIRED)))
        page.status = recorder.status
        return page

    # The panel's own health, above the paired devices: are
    # devices paired, is the link up, or could it not be told?
    # One row, one dot, one word - the question the panel is
    # opened for, before the rows that hold the devices.
    body = common.page_body(18)
    body.set_margin_top(12)
    body.set_margin_bottom(12)
    body.append(recorder.row(
        common.STATUS_OK,
        f"{plural(len(devices), 'device')} paired",
        f"read once at {time.strftime('%H:%M:%S', time.localtime(read_at))}"))
    body.append(_revealed(common.banner(
        f"{len(devices)} paired device{'' if len(devices) == 1 else 's'}, read once "
        f"at {time.strftime('%H:%M:%S', time.localtime(read_at))}. Press Reload "
        "to ask again; nothing here is polled in the background."
    )))

    link = common.group("Phone link", "The backend this panel reads.")
    _add_row(link, _row(
        BACKEND_TITLE.get(str(which), str(which)),
        "Read through the phone link only. Calls, texts and files over plain "
        "Bluetooth are on the Phone page.",
    ))
    body.append(link)

    group = common.group("Paired devices")
    for device in devices:
        _add_row(group, _device_row(device, read_at))
    body.append(group)

    set_content(common.scrolled(body))
    page.status = recorder.status
    return page


__all__ = ["TITLE", "ICON", "build"]
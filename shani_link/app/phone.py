"""The Phone page: your landline and your phone, from this computer.

Calls (a dialer shaped like a car's, answer, hang up, hold, swap, merge, the
call's picture, recent calls), messages (conversations, a thread, reply),
contacts (search, call, text), the phone's music, files both ways, and the
remote (this computer as the phone's Bluetooth keyboard: its camera shutter,
media and volume keys, through `shani_link.phone_remote`). It adds
no transport: the landline is the service's (`line.py`, over D-Bus), the phone
is `shani_link.phone` (GSConnect, KDE Connect, or plain Bluetooth through
`phone_bluez`), and Bluetooth call audio is `shani_link.bt_telephony`.

Moved here from Shani Chronoa's Phone panel. What did not come along is the
assistant's consent: a person pressing Call is the decision, so nothing here
asks a switch first. What runs with the window closed - the ringing call, the
text notifications, receiving files - has its switch on the Background page.

**Nothing slow runs on the main thread.** Listing messages over Bluetooth MAP
and pulling contacts over PBAP each take seconds (measured ~24 s for 813
contacts plus 200 messages), so every read and every action runs in a thread
and hands its result back with `GLib.idle_add`.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from shani_link import phone as ph  # noqa: E402
from shani_link.app import common  # noqa: E402
from shani_link.app.common import plural  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Phone"
ICON = "call-start-symbolic"
SUBTITLE = ("Calls on your landline and your phone, its messages, contacts, "
            "music and files - through the phone link, or plain Bluetooth "
            "when there is none. Your watch is on the Watch page.")
#: Reading the phone takes seconds; build this page when it is opened, not at start.
PREBUILD = False

ROW_CSS = "phone-row"


def _in_thread(work: Callable[[], Any], done: Callable[[Any], None]) -> None:
    """Run `work` off the main thread and hand its result (or exception) to `done`."""
    def run():
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001 - shown, not raised
            logger.warning("phone surface: %s", exc, exc_info=True)
            result = exc
        GLib.idle_add(lambda: (done(result), False)[1])
    threading.Thread(target=run, daemon=True).start()


def _call_tool(arguments: dict) -> str:
    """Bluetooth call control: shani-link's engine, asked directly."""
    from shani_link import bt_telephony
    return str(bt_telephony._run(arguments))


def _media(action: str) -> str:
    """Play, pause, skip or say what is playing: a player on this computer
    (MPRIS) first, else the phone's own player over Bluetooth (AVRCP)."""
    from shani_link import mpris, phone_bluez
    if mpris.players():
        if action == "status":
            player = mpris.current()
            what = mpris.now_playing(player) or "nothing named"
            return f"{player.rsplit('.', 1)[-1]} is {mpris.status(player).lower()}: {what}."
        return mpris.control(action)
    found = phone_bluez.avrcp_players()
    if not found:
        return "No media player is running here, and no phone is sharing its music over Bluetooth."
    path, name = found[0]
    if action == "status":
        state, song = phone_bluez.avrcp_status(path)
        return f"{name} is {str(state).lower()}" + (f": {song}." if song else ".")
    ok, why = phone_bluez.avrcp(path, action)
    return f"Done: {action} on {name}." if ok else f"Not done: {why}"


def _when(ms: int) -> str:
    if not ms:
        return ""
    t = ms / 1000
    return time.strftime("%H:%M" if time.time() - t < 86400 else "%d %b %H:%M", time.localtime(t))


def _clear(box: Gtk.Widget) -> None:
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


def _label(text: str, dim: bool = False, wrap: bool = True) -> Gtk.Label:
    lab = Gtk.Label(label=text, xalign=0.0, wrap=wrap, selectable=False)
    lab.set_wrap_mode(Pango.WrapMode.WORD_CHAR)  # a long number or link must not widen the page
    if dim:
        lab.add_css_class("dim-label")
    return lab


def _human_size(path) -> str:
    """`4.7 MB`, and "unknown size" when `stat` fails.

    A received file's *size* is the second thing anybody asks - the first is its
    name - and a row with a size nobody can read is a number that reads as
    complete.
    """
    try:
        number = float(path.stat().st_size)
    except OSError:
        return "unknown size"
    if number < 1024:
        return f"{int(number)} B"
    for unit in ("kB", "MB", "GB"):
        number /= 1024.0
        if number < 1024:
            return f"{number:.1f} {unit}"
    return f"{number:.1f} TB"


def _button(label: str, on_click: Callable[[], None], suggested: bool = False,
            icon: str = "") -> Gtk.Button:
    btn = Gtk.Button(label=label) if not icon else Gtk.Button(icon_name=icon, tooltip_text=label)
    if suggested:
        btn.add_css_class("suggested-action")
    btn.connect("clicked", lambda _b: on_click())
    btn.set_valign(Gtk.Align.CENTER)
    return btn


def _list() -> Gtk.ListBox:
    lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    lb.add_css_class("boxed-list")
    return lb


def _preview(body: str, width: int = 80) -> str:
    """A message body as one readable line, cut on a grapheme boundary.

    **`common.clip`, not `body[:80]`.** A message body is sender- or
    model-supplied text, and a hard slice cuts a grapheme cluster — "👨‍👩‍👧‍👦"
    is seven code points held together by ZWJ, and cutting after the second
    leaves a ZWJ with no emoji, which draws the missing-glyph box in a row
    somebody is trying to read. `clip` counts columns, so a CJK-heavy message
    does not overflow the row either.

    A named function rather than an inline expression, because the only way to
    test the inline version was to build the whole panel.
    """
    line = (body or "(no text)").replace("\n", " ")
    return common.clip(line, width)


def _item(title: str, subtitle: str = "", suffix: Optional[Gtk.Widget] = None) -> Gtk.Widget:
    # `common.row` escapes the title itself since 2026-10-11; this used to
    # hand-roll it, which is the workaround that made the helper's own contract
    # untrue. Escaping in both places now would double-escape and show `&amp;`.
    row = common.row(title or "(unknown)", subtitle, suffix=suffix)
    row.add_css_class(ROW_CSS)
    return row


class PhonePanel:
    def __init__(self, app: Any):
        self.app = app
        self.device = None
        self.book: "list[tuple[str, tuple[str, ...]]]" = []
        self.status_recorder = common.StatusRecorder()

    # --- shared bits ---------------------------------------------------------

    def name_for(self, number: str) -> str:
        return ph.name_for(number, self.book) if self.book else ""

    def notice(self, box: Gtk.Box, text: str) -> None:
        _clear(box)
        box.append(_label(text, dim=True))

    # --- the page ------------------------------------------------------------

    #: The panel's own look, installed once per display by `build`.
    #:
    #: **The tab bar is the panel's widest thing and it has to fit.**
    #:
    #: `GtkStackSwitcher` is the only `Gtk.StackSwitcher` in this tree, and
    #: GTK gives each of its buttons a fixed minimum of about 133px - measured
    #: with a bare switcher and five short titles, so it is the switcher and
    #: not this panel's labels. Five tabs therefore needed **666px**, and this
    #: panel's narrow budget is 480: the tab bar was 70% wider than the
    #: window it had to fit in, so it was clipped or gave a sideways
    #: scrollbar. The theme's floor is a *style* choice (even tab buttons)
    #: and this panel cannot afford it.
    #:
    #: With the floor removed the five tabs need **389px** and the panel
    #: measures 413 - inside the budget, with room to spare. The sixth tab
    #: (Remote) brings the tab bar to 476px, measured the same way: inside 480. The buttons are
    #: then as wide as their labels, which is how a tab bar looks in most
    #: applications; GTK keeps the selection border inside the button, so
    #: choosing a tab does not move the others.
    PANEL_CSS = b"""
/* The Phone panel's tab bar. */
stackswitcher button {
    min-width: 0;
}
"""

    def _install_panel_css(self) -> None:
        """Put the panel's own stylesheet on the display, once.

        Here rather than in the window's stylesheet, so the panel looks the
        same wherever it is shown - the same reason the dialer's own sheet
        lives here.
        """
        display = Gdk.Display.get_default()
        if display is None or getattr(PhonePanel, "_panel_css_installed", False):
            return
        provider = Gtk.CssProvider()
        provider.load_from_data(self.PANEL_CSS, len(self.PANEL_CSS))
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        PhonePanel._panel_css_installed = True

    def build(self) -> Gtk.Widget:
        page, set_content = common.surface(TITLE, SUBTITLE)
        page.status = self.status_recorder.status
        body = common.page_body(12)
        for side in ("top", "bottom", "start", "end"):
            getattr(body, f"set_margin_{side}")(12)
        # The dot in the sidebar is whatever this row says: drawn through the
        # recorder and redrawn when the phone answers, never set without a row.
        self.status_box = common.page_body(0)
        body.append(self.status_box)
        self._install_panel_css()
        self.set_status(common.STATUS_UNKNOWN, "Looking for your phone...")

        stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        switcher = Gtk.StackSwitcher(stack=stack, halign=Gtk.Align.CENTER)
        body.append(switcher)
        body.append(stack)
        for name, title, builder in (("calls", "Calls", self.calls_tab),
                                     ("messages", "Messages", self.messages_tab),
                                     ("contacts", "Contacts", self.contacts_tab),
                                     ("music", "Music", self.music_tab),
                                     ("files", "Files", self.files_tab),
                                     ("remote", "Remote", self.remote_tab)):
            stack.add_titled(builder(), name, title)
        set_content(common.scrolled(body))
        self.find_phone()
        return page

    def set_status(self, status: str, summary: str, detail: str = "") -> None:
        _clear(self.status_box)
        self.status_box.append(self.status_recorder.row(status, summary, detail))

    def find_phone(self) -> None:
        def work():
            phones = [d for d in ph.devices() if d.paired]
            dev = next((d for d in phones if d.reachable), phones[0] if phones else None)
            battery = ph.battery(dev) if dev is not None and dev.reachable else None
            return dev, battery, ph.backend()

        def done(result):
            if isinstance(result, Exception):
                self.set_status(common.STATUS_ATTENTION, "No phone", str(result))
                return
            dev, battery, which = result
            self.device = dev
            if dev is None:
                self.set_status(common.STATUS_ATTENTION, "No phone is paired",
                                "Pair it over Bluetooth for calls and texts")
                self.status_box.append(common.connect_button(
                    self.app, "bluetooth", "Pair a phone"))
                return
            how = {"bluetooth": "Bluetooth", "gsconnect": "GSConnect",
                   "kdeconnect": "KDE Connect"}.get(str(which), str(which))
            self.set_status(
                common.STATUS_OK if dev.reachable else common.STATUS_ATTENTION,
                f"{dev.name} - {'connected' if dev.reachable else 'not connected'}",
                f"through {how}" + (f", battery {battery[0]}%" if battery else ""))
            self.load_contacts()
            self.load_history()
            self.load_conversations()
        _in_thread(work, done)

    # --- calls ---------------------------------------------------------------

    #: The dialer's look, installed once per display by `calls_tab`. Here,
    #: not in the window's stylesheet, so the panel looks the same wherever it
    #: is shown (a lone render showed square grey keys without it).
    DIALER_CSS = b"""
/* The Phone panel's dialer, shaped like a car's: one big number,
   round keys a thumb can hit, one green call and one red end. */
.dialer-number {
    font-size: 30px;
    font-weight: 300;
    letter-spacing: 2px;
    background: none;
    border: none;
    box-shadow: none;
    min-height: 48px;
}
.dialer-match { font-size: 14px; }
.dialer-key {
    min-width: 68px;
    min-height: 68px;
    border-radius: 9999px;
    padding: 0;
}
.dialer-key .digit { font-size: 24px; font-weight: 500; }
.dialer-key .letters { font-size: 10px; letter-spacing: 1px; opacity: 0.6; }
.dialer-call, .dialer-end, .dialer-answer {
    min-width: 64px;
    min-height: 64px;
    border-radius: 9999px;
    padding: 0;
    color: white;
}
.dialer-call, .dialer-answer { background: #26a269; }
.dialer-call:hover, .dialer-answer:hover { background: #2ec27e; }
.dialer-end { background: #c01c28; }
.dialer-end:hover { background: #e01b24; }
.dialer-call image, .dialer-end image, .dialer-answer image { -gtk-icon-size: 28px; }
.dialer-incall {
    padding: 12px 18px;
    border-radius: 18px;
}
"""

    #: Letters under each key, as on every phone keypad.
    KEY_LETTERS = {"2": "ABC", "3": "DEF", "4": "GHI", "5": "JKL", "6": "MNO",
                   "7": "PQRS", "8": "TUV", "9": "WXYZ", "0": "+", "1": "", "*": "", "#": ""}

    def calls_tab(self) -> Gtk.Widget:
        """A dialer shaped like a car's: one big number, round keys, one green call.

        **Why this shape.** The old tab was an entry, a grid of 56px square
        buttons and a row of three text buttons ("Answer", "Hang up", "Call
        status") that looked identical whether a call was ringing or not. In a
        car - the other place a computer dials for you - the screen is read at
        a glance and hit with a thumb: the number is the biggest thing on it,
        the keys are round and large, the only colours are green to call and
        red to end, and typing digits shows the contact they match. The call
        controls are one card, shown as a call bar, with the audio route
        beside them where it matters.
        """
        display = Gdk.Display.get_default()
        if display is not None and not getattr(PhonePanel, "_css_installed", False):
            provider = Gtk.CssProvider()
            provider.load_from_data(self.DIALER_CSS, len(self.DIALER_CSS))
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            PhonePanel._css_installed = True
        outer = Adw.Clamp(maximum_size=420)
        box = common.page_body(14)
        outer.set_child(box)

        # The JioFiber landline strip: its state, and the way to add this
        # computer to the line (the dialer below calls through it when it is up).
        box.append(self._landline_strip())

        # The far end's picture. Hidden until a call carries video, because a
        # grey rectangle that is always on screen reads as a broken camera rather
        # than as a call with no video in it.
        box.append(self._video_surface())

        # The in-call bar: answer / end, and where the audio goes.
        incall = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        incall.add_css_class("card")
        incall.add_css_class("dialer-incall")
        self.answer_button = self._round_button("call-start-symbolic", "Answer",
                                                "dialer-answer", lambda: self.call_action("answer"))
        self.end_button = self._round_button("call-stop-symbolic", "Hang up",
                                             "dialer-end", lambda: self.call_action("hangup"))
        incall.append(self.answer_button)
        incall.append(self.end_button)
        route = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
        route.append(_label("Call audio", dim=True, wrap=False))
        toggles = Gtk.Box(spacing=0)
        toggles.add_css_class("linked")
        self.on_pc = Gtk.ToggleButton(label="This PC")
        self.on_phone = Gtk.ToggleButton(label="Phone", group=self.on_pc)
        self.on_pc.connect("toggled", lambda b: b.get_active() and self.route("pc"))
        self.on_phone.connect("toggled", lambda b: b.get_active() and self.route("phone"))
        toggles.append(self.on_pc)
        toggles.append(self.on_phone)
        route.append(toggles)
        incall.append(route)
        # Mute, and the running call timer beside it.
        self.mute_button = Gtk.ToggleButton(icon_name="microphone-sensitivity-muted-symbolic",
                                            valign=Gtk.Align.CENTER, tooltip_text="Mute the microphone")
        self.mute_button.add_css_class("circular")
        self.mute_button.connect("toggled", self._on_mute)
        self.mute_button.update_property([Gtk.AccessibleProperty.LABEL], ["Mute"])
        incall.append(self.mute_button)
        # Hold and Swap. They appear together because they are one gesture on a
        # phone: Hold puts the live call aside and Swap trades it for whatever is
        # aside. Both are hidden until there is something to hold, so a dialer
        # with one call does not carry controls that would refuse.
        self.hold_button = Gtk.Button(icon_name="media-playback-pause-symbolic",
                                      valign=Gtk.Align.CENTER,
                                      tooltip_text="Hold this call")
        self.hold_button.add_css_class("circular")
        self.hold_button.connect("clicked", lambda _b: self.call_action("hold"))
        self.hold_button.update_property([Gtk.AccessibleProperty.LABEL], ["Hold"])
        self.hold_button.set_visible(False)
        incall.append(self.hold_button)
        self.swap_button = Gtk.Button(icon_name="media-playlist-consecutive-symbolic",
                                      valign=Gtk.Align.CENTER,
                                      tooltip_text="Swap to the other call")
        self.swap_button.add_css_class("circular")
        self.swap_button.connect("clicked", lambda _b: self.call_action("swap"))
        self.swap_button.update_property([Gtk.AccessibleProperty.LABEL], ["Swap"])
        self.swap_button.set_visible(False)
        incall.append(self.swap_button)
        self.merge_button = Gtk.Button(icon_name="view-list-ordered-symbolic",
                                       valign=Gtk.Align.CENTER,
                                       tooltip_text="Merge all live calls")
        self.merge_button.add_css_class("circular")
        self.merge_button.connect("clicked", lambda _b: self.call_action("merge"))
        self.merge_button.update_property([Gtk.AccessibleProperty.LABEL], ["Merge"])
        self.merge_button.set_visible(False)
        incall.append(self.merge_button)
        self.call_timer = _label("", dim=True, wrap=False)
        self.call_timer.set_valign(Gtk.Align.CENTER)
        incall.append(self.call_timer)
        status = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text="Call status")
        status.add_css_class("flat")
        status.add_css_class("circular")
        status.connect("clicked", lambda _b: self.call_action("status"))
        status.update_property([Gtk.AccessibleProperty.LABEL], ["Call status"])
        incall.append(status)
        box.append(incall)

        # The number: as big as the screen allows, with backspace beside it.
        display = Gtk.Box(spacing=6)
        self.number = Gtk.Entry(hexpand=True, placeholder_text="Number to call",
                                input_purpose=Gtk.InputPurpose.PHONE, xalign=0.5)
        self.number.add_css_class("dialer-number")
        self.number.connect("activate", lambda _e: self.call(self.number.get_text()))
        self.number.connect("changed", lambda _e: self._show_match())
        backspace = Gtk.Button(icon_name="edit-clear-symbolic", valign=Gtk.Align.CENTER,
                               tooltip_text="Delete the last digit")
        backspace.add_css_class("flat")
        backspace.add_css_class("circular")
        backspace.connect("clicked", lambda _b: self.number.set_text(self.number.get_text()[:-1]))
        backspace.update_property([Gtk.AccessibleProperty.LABEL], ["Delete the last digit"])
        spacer = Gtk.Box()
        spacer.set_size_request(34, -1)
        display.append(spacer)
        display.append(self.number)
        display.append(backspace)
        box.append(display)
        self.match = _label("", dim=True, wrap=False)
        self.match.set_xalign(0.5)
        self.match.add_css_class("dialer-match")
        box.append(self.match)

        keys = Gtk.Grid(column_spacing=18, row_spacing=12, halign=Gtk.Align.CENTER)
        for i, k in enumerate("123456789*0#"):
            keys.attach(self._dial_key(k), i % 3, i // 3, 1, 1)
        box.append(keys)

        call = Gtk.Box(halign=Gtk.Align.CENTER, margin_top=4)
        self.call_button = self._round_button("call-start-symbolic", "Call", "dialer-call",
                                              lambda: self.call(self.number.get_text()))
        call.append(self.call_button)
        box.append(call)

        self.call_note = _label("", dim=True)
        self.call_note.set_xalign(0.5)
        self.call_note.set_justify(Gtk.Justification.CENTER)
        self._call_note_row, self._call_note_btn = self._note_row()
        self._call_note_row.set_halign(Gtk.Align.CENTER)
        self._call_note_row.append(self.call_note)
        box.append(self._call_note_row)
        self.devices_note = _label("", dim=True)
        self.devices_note.add_css_class("caption")
        box.append(self.devices_note)
        self.show_route()
        head = _label("Recent calls")
        head.add_css_class("heading")
        box.append(head)
        self.history = common.page_body(0)
        box.append(self.history)
        self._wire_landline_events()
        return outer

    # --- live landline call state ---------------------------------------------
    def _wire_landline_events(self) -> None:
        """Reflect the landline's own events in the dialer, live.

        The call happens on the landline's thread; its events arrive there too,
        so each one is marshalled onto the GTK loop before it touches a widget.
        Subscribed through the facade (`landline.listen`), never the engine, so
        the other session's work on the SIP layer cannot break this.
        """
        try:
            from shani_link.app import line as landline
            if getattr(self, "_landline_unsub", None):
                self._landline_unsub()
            self._landline_unsub = landline.listen(self._on_landline_event)
            # The picture rides its own channel: `listen` is for call state, and
            # fifteen frames a second through the same list would put a copy of
            # every frame in the event queue behind each one.
            if getattr(self, "_video_unsub", None):
                self._video_unsub()
            self._video_unsub = landline.video_sink(self._on_frame)
        except Exception:  # noqa: BLE001 - no landline is just a quiet dialer
            self._landline_unsub = None
            self._video_unsub = None

    def _video_surface(self) -> Gtk.Widget:
        """The far end's picture, fed a texture one decoded frame at a time.

        Drawn rather than a `Gtk.Image` from a file: the frames arrive as raw
        rgb24 off the decoder's thread fifteen times a second, and writing one
        to disk per frame in order to show it is fifteen file writes to draw a
        picture. `Gdk.MemoryTexture` wraps the bytes the decoder already has.

        **No toggle, and that is deliberate.** A per-call video switch needs a
        re-INVITE to add or drop the `m=video` line, and the offer decides at
        INVITE time whether this machine has a camera at all. A button that
        looks like it turns video off and does not is the dead-control class
        this panel has been bitten by twice. When there is no picture the row
        says which of the two reasons it is - here rather than left to guess.
        """
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        frame.add_css_class("card")
        self.video_box = frame
        self.video_placeholder = _label("", dim=True)
        self.video_placeholder.set_xalign(0.5)
        self.video_placeholder.set_vexpand(True)
        self.video_placeholder.set_justify(Gtk.Justification.CENTER)
        self.video_picture = Gtk.Picture()
        self.video_picture.set_can_shrink(True)
        # 16:9 held by the aspect frame rather than by the texture, so a
        # differently-shaped source is letterboxed instead of stretched.
        aspect = Gtk.AspectFrame(xalign=0.5, yalign=0.5, ratio=16.0 / 9.0,
                                 obey_child=False)
        aspect.set_size_request(-1, 170)
        aspect.set_child(self.video_picture)
        frame.append(self.video_placeholder)
        frame.append(aspect)
        frame.set_visible(False)
        return frame

    def _on_frame(self, rgb: bytes, width: int, height: int) -> None:
        """A decoded picture, from the decoder's own worker thread.

        Marshalled onto the GTK loop here rather than in the decoder, because the
        decoder is a plain thread and building GTK objects on it would put every
        other widget in this app one frame behind.
        """
        GLib.idle_add(self._paint_frame, bytes(rgb), int(width), int(height))

    def _paint_frame(self, rgb: bytes, width: int, height: int) -> bool:
        """One frame onto the picture; anything unusable leaves the last one up.

        The length check is **exact**, and that is load-bearing rather than
        tidiness: GLib refuses a buffer *shorter* than the stride it is given
        (measured: `gdk_memory_texture_new` asserts and returns NULL), but it
        accepts a longer one without complaint - so an over-long buffer would be
        drawn as a stretched frame of someone else's pixels. The decoder hands
        over exactly one picture, so exactly one picture is what is drawn.
        """
        if width < 1 or height < 1 or len(rgb) != width * height * 3:
            return False
        try:
            texture = Gdk.MemoryTexture.new(
                width, height, Gdk.MemoryFormat.R8G8B8,
                GLib.Bytes.new(rgb), width * 3)
        except Exception:  # noqa: BLE001 - a frame we cannot wrap is a dropped frame
            logger.debug("landline: could not wrap a %dx%d frame", width, height)
            return False
        self.video_picture.set_paintable(texture)
        self._video_seen = True
        if self.video_placeholder is not None and self.video_placeholder.get_visible():
            self.video_placeholder.set_visible(False)
        if self.video_box is not None and not self.video_box.get_visible():
            self.video_box.set_visible(True)
        return False

    def _show_video_state(self) -> None:
        """When a call is up, say what the picture is doing - including nothing.

        A hidden box and an empty one look the same to somebody who does not know
        which to expect, and both say "this call has no video". Saying *which* of
        the two reasons it is - no camera here, or the far end answered without
        one - is the difference between a fact and a grey rectangle, and the two
        have different remedies.
        """
        if self.video_box is None or getattr(self, "_video_seen", False):
            return
        try:
            from shani_link.app import line as landline
            why = ("The other side answered this call without video."
                   if landline.video_available() else
                   "No picture: this computer has no camera to send one from "
                   "(video needs ffmpeg and a camera).")
        except Exception:  # noqa: BLE001 - no facade is still a reason
            why = "No picture came through on this call."
        self.video_placeholder.set_text(why)
        self.video_box.set_visible(True)

    def _hide_video(self) -> None:
        self._video_seen = False
        if self.video_placeholder is not None:
            self.video_placeholder.set_visible(True)
        if self.video_box is not None:
            self.video_box.set_visible(False)

    def _on_landline_event(self, kind: str, **info: Any) -> None:
        from gi.repository import GLib
        GLib.idle_add(self._apply_landline_event, kind, info)

    def _apply_landline_event(self, kind: str, info: dict) -> bool:
        who = info.get("name") or info.get("number") or ""
        note = getattr(self, "call_note", None)
        if note is not None:
            text = {
                "incoming": f"Incoming landline call from {who}",
                "ringing": f"Calling {who} on your landline...",
                "answered": f"On a landline call with {who}",
                "ended": f"Landline call ended: {info.get('reason', '')}".rstrip(": "),
            }.get(kind)
            if text:
                note.set_text(text)
        # Answer only makes sense while a call is ringing in.
        if getattr(self, "answer_button", None) is not None:
            self.answer_button.set_sensitive(kind == "incoming")
        if kind == "answered":
            self._start_call_timer()
            self._show_video_state()
        elif kind == "ended":
            self._stop_call_timer()
            self._hide_video()
        if kind in ("answered", "ended"):
            self._refresh_call_controls()
        if kind in ("registered", "unregistered") and hasattr(self, "_landline_status"):
            self._refresh_landline()
        if kind == "ended" and hasattr(self, "history"):
            self.load_history()  # the call just logged; refresh recents
        return False  # one-shot idle

    def _on_mute(self, button: Gtk.ToggleButton) -> None:
        from shani_link.app import line as landline
        on = button.get_active()
        landline.set_muted(on)
        button.set_tooltip_text("Unmute the microphone" if on else "Mute the microphone")
        if getattr(self, "call_note", None) is not None and landline.in_call():
            self.call_note.set_text("Microphone muted" if on else "Microphone on")

    def _start_call_timer(self) -> None:
        import time
        from gi.repository import GLib
        self._call_started = time.monotonic()
        self._stop_call_timer(clear=False)

        def tick() -> bool:
            if getattr(self, "call_timer", None) is None:
                return False
            secs = int(time.monotonic() - self._call_started)
            self.call_timer.set_text(f"{secs // 60}:{secs % 60:02d}")
            return True
        tick()
        self._call_timer_id = GLib.timeout_add_seconds(1, tick)

    def _stop_call_timer(self, clear: bool = True) -> None:
        from gi.repository import GLib
        tid = getattr(self, "_call_timer_id", 0)
        if tid:
            GLib.source_remove(tid)
            self._call_timer_id = 0
        if clear and getattr(self, "call_timer", None) is not None:
            self.call_timer.set_text("")
        # A call ending clears any mute, so the next call starts open.
        if clear and getattr(self, "mute_button", None) is not None:
            self.mute_button.set_active(False)

    @staticmethod
    def _round_button(icon: str, label: str, css: str, on_click: Callable[[], None]) -> Gtk.Button:
        button = Gtk.Button(icon_name=icon, tooltip_text=label, valign=Gtk.Align.CENTER)
        button.add_css_class(css)
        button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        button.connect("clicked", lambda _b: on_click())
        return button

    def _dial_key(self, key: str) -> Gtk.Button:
        face = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, valign=Gtk.Align.CENTER)
        digit = Gtk.Label(label=key)
        digit.add_css_class("digit")
        letters = Gtk.Label(label=self.KEY_LETTERS.get(key, "") or " ")
        letters.add_css_class("letters")
        face.append(digit)
        face.append(letters)
        button = Gtk.Button(child=face)
        button.add_css_class("dialer-key")
        button.update_property([Gtk.AccessibleProperty.LABEL], [key])
        button.connect("clicked", lambda _b: self._key_pressed(key))
        # Hold 0 for "+", as every phone does.
        if key == "0":
            hold = Gtk.GestureLongPress()
            hold.connect("pressed", lambda *_: self.number.set_text(self.number.get_text() + "+"))
            button.add_controller(hold)
        return button

    def _key_pressed(self, key: str) -> None:
        """A keypad press: a tone down the live call, or a digit into the field.

        During an active landline call the keypad is the phone's keypad - it
        sends DTMF so you can work a menu ("press 1 for ...") - not a text field.
        """
        try:
            from shani_link.app import line as landline
            if landline.in_call():
                landline.dtmf(key)
                self.number.set_text(self.number.get_text() + key)
                return
        except Exception:  # noqa: BLE001
            pass
        self.number.set_text(self.number.get_text() + key)

    def _show_match(self) -> None:
        """The contact the typed digits belong to, under the number."""
        typed = "".join(ch for ch in self.number.get_text() if ch.isdigit() or ch == "+")
        name = self.name_for(typed) if len(typed) >= 6 else ""
        self.match.set_text(name)

    def _number_for_name(self, name: str) -> str:
        """A number for a typed contact name: the loaded book first, then the cache."""
        want = "".join(name.lower().split())
        for cname, numbers in (self.book or []):
            if numbers and "".join(str(cname).lower().split()) == want:
                return str(numbers[0])
        for cname, numbers in (self.book or []):
            if numbers and want in "".join(str(cname).lower().split()):
                return str(numbers[0])
        try:
            from shani_link.app import line as landline
            return landline.resolve_name(name)
        except Exception:  # noqa: BLE001
            return ""

    def _note_row(self) -> "tuple[Gtk.Box, None]":
        """A row for a dim note (the second element is kept for the old shape:
        Chronoa put a "Turn on" button for its assistant switch there)."""
        return Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6), None

    def call(self, number: str) -> None:
        number = (number or "").strip()
        if not number:
            self.call_note.set_text("Type a number first.")
            return
        # A typed name instead of a number: look it up in the contact book.
        if any(ch.isalpha() for ch in number):
            resolved = self._number_for_name(number)
            if not resolved:
                self.call_note.set_text(f"No contact matching '{number}'.")
                return
            number = resolved
        # The JioFiber landline, when it is connected, is the same dialer's
        # first line - it needs no phone and no Bluetooth (landline/__init__.py).
        if self._landline_ready():
            from shani_link.app import line as landline
            from shani_link.app.line import dial_user
            try:
                landline.dial(number)
                self.call_note.set_text(f"Calling {dial_user(number)} on your landline. "
                                        "Hang up here when you are done.")
            except (RuntimeError, ValueError) as exc:
                self.call_note.set_text(f"Not called: {exc}")
            return
        if self.device is None:
            self.call_note.set_text("No phone is connected.")
            return
        self.call_note.set_text(f"Calling {number}...")
        dev = self.device

        def done(result):
            if isinstance(result, Exception):
                self.call_note.set_text(f"Not called: {result}")
            elif ph.places_calls():
                self.call_note.set_text(f"Calling {number} through {dev.name}. Hang up here or on the phone.")
            else:
                self.call_note.set_text(f"Opened the dialer on {dev.name} with {number} - press call there.")
        _in_thread(lambda: ph.dial(dev, number), done)

    def show_route(self) -> None:
        """Which way the toggle points, and the PC's speaker and mic by name."""
        def work():
            from shani_link import bt_telephony as bc
            from shani_link import audio as pipewire
            gws = bc.gateways()
            where = bc.audio_route(gws[0][0])[0] if gws else ""
            names = []
            for target in ("@DEFAULT_AUDIO_SINK@", "@DEFAULT_AUDIO_SOURCE@"):
                out = pipewire.run_wpctl("inspect", target)
                text = getattr(out, "stdout", out) or ""
                hit = [ln.split("=", 1)[1].strip().strip('"') for ln in str(text).splitlines()
                       if "node.description" in ln]
                names.append(hit[0] if hit else "unknown")
            return where, names

        def done(result):
            if isinstance(result, Exception):
                self.devices_note.set_text(f"Call audio route unknown: {result}")
                return
            where, (speaker, mic) = result
            self._routing = True
            (self.on_pc if where == "pc" else self.on_phone).set_active(True)
            self._routing = False
            self.devices_note.set_text(f"On this PC a call uses the speaker '{speaker}' and the "
                                       f"microphone '{mic}' (the system defaults; change them in Sound settings).")
        _in_thread(work, done)

    def route(self, where: str) -> None:
        if getattr(self, "_routing", False):
            return
        self.call_note.set_text("...")
        _in_thread(lambda: _call_tool({"action": "audio", "to": where}),
                   lambda r: self.call_note.set_text(str(r).split(" (unverified")[0]))

    def call_action(self, action: str) -> None:
        # Answer and Hang up act on whichever line has the call. A landline call
        # in progress is answered and ended on the landline; otherwise the
        # paired phone, as before.
        from shani_link.app import line as landline
        # the line is shani-link's: what it says about calls, not its engine object
        has_call, up = bool(landline.calls()) or landline.in_call(), landline.ready()
        if action in ("hold", "swap", "merge"):
            # Hold and Swap are landline-only: the paired phone's route toggle
            # is a different control and there is no far-end call to hold here.
            try:
                # **`elif`, not three `if`s with an `else` on the last.**
                # The `else` used to belong to the *swap* test, so a hold
                # press also ran the merge branch - and `merge()` raises
                # "the landline is not connected" when there is no line, so
                # the note said that instead of "Held". The action and the
                # sentence disagreed, and the one the person read was the
                # wrong one. Measured: the hold note read "the landline is
                # not connected" while the hold had in fact been taken.
                if action == "hold":
                    self.call_note.set_text(
                        "Held - dial another number, or Swap."
                        if landline.hold() else
                        "That call was not held. It may already be held, or the "
                        "other side would not take it.")
                elif action == "swap":
                    self.call_note.set_text(
                        "Swapped." if landline.swap() else
                        "Not swapped: Swap needs two answered calls.")
                else:
                    self.call_note.set_text(
                        "Merged." if landline.merge() else
                        "Not merged: Merge needs at least two answered calls.")
            except RuntimeError as exc:
                self.call_note.set_text(str(exc))
            self._refresh_call_controls()
            return
        if has_call or (action == "answer" and up):
            try:
                landline.answer() if action == "answer" else landline.hangup()
                self.call_note.set_text("Answered." if action == "answer" else "Hung up.")
            except RuntimeError as exc:
                self.call_note.set_text(str(exc))
            return
        self.call_note.set_text("...")
        _in_thread(lambda: _call_tool({"action": action}),
                   lambda r: self.call_note.set_text(str(r)))

    def _refresh_call_controls(self) -> None:
        """Hold and Swap exist only while there is a call to put aside.

        Shown from the line's own state rather than tracked here, because a
        button that appears on a guess is a button nobody can rely on - and a
        dialer with one call carrying a Hold that refuses is the dead-control
        class this panel has been bitten by twice.
        """
        hold, swap, merge = (getattr(self, "hold_button", None),
                            getattr(self, "swap_button", None),
                            getattr(self, "merge_button", None))
        if hold is None or swap is None:
            return
        try:
            from shani_link.app import line as landline
            live = landline.calls()
        except Exception:  # noqa: BLE001 - no facade is no calls
            live = []
        answerable = [c for c in live if c.state == "active"]
        aside = [c for c in live if c.held]
        # Swap needs two answered calls; Hold needs one that is not already aside.
        swap.set_visible(bool(len(answerable) and aside))
        hold.set_visible(bool(answerable and not aside) or bool(swap.get_visible()))
        if merge is not None:
            # Merge needs at least two answered calls.
            merge.set_visible(bool(len(answerable) >= 2))

    @staticmethod
    def _landline_ready() -> bool:
        try:
            from shani_link.app import line as landline
            return landline.ready()
        except Exception:  # noqa: BLE001
            return False

    # --- the landline strip ---------------------------------------------------
    def _landline_strip(self) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("card")
        card.set_margin_bottom(4)
        for side in ("top", "bottom", "start", "end"):
            getattr(card, f"set_margin_{side}")(10)
        row = Gtk.Box(spacing=8)
        icon = Gtk.Image.new_from_icon_name("network-wireless-symbolic")
        self._landline_status = _label("", dim=True, wrap=True)
        self._landline_status.set_hexpand(True)
        self._landline_status.set_xalign(0)
        self._landline_button = Gtk.Button(valign=Gtk.Align.CENTER)
        self._landline_button.add_css_class("suggested-action")
        #: "setup" while turning on or adding, "confirm" while a code is awaited.
        self._landline_mode = "setup"
        self._landline_button.connect("clicked", lambda _b: (
            self._landline_confirm() if self._landline_mode == "confirm"
            else self._landline_setup_clicked()))
        row.append(icon)
        row.append(self._landline_status)
        row.append(self._landline_button)
        card.append(row)
        # Where the texted code is typed, shown only while adding this computer.
        self._landline_code = Gtk.Entry(placeholder_text="Code from the text message",
                                        input_purpose=Gtk.InputPurpose.DIGITS)
        self._landline_code.connect("activate", lambda _e: self._landline_confirm())
        self._landline_code.set_visible(False)
        card.append(self._landline_code)
        self._refresh_landline()
        return card

    def _refresh_landline(self) -> None:
        from shani_link.app import line as landline
        if not landline.is_set_up():
            self._landline_status.set_text("Add this computer to your JioFiber landline to call on it.")
            self._landline_button.set_label("Set up")
        elif not landline.enabled():
            self._landline_status.set_text("Your JioFiber landline is set up here and switched off.")
            self._landline_button.set_label("Turn on")
        else:
            self._landline_status.set_text(landline.status())
            self._landline_button.set_label("Forget")

    def _landline_setup_clicked(self) -> None:
        from shani_link.app import line as landline
        if landline.is_set_up() and not landline.enabled():
            try:
                landline.set_enabled(True)
            except RuntimeError as exc:
                self._landline_status.set_text(str(exc))
                return
            self._refresh_landline()
            return
        if landline.is_set_up():
            landline.forget()
            self._refresh_landline()
            return
        self._landline_status.set_text("Asking your router to add this computer...")
        self._landline_button.set_sensitive(False)

        def work():
            try:
                return landline.setup_start()
            except Exception as exc:  # noqa: BLE001
                return exc

        def done(result):
            self._landline_button.set_sensitive(True)
            if isinstance(result, Exception):
                self._landline_status.set_text(f"Could not start: {result}")
                self._refresh_landline()
                return
            self._landline_status.set_text(f"A code was texted to {result}. Type it here.")
            self._landline_code.set_visible(True)
            self._landline_code.grab_focus()
            self._landline_button.set_label("Confirm")
            self._landline_mode = "confirm"
        _in_thread(work, done)

    def _landline_confirm(self) -> None:
        from shani_link.app import line as landline
        code = self._landline_code.get_text().strip()
        self._landline_status.set_text("Confirming...")

        def work():
            try:
                number = landline.setup_finish(code)
                landline.start(self.app)
                return number
            except Exception as exc:  # noqa: BLE001
                return exc

        def done(result):
            self._landline_mode = "setup"
            self._landline_code.set_visible(False)
            self._landline_code.set_text("")
            if isinstance(result, Exception):
                self._landline_status.set_text(f"Not added: {result}")
            else:
                self._landline_status.set_text(f"Landline {result} added. Connecting...")
            self._refresh_landline()
        _in_thread(work, done)

    def load_history(self) -> None:
        # The landline keeps its own local log (placed / received / missed); show
        # it whenever there is one, with or without a phone connected.
        from shani_link.app import line as landline
        landline_rows = landline.history()
        if self.device is None or ph.backend() != "bluetooth":
            if landline_rows:
                self._show_landline_history(landline_rows)
            else:
                self.notice(self.history, "Recent calls are read over Bluetooth only.")
            return
        self.notice(self.history, "Reading recent calls...")
        from shani_link import phone_bluez
        dev = self.device

        def done(result):
            if isinstance(result, Exception):
                self.notice(self.history, f"Could not read recent calls: {result}")
                return
            _clear(self.history)
            lb = _list()
            for c in result[:30]:
                who = c["name"] or self.name_for(c["number"]) or c["number"]
                lb.append(_item(who, f"{c['kind'] or 'call'} - {_when(c['date'])}",
                                _button("Call", lambda n=c["number"]: self.call(n), icon="call-start-symbolic")))
            self.history.append(lb if result else _label("No recent calls.", dim=True))
        _in_thread(lambda: phone_bluez.call_history(dev.id, 30), done)

    def _show_landline_history(self, rows: "list[dict]") -> None:
        """Recent landline calls, newest first, each with one-tap redial."""
        _clear(self.history)
        lb = _list()
        for c in rows[:30]:
            number = c.get("number", "")
            who = c.get("name") or self.name_for(number) or number or "Unknown"
            kind = c.get("kind", "call")
            when = _when(c.get("when", 0) * 1000) if c.get("when") else ""
            lb.append(_item(who, f"{kind} · landline{(' · ' + when) if when else ''}",
                            _button("Call", lambda n=number: self.call(n),
                                    icon="call-start-symbolic")))
        self.history.append(lb)

    # --- messages -------------------------------------------------------------

    def messages_tab(self) -> Gtk.Widget:
        box = common.page_body(10)
        self.threads = common.page_body(0)
        box.append(self.threads)
        self.thread_title = _label("")
        box.append(self.thread_title)
        self.thread_view = common.page_body(4)
        box.append(self.thread_view)
        compose = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.to = Gtk.Entry(placeholder_text="To (number)", width_chars=14)
        self.text = Gtk.Entry(hexpand=True, placeholder_text="Message")
        self.text.connect("activate", lambda _e: self.send())
        compose.append(self.to)
        compose.append(self.text)
        compose.append(_button("Send", self.send, suggested=True))
        box.append(compose)
        self.send_note = _label("", dim=True)
        self._send_note_row, self._send_note_btn = self._note_row()
        self._send_note_row.append(self.send_note)
        box.append(self._send_note_row)
        return box

    def load_conversations(self) -> None:
        self.notice(self.threads, "Reading messages...")
        dev = self.device

        def done(result):
            if isinstance(result, Exception):
                self.notice(self.threads, f"Could not read messages: {result}")
                return
            _clear(self.threads)
            lb = _list()
            for m in result[:40]:
                who = ", ".join(self.name_for(a) or a for a in m.addresses) or "(unknown)"
                preview = _preview(m.body)
                lb.append(_item(who, f"{_when(m.date)}{'' if m.read else ' - unread'}: {preview}",
                                _button("Open", lambda m=m, who=who: self.open_thread(m, who))))
            self.threads.append(lb if result else _label("No messages.", dim=True))
        _in_thread(lambda: ph.conversations(dev), done)

    def open_thread(self, latest, who: str) -> None:
        self.thread_title.set_text(f"With {who}")
        self.to.set_text(latest.addresses[0] if latest.addresses else "")
        self.notice(self.thread_view, "Reading the conversation...")
        dev = self.device

        def done(result):
            if isinstance(result, Exception):
                self.notice(self.thread_view, f"Could not read it: {result}")
                return
            _clear(self.thread_view)
            for m in result:
                lab = _label(f"{'Them' if m.incoming else 'You'} ({_when(m.date)}): {m.body or '(no text)'}")
                lab.set_halign(Gtk.Align.START if m.incoming else Gtk.Align.END)
                self.thread_view.append(lab)
        _in_thread(lambda: ph.thread_messages(dev, latest.thread, 20), done)

    def send(self) -> None:
        number, text = self.to.get_text().strip(), self.text.get_text().strip()
        if not number or not text:
            self.send_note.set_text("Type a number and a message.")
            return
        if self.device is None:
            self.send_note.set_text("No phone is connected.")
            return
        self.send_note.set_text("Sending...")
        dev = self.device

        def done(result):
            if isinstance(result, Exception):
                self.send_note.set_text(f"Not sent: {result}")
            else:
                self.text.set_text("")
                self.send_note.set_text(f"Handed to {dev.name} to send to {number}. "
                                        "The phone does not report delivery back.")
        _in_thread(lambda: ph.send_sms(dev, (number,), text), done)

    # --- contacts ---------------------------------------------------------------

    def contacts_tab(self) -> Gtk.Widget:
        box = common.page_body(10)
        self.search = Gtk.SearchEntry(placeholder_text="Search contacts")
        self.search.connect("search-changed", lambda _e: self.show_contacts())
        box.append(self.search)
        self.contact_list = common.page_body(0)
        box.append(self.contact_list)
        return box

    def load_contacts(self) -> None:
        self.notice(self.contact_list, "Reading contacts...")
        dev = self.device

        def done(result):
            if isinstance(result, Exception):
                self.notice(self.contact_list, f"Could not read contacts: {result}")
                return
            self.book = sorted(result, key=lambda c: c[0].lower())
            # Remember them so the landline can be dialled by name later, even
            # with no phone connected and from the headless skill.
            try:
                from shani_link.app import line as landline
                landline.cache_contacts(self.book)
            except Exception:  # noqa: BLE001
                pass
            self.show_contacts()
        _in_thread(lambda: ph.contacts(dev), done)

    def show_contacts(self) -> None:
        if not self.book:
            return
        q = self.search.get_text().strip().lower()
        hits = [c for c in self.book if q in c[0].lower() or any(q in n for n in c[1])] if q else self.book
        _clear(self.contact_list)
        lb = _list()
        for name, numbers in hits[:60]:
            suffix = Gtk.Box(spacing=4)
            suffix.append(_button("Call", lambda n=numbers[0]: self.call(n), icon="call-start-symbolic"))
            suffix.append(_button("Text", lambda n=numbers[0]: self.to.set_text(n), icon="mail-message-new-symbolic"))
            lb.append(_item(name, ", ".join(numbers), suffix))
        self.contact_list.append(lb)
        if len(hits) > 60:
            self.contact_list.append(_label(f"{len(hits) - 60} more - search to narrow.", dim=True))

    # --- music ------------------------------------------------------------------

    def music_tab(self) -> Gtk.Widget:
        box = common.page_body(10)
        self.now = _label("Press Refresh to see what is playing.", dim=True)
        box.append(self.now)
        row = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        for label, icon, action in (("Previous", "media-skip-backward-symbolic", "previous"),
                                    ("Play or pause", "media-playback-start-symbolic", "toggle"),
                                    ("Next", "media-skip-forward-symbolic", "next"),
                                    ("Refresh", "view-refresh-symbolic", "status")):
            row.append(_button(label, lambda a=action: self.music(a), icon=icon))
        box.append(row)
        box.append(_label("Phone music appears through bluez's mpris-proxy, which must be "
                          "running (systemctl --user start mpris-proxy).", dim=True))
        return box

    def music(self, action: str) -> None:
        def work():
            if ph.backend() == "bluetooth":
                from shani_link import phone_bluez
                phone_bluez.ensure_music_bridge()
            return _media(action)
        _in_thread(work,
                   lambda r: self.now.set_text(str(r).split(" (unverified")[0]))

    # --- files ------------------------------------------------------------------

    def files_tab(self) -> Gtk.Widget:
        box = common.page_body(10)
        box.append(_button("Send a file to the phone...", self.pick_file, suggested=True))
        self.file_note = _label("The phone asks you to accept each file.", dim=True)
        box.append(self.file_note)
        box.append(_label("Files from the phone: share them to this computer over Bluetooth "
                          "(Share > Bluetooth on the phone). Shani Link asks before saving each one, "
                          "into your Downloads folder, while \"Receive files over Bluetooth\" "
                          "is on (Background).", dim=True))
        # **What actually arrived.** The sentence above has been here since the
        # receiver landed, and it was the whole of it: a person who shared
        # something to this computer had no way to see what had been saved or
        # where, so the only confirmation was a notification they might have
        # missed. `obex_receive` writes into Downloads by name and never
        # overwrites, so the folder *is* the record - it just was not shown.
        self.received = common.page_body(0)
        box.append(self.received)
        box.append(_label("", dim=True))            # the note, filled below
        self.received_note = box.get_last_child()
        _in_thread(self._received_work, self._received_done)
        return box

    def _received_work(self):
        """What has arrived over Bluetooth, newest first.

        `Downloads/` is the whole list on purpose: `obex_receive.deliver()` moves
        a completed file there and `free_path` never lets two share a name, so
        the folder is the store. Keeping a second index of it is how a shelf
        comes to disagree with the files it is describing.

        Sorted by `(mtime, name)` - the name as tiebreaker, because two files
        this machine wrote in the same instant are exactly what happens when a
        test seeds them, and what happens whenever a receiver moves two files
        quickly. Directory order is not a claim about anything, and the row
        above it says "newest first".
        """
        import pathlib
        from shani_link import obex_receive
        folder = pathlib.Path(obex_receive.downloads())
        try:
            found = sorted((p for p in folder.iterdir() if p.is_file()),
                           key=lambda p: (p.stat().st_mtime, p.name),
                           reverse=True)
        except OSError as exc:
            raise RuntimeError(f"could not read {folder} ({exc})") from exc
        return folder, found[:40]

    def _received_done(self, result):
        if isinstance(result, Exception):
            self.received_note.set_text(f"Could not read Downloads: {result}")
            return
        folder, found = result
        _clear(self.received)
        if not found:
            self.notice(self.received, "Nothing has arrived yet.")
            return
        self.received_note.set_text(f"{plural(len(found), 'file')} in {folder}, newest first.")
        for path in found:
            self.received.append(common.row(path.name, _human_size(path)))

    def pick_file(self) -> None:
        dialog = Gtk.FileDialog(title="Send to your phone", modal=True)
        root = self.file_note.get_root()

        def chosen(dlg, result):
            try:
                f = dlg.open_finish(result)
            except GLib.Error as exc:
                if exc.code != Gtk.DialogError.DISMISSED:   # cancelled is silence; anything else is said
                    self.file_note.set_text(f"The file dialog did not open: {exc.message}")
                return
            if f is not None and f.get_path():
                self.send_file(f.get_path())
        dialog.open(root if isinstance(root, Gtk.Window) else None, None, chosen)

    def send_file(self, path: str) -> None:
        if self.device is None:
            self.file_note.set_text("No phone is connected.")
            return
        import os
        name = os.path.basename(path)
        self.file_note.set_text(f"Sending {name} - accept it on the phone...")
        dev = self.device

        def done(result):
            if isinstance(result, Exception):
                self.file_note.set_text(f"Not sent: {result}")
                return
            ok, why = result
            self.file_note.set_text(f"Sent {name} to {dev.name}." if ok else f"Not sent: {why}")
        _in_thread(lambda: ph.act(dev, "share", path), done)


    # --- remote ----------------------------------------------------------------

    #: (label, icon, key of `phone_remote.KEYS`) - the keys a person reaches for
    REMOTE_KEYS = (("Take a photo", "camera-photo-symbolic", "shutter"),
                   ("Previous", "media-skip-backward-symbolic", "previous"),
                   ("Play or pause", "media-playback-start-symbolic", "play_pause"),
                   ("Next", "media-skip-forward-symbolic", "next"),
                   ("Volume down", "audio-volume-low-symbolic", "volume_down"),
                   ("Volume up", "audio-volume-high-symbolic", "volume_up"))
    REMOTE_SWITCH = "phone-remote-enabled"
    REMOTE_OFF = ("Turn on \u201cPhone as a remote\u201d on the Background page, then pair "
                  "\u201cChronoa Remote\u201d from the phone's Bluetooth settings, to press "
                  "its camera shutter and media keys from here.")

    def remote_tab(self) -> Gtk.Widget:
        """The phone's keys, pressed from here - only while its switch is on.

        The switch is read each time the tab is shown, so turning it on on the
        Background page and coming back is enough.
        """
        box = common.page_body(10)
        self.remote_off = common.page_body(8)
        self.remote_off.append(_label(self.REMOTE_OFF, dim=True))
        self.remote_off.append(_button("Open Background", self._open_background))
        box.append(self.remote_off)
        self.remote_keys = common.page_body(10)
        grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3,
                           column_spacing=6, row_spacing=6, homogeneous=True)
        self.remote_buttons = {}
        for label, icon, key in self.REMOTE_KEYS:
            button = Gtk.Button(child=Adw.ButtonContent(icon_name=icon, label=label),
                                tooltip_text=label)
            button.connect("clicked", lambda _b, k=key: self.remote_press(k))
            self.remote_buttons[key] = button
            grid.append(button)
        self.remote_keys.append(grid)
        self.remote_note = _label("In the phone's camera app, Take a photo presses the shutter.", dim=True)
        self.remote_keys.append(self.remote_note)
        box.append(self.remote_keys)
        box.connect("map", lambda *_a: self.refresh_remote())
        self.refresh_remote()
        return box

    def _remote_on(self) -> bool:
        switches = getattr(self.app, "switches", None)
        try:
            if switches is None:
                from shani_link import settings
                switches = settings.Settings()
            return bool(switches.get_bool(self.REMOTE_SWITCH, False))
        except Exception:  # noqa: BLE001 - unreadable is off
            logger.debug("could not read %s", self.REMOTE_SWITCH, exc_info=True)
            return False

    def refresh_remote(self) -> None:
        on = self._remote_on()
        self.remote_keys.set_visible(on)
        self.remote_off.set_visible(not on)

    def _open_background(self) -> None:
        show = getattr(self.app, "show_page", None)
        if callable(show):
            show("background")

    def remote_press(self, key: str) -> None:
        from shani_link import phone_remote
        self.remote_note.set_text("Pressing...")
        _in_thread(lambda: phone_remote.press(key),
                   lambda r: self.remote_note.set_text(
                       f"Not pressed: {r}" if isinstance(r, Exception) else str(r)))


def build(app: Any) -> Gtk.Widget:
    return PhonePanel(app).build()

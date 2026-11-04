"""Shani Link's window: a sidebar of pages, one page at a time beside it.

Pages: Phone, Watch, Devices (Paired, Android, Phone link, Screen - and Connect
a device), Radio, NFC, Remote, Background. Each page is built the first time it
is shown (reading a phone or a watch takes seconds) and again on Reload.

The pages were Shani Chronoa's device panels. They get a small context object
as their `app`: `switches` (the Background switches), `activate_action` (the one
action they fire: "connect-device") and `show_page` - the three things they
reached for on Chronoa's application, and nothing of an assistant.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from shani_link.app import common  # noqa: E402

logger = logging.getLogger(__name__)

APP_ID = "dev.shani.Link.App"
TITLE = "Shani Link"


def _phone(ctx):
    from shani_link.app import phone
    return phone.build(ctx)


def _watch(ctx):
    from shani_link.app import wearables
    return wearables.build(ctx)


def _paired(ctx):
    from shani_link.app import devices
    return devices.build(ctx)


def _android(ctx):
    from shani_link.app import android
    return android.build(ctx)


def _phone_link(ctx):
    from shani_link.app import phone_link
    return phone_link.build(ctx)


def _screen(ctx):
    from shani_link.app import screen_share
    return screen_share.build(ctx)


def _radio(ctx):
    from shani_link.app import tools_pages
    return tools_pages.build_radio(ctx)


def _nfc(ctx):
    from shani_link.app import tools_pages
    return tools_pages.build_nfc(ctx)


def _remote(ctx):
    from shani_link.app import tools_pages
    return tools_pages.build_remote(ctx)


def _background(ctx):
    from shani_link.app import background
    return background.build(ctx)


#: The Devices page's tabs: (id, title, icon, build)
DEVICE_TABS = (
    ("paired", "Paired", "network-wireless-symbolic", _paired),
    ("android", "Android", "phone-symbolic", _android),
    ("phone_link", "Phone link", "network-wireless-symbolic", _phone_link),
    ("screen", "Screen", "video-display-symbolic", _screen),
)

#: The sidebar: (id, title, icon, build). `devices` is built by the window.
PAGES = (
    ("phone", "Phone", "call-start-symbolic", _phone),
    ("watch", "Watch", "alarm-symbolic", _watch),
    ("devices", "Devices", "preferences-system-devices-symbolic", None),
    ("radio", "Radio", "audio-speakers-symbolic", _radio),
    ("nfc", "NFC", "auth-smartcard-symbolic", _nfc),
    ("remote", "Remote", "input-gaming-symbolic", _remote),
    ("background", "Background", "emblem-system-symbolic", _background),
)

#: Names Chronoa's panels went by (Connect a device's "Open ..." buttons, and
#: `shani-link-app --page=`): page, then a Devices tab where there is one.
ALIASES = {
    "wearables": ("watch", ""), "android": ("devices", "android"),
    "phone_link": ("devices", "phone_link"), "screen_share": ("devices", "screen"),
    "screen": ("devices", "screen"), "paired": ("devices", "paired"),
    "fm_radio": ("radio", ""), "ir_remote": ("remote", ""),
}


class Context:
    """What a page is handed as its `app`."""

    def __init__(self, window: "LinkWindow", switches: Any) -> None:
        self.window = window
        self.switches = switches
        #: Chronoa's pages read `app.config`; the only settings here are switches
        self.config = switches

    def activate_action(self, name: str, value: "GLib.Variant | None" = None) -> None:
        self.window.do_action(name, value.unpack() if value is not None else None)

    def show_page(self, name: str) -> None:
        self.window.show_page(name)


class LinkWindow(Adw.ApplicationWindow):
    def __init__(self, application: "Optional[Gtk.Application]" = None, switches: Any = None,
                 page: str = "phone") -> None:
        super().__init__(title=TITLE, default_width=980, default_height=760)
        if application is not None:
            self.set_application(application)
        if switches is None:
            from shani_link.app.switches import Switches
            switches = Switches()
        self.ctx = Context(self, switches)
        self._install_css()
        self.built: Dict[str, Gtk.Widget] = {}
        self.current = ""

        self.split = Adw.NavigationSplitView()
        self.split.set_min_sidebar_width(200)
        self.set_content(self.split)

        side = Adw.ToolbarView()
        side.add_top_bar(Adw.HeaderBar(title_widget=Adw.WindowTitle(title=TITLE)))
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("navigation-sidebar")
        self.rows: Dict[str, Gtk.ListBoxRow] = {}
        for name, title, icon, _build in PAGES:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8, margin_start=6, margin_end=6)
            box.append(Gtk.Image(icon_name=icon))
            box.append(Gtk.Label(label=title, xalign=0.0, hexpand=True))
            row.set_child(box)
            row.page_name = name
            row.update_property([Gtk.AccessibleProperty.LABEL], [title])
            self.list.append(row)
            self.rows[name] = row
        self.list.connect("row-activated", lambda _l, row: self.show_page(row.page_name))
        side.set_content(Gtk.ScrolledWindow(child=self.list, vexpand=True,
                                            hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.split.set_sidebar(Adw.NavigationPage(title=TITLE, child=side))

        content = Adw.ToolbarView()
        self.header = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title="")
        self.header.set_title_widget(self.title)
        reload = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Read this page again")
        reload.update_property([Gtk.AccessibleProperty.LABEL], ["Reload"])
        reload.connect("clicked", lambda _b: self.reload())
        self.header.pack_end(reload)
        connect = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Connect a device")
        connect.update_property([Gtk.AccessibleProperty.LABEL], ["Connect a device"])
        connect.connect("clicked", lambda _b: self.open_connect(""))
        self.header.pack_start(connect)
        content.add_top_bar(self.header)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        content.set_content(self.stack)
        self.content_page = Adw.NavigationPage(title=TITLE, child=content)
        self.split.set_content(self.content_page)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)
        self.show_page(page or "phone")

    # --- pages ---------------------------------------------------------------

    @staticmethod
    def _install_css() -> None:
        display = Gdk.Display.get_default()
        if display is None or getattr(LinkWindow, "_css_done", False):
            return
        provider = Gtk.CssProvider()
        provider.load_from_data(common.CSS, len(common.CSS))
        Gtk.StyleContext.add_provider_for_display(display, provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        LinkWindow._css_done = True

    def _build(self, name: str) -> Gtk.Widget:
        try:
            if name == "devices":
                return self._build_devices()
            builder = next(b for n, _t, _i, b in PAGES if n == name)
            return builder(self.ctx)
        except Exception as exc:  # noqa: BLE001 - one broken page is not the window
            logger.exception("page %s did not build", name)
            return common.empty_state("dialog-warning-symbolic", "This page could not be shown",
                                      f"{type(exc).__name__}: {exc}")

    def _build_devices(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, vexpand=True)
        stack = Adw.ViewStack(vexpand=True)
        box.tabs = {}

        def add_tab(tab: str) -> None:
            if tab in box.tabs:
                return
            _id, title, icon, build = next(t for t in DEVICE_TABS if t[0] == tab)
            try:
                widget = build(self.ctx)
            except Exception as exc:  # noqa: BLE001
                logger.exception("tab %s did not build", tab)
                widget = common.empty_state("dialog-warning-symbolic", "This tab could not be shown",
                                            f"{type(exc).__name__}: {exc}")
            box.tabs[tab] = widget
            holder = stack.get_child_by_name(tab)
            holder.append(widget)

        for tab, title, icon, _build in DEVICE_TABS:
            holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
            stack.add_titled_with_icon(holder, tab, title, icon)

        def visible(*_a) -> None:
            name = stack.get_visible_child_name()
            if name:
                add_tab(name)
        stack.connect("notify::visible-child-name", visible)
        switcher = Adw.ViewSwitcher(stack=stack, policy=Adw.ViewSwitcherPolicy.WIDE,
                                    halign=Gtk.Align.CENTER, margin_top=6)
        bar = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=6)
        bar.append(switcher)
        box.append(bar)
        box.append(stack)
        box.stack = stack
        box.add_tab = add_tab
        box.status = lambda: getattr(box.tabs.get(stack.get_visible_child_name() or ""), "status",
                                     lambda: common.STATUS_UNKNOWN)()
        visible()
        return box

    def show_page(self, name: str) -> Gtk.Widget:
        """Show page `name` (or a Chronoa-era alias), building it the first time."""
        page, tab = ALIASES.get(name, (name, ""))
        if page not in self.rows:
            page, tab = "phone", ""
        if page not in self.built:
            widget = self._build(page)
            self.built[page] = widget
            self.stack.add_named(widget, page)
        widget = self.built[page]
        if tab and hasattr(widget, "stack"):
            widget.stack.set_visible_child_name(tab)
            widget.add_tab(tab)
        self.stack.set_visible_child_name(page)
        self.current = page
        title = next(t for n, t, _i, _b in PAGES if n == page)
        self.title.set_title(title)
        self.content_page.set_title(title)
        row = self.rows[page]
        if self.list.get_selected_row() is not row:
            self.list.select_row(row)
        self.split.set_show_content(True)
        return widget

    def reload(self) -> None:
        """Build the current page again: every page reads once, when built."""
        name = self.current
        old = self.built.pop(name, None)
        tab = old.stack.get_visible_child_name() if old is not None and hasattr(old, "stack") else ""
        if old is not None:
            self.stack.remove(old)
        widget = self.show_page(name)
        if tab and hasattr(widget, "stack"):
            widget.stack.set_visible_child_name(tab)

    # --- actions -----------------------------------------------------------------

    def do_action(self, name: str, value: Any = None) -> None:
        if name == "connect-device":
            self.open_connect(str(value or ""))
        elif name == "show-page":
            self.show_page(str(value or ""))

    def open_connect(self, route: str = "") -> None:
        from shani_link.app.connect_device import ConnectDeviceWindow
        window = ConnectDeviceWindow(self.ctx, route, transient_for=self)
        app = self.get_application()
        if app is not None:
            window.set_application(app)
        window.present()
        self.connect_window = window

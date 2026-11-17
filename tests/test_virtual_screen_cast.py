"""Exercise the portal wire shapes without a real desktop, phone or uinput."""

import os
from types import SimpleNamespace

import pytest
from gi.repository import GLib

from shani_link import screen_stream as ss


class Portal:
    def __init__(self, mask=7, streams=None):
        self.mask = mask
        self.streams = streams if streams is not None else [(42, {"source_type": 4})]
        self.calls = []
        self.requests = []
        self.unsubscribed = []
        self.read_fd, self.write_fd = os.pipe()

    def call_sync(self, *args):
        self.calls.append(args)
        if args[3] == "Get":
            assert args[4].unpack() == (
                "org.freedesktop.portal.ScreenCast",
                "AvailableSourceTypes",
            )
            assert args[5].dup_string() == "(v)"
            return GLib.Variant("(v)", (GLib.Variant("u", self.mask),))

    def request(self, method, args, timeout=120):
        self.requests.append((method, args))
        if method == "CreateSession":
            return {"session_handle": "/test/session"}
        if method == "Start":
            return {"streams": self.streams}
        return {}

    def signal_subscribe(self, *args):
        return 99

    def signal_unsubscribe(self, subscription):
        self.unsubscribed.append(subscription)

    def call_with_unix_fd_list_sync(self, *args):
        self.calls.append(args)
        return GLib.Variant("(h)", (0,)), SimpleNamespace(
            get=lambda index: os.dup(self.read_fd)
        )

    def close(self):
        os.close(self.read_fd)
        os.close(self.write_fd)


@pytest.fixture
def portal():
    bus = Portal()
    cast = ss.ScreenCast.__new__(ss.ScreenCast)
    cast.GLib = GLib
    cast.bus = bus
    cast.session = ""
    cast.closed = False
    cast._closed_subscription = None
    cast._request = bus.request
    yield cast, bus
    cast.close()
    bus.close()


@pytest.mark.parametrize(
    "mask,expected",
    [(0, False), (1, False), (3, False), (4, True), (7, True), (12, True)],
)
def test_real_uint32_variant_is_a_bitmask(portal, mask, expected):
    cast, bus = portal
    bus.mask = mask
    assert cast.available_source_types() == mask
    assert cast.supports_virtual() is expected


def test_unavailable_property_is_not_unsupported(portal, monkeypatch):
    cast, bus = portal

    def failed(*args):
        raise GLib.Error("portal unavailable")

    monkeypatch.setattr(bus, "call_sync", failed)
    with pytest.raises(RuntimeError, match="portal unavailable"):
        cast.supports_virtual()
    assert not bus.requests


@pytest.mark.parametrize(
    "kwargs,source", [({}, 1), ({"window": True}, 2), ({"virtual": True}, 4)]
)
def test_start_sends_only_the_requested_source_and_closes(portal, kwargs, source):
    cast, bus = portal
    fd, node = cast.start(**kwargs)
    try:
        assert node == 42 and os.fstat(fd)
        options = bus.requests[1][1][-1]
        assert options["types"].unpack() == source
        assert options["multiple"].unpack() is False
        if source != 4:
            assert not any(call[3] == "Get" for call in bus.calls)
        cast.close()
        cast.close()
        assert bus.unsubscribed == [99]
        assert sum(call[3] == "Close" for call in bus.calls) == 1
    finally:
        os.close(fd)


def test_unsupported_and_conflicting_sources_do_not_create_a_session(portal):
    cast, bus = portal
    bus.mask = 3
    with pytest.raises(RuntimeError, match="does not support"):
        cast.start(virtual=True)
    with pytest.raises(ValueError, match="not both"):
        cast.start(window=True, virtual=True)
    assert not bus.requests


@pytest.mark.parametrize(
    "streams",
    [
        [],
        [(42, {})],
        [(42, {"source_type": 1})],
        [(42, {"source_type": 4}), (43, {"source_type": 4})],
    ],
)
def test_virtual_never_silently_becomes_a_monitor(portal, streams):
    cast, bus = portal
    bus.streams = streams
    with pytest.raises(RuntimeError, match="portal"):
        cast.start(virtual=True)
    assert cast.closed and cast.session == ""
    assert any(call[3] == "Close" for call in bus.calls)
    assert not any(call[3] == "OpenPipeWireRemote" for call in bus.calls)


def test_a_failed_start_releases_the_session(portal, monkeypatch):
    cast, bus = portal

    def request(method, args, timeout=120):
        if method == "Start":
            raise RuntimeError("cancelled")
        return bus.request(method, args, timeout)

    monkeypatch.setattr(cast, "_request", request)
    with pytest.raises(RuntimeError, match="cancelled"):
        cast.start(virtual=True)
    assert cast.closed and cast.session == ""


def test_missing_pipewire_fd_releases_subscription_and_session(portal, monkeypatch):
    cast, bus = portal
    monkeypatch.setattr(
        bus, "call_with_unix_fd_list_sync", lambda *a: (GLib.Variant("(h)", (0,)), None)
    )
    with pytest.raises(RuntimeError, match="no PipeWire file descriptor"):
        cast.start(virtual=True)
    assert cast.closed and cast.session == ""
    assert bus.unsubscribed == [99]


@pytest.mark.parametrize(
    "mask,exit_code,phrase", [(7, 0, "advertised"), (3, 1, "not supported")]
)
def test_cli_probe_touches_no_phone_encoder_or_input(
    portal, monkeypatch, capsys, mask, exit_code, phrase
):
    cast, bus = portal
    bus.mask = mask
    monkeypatch.setattr(ss, "ScreenCast", lambda: cast)
    monkeypatch.setattr("sys.argv", ["screen-stream", "--check-virtual"])
    monkeypatch.setattr(ss, "gstreamer_ready", lambda *a: pytest.fail("encoder probed"))
    monkeypatch.setattr(ss, "_serve", lambda *a: pytest.fail("phone contacted"))
    monkeypatch.setattr(ss, "_input_surfaces", lambda *a: pytest.fail("input opened"))
    with pytest.raises(SystemExit) as result:
        ss.main()
    assert result.value.code == exit_code
    assert phrase in capsys.readouterr().out
    assert not bus.requests


def test_cli_probe_reports_unknown_separately(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["screen-stream", "--check-virtual"])

    def unavailable():
        raise RuntimeError("no session bus")

    monkeypatch.setattr(ss, "ScreenCast", unavailable)
    with pytest.raises(SystemExit) as result:
        ss.main()
    assert result.value.code == 2
    assert "could not be determined" in capsys.readouterr().err


@pytest.mark.parametrize(
    "args",
    [
        ["--virtual", "--window"],
        ["--virtual", "--touch"],
        ["--virtual", "--record", "out"],
        ["--virtual", "--stills", "out"],
    ],
)
def test_cli_refuses_conflicting_or_unmapped_input_modes(args):
    with pytest.raises(SystemExit) as result:
        ss._arguments(args)
    assert result.value.code == 2


@pytest.mark.parametrize("transport", ["usb", "bluetooth"])
def test_cli_virtual_selection_reaches_the_stream(monkeypatch, transport):
    args = ss._arguments(["--virtual", "--transport", transport])
    calls = []
    monkeypatch.setattr(ss, "phone_serial", lambda: ("phone", ""))
    monkeypatch.setattr(ss.android, "display_size", lambda serial: (1080, 2400))
    monkeypatch.setattr(ss, "bluetooth_address", lambda: "192.0.2.1")
    from contextlib import contextmanager

    @contextmanager
    def tunnel(*args):
        yield True, ""

    monkeypatch.setattr(ss, "adb_link", tunnel)
    monkeypatch.setattr(ss, "_run", lambda *a, **kw: calls.append(kw))
    ss._serve(args, ss.create_codec("webm"), None, None, None)
    assert calls == [{"virtual": True}]


def test_request_removes_its_deadline_after_success(monkeypatch):
    cast = ss.ScreenCast.__new__(ss.ScreenCast)
    cast.GLib = GLib
    cast.sender = "test"
    sources = []
    callback = None

    def subscribe(*args):
        nonlocal callback
        callback = args[-1]
        return 1

    def call(*args):
        GLib.idle_add(
            lambda: (
                callback(
                    None, None, None, None, None, GLib.Variant("(ua{sv})", (0, {}))
                ),
                False,
            )[1]
        )

    cast.bus = SimpleNamespace(
        signal_subscribe=subscribe, call_sync=call, signal_unsubscribe=lambda sub: None
    )
    original = GLib.timeout_add_seconds

    def timer(*args):
        result = original(*args)
        sources.append(result)
        return result

    monkeypatch.setattr(GLib, "timeout_add_seconds", timer)
    assert cast._request("CreateSession", [{}]) == {}
    assert sources and GLib.MainContext.default().find_source_by_id(sources[0]) is None


def test_timed_out_request_is_closed(monkeypatch):
    cast = ss.ScreenCast.__new__(ss.ScreenCast)
    cast.GLib = GLib
    cast.sender = "test"
    calls = []
    cast.bus = SimpleNamespace(
        signal_subscribe=lambda *a: 1,
        signal_unsubscribe=lambda *a: None,
        call_sync=lambda *a: calls.append(a),
    )
    monkeypatch.setattr(
        GLib, "timeout_add_seconds", lambda delay, callback: GLib.idle_add(callback)
    )
    with pytest.raises(RuntimeError, match="no answer"):
        cast._request("CreateSession", [{}])
    assert calls[-1][2:4] == ("org.freedesktop.portal.Request", "Close")
    assert calls[-1][1].startswith("/org/freedesktop/portal/desktop/request/test/")


def test_virtual_stream_releases_fd_and_session(portal, monkeypatch):
    cast, bus = portal
    held = []

    class Server:
        def __init__(self, stream, *args, **kwargs):
            held.append(stream.fd)
            assert stream.node == 42

        def serve(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(ss, "ScreenCast", lambda: cast)
    monkeypatch.setattr(ss, "StreamServer", Server)
    ss._run(
        "127.0.0.1",
        1280,
        720,
        24,
        4000000,
        ss.create_codec("webm"),
        seconds=0.01,
        virtual=True,
    )
    assert cast.closed and len(held) == 1
    with pytest.raises(OSError):
        os.fstat(held[0])
    assert bus.requests[1][1][-1]["types"].unpack() == 4


def test_environment_cannot_enable_unmapped_virtual_touch(monkeypatch):
    monkeypatch.setattr("sys.argv", ["screen-stream", "--virtual"])
    monkeypatch.setenv("SHANI_LINK_SCREEN_TOUCH", "1")
    monkeypatch.setattr(ss, "gstreamer_ready", lambda *a: (True, ""))
    monkeypatch.setattr(ss, "_input_surfaces", lambda *a: pytest.fail("input opened"))
    with pytest.raises(SystemExit, match="mapping is not verified"):
        ss.main()


def test_capability_read_crosses_a_private_bus():
    """Real D-Bus (v) reply, not a fake Python list shaped like the old bug."""
    import shutil
    import subprocess
    import threading
    from gi.repository import Gio

    if not shutil.which("dbus-daemon"):
        pytest.skip("dbus-daemon is not installed")
    daemon = subprocess.Popen(
        ["dbus-daemon", "--session", "--nofork", "--print-address=1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    service = client = None
    thread = None
    loop = None
    try:
        address = daemon.stdout.readline().strip()
        assert address.startswith("unix:")
        flags = (
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
        )
        service = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
        context = GLib.MainContext.new()
        context.push_thread_default()
        try:
            info = Gio.DBusNodeInfo.new_for_xml(
                '<node><interface name="org.freedesktop.portal.ScreenCast">'
                '<property name="AvailableSourceTypes" type="u" access="read"/>'
                "</interface></node>"
            )
            service.register_object(
                "/org/freedesktop/portal/desktop",
                info.interfaces[0],
                None,
                lambda *a: GLib.Variant("u", 7),
                None,
            )
        finally:
            context.pop_thread_default()
        loop = GLib.MainLoop.new(context, False)
        thread = threading.Thread(target=loop.run, daemon=True)
        thread.start()
        client = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
        cast = ss.ScreenCast.__new__(ss.ScreenCast)
        cast.GLib = GLib
        cast.bus = client
        cast.PORTAL = (service.get_unique_name(), "/org/freedesktop/portal/desktop")
        assert cast.available_source_types() == 7
        assert cast.supports_virtual() is True
    finally:
        if loop is not None:
            loop.quit()
        if thread is not None:
            thread.join(timeout=5)
        for connection in (client, service):
            if connection is not None:
                connection.close_sync(None)
        daemon.terminate()
        daemon.communicate(timeout=5)

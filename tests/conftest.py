"""The app's tests draw on a private Broadway display, never the person's desktop.

Set up here, at import - before any test module imports Gtk, because PyGObject
opens the display when Gtk is first imported. If `gtk4-broadwayd` is missing the
real display variables are removed anyway, so a GTK test can only skip (see
`_gui.needs_display`), never put a window on somebody's screen.
"""
import atexit
import os
import pathlib
import shutil
import socket
import subprocess
import time

_DAEMON = None


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("::", port))
        except OSError:
            return False
    return True


def _free_display(base: pathlib.Path, start: int = 5100, stop: int = 6000):
    """A display number whose socket is absent *and* whose port (8080+N) binds:
    a stale socket file is not a free display, and neither is a held port."""
    return next((n for n in range(start, stop)
                 if not (base / f"broadway{n + 1}.socket").exists() and _port_free(8080 + n)), None)


def _start_broadway() -> bool:
    global _DAEMON
    for key in ("DISPLAY", "WAYLAND_DISPLAY"):
        os.environ.pop(key, None)
    daemon = shutil.which("gtk4-broadwayd")
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not daemon or not runtime:
        os.environ.pop("BROADWAY_DISPLAY", None)
        return False
    base = pathlib.Path(runtime)
    number = _free_display(base)
    if number is None:
        return False
    _DAEMON = subprocess.Popen([daemon, f":{number}"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    deadline = time.time() + 10
    while time.time() < deadline and not (base / f"broadway{number + 1}.socket").exists():
        time.sleep(0.05)
    if not (base / f"broadway{number + 1}.socket").exists():
        _DAEMON.terminate()
        return False
    os.environ["GDK_BACKEND"] = "broadway"
    os.environ["BROADWAY_DISPLAY"] = f":{number}"
    atexit.register(_stop_broadway)
    return True


def _stop_broadway() -> None:
    if _DAEMON is not None and _DAEMON.poll() is None:
        _DAEMON.terminate()
        try:
            _DAEMON.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _DAEMON.kill()


HAS_DISPLAY = _start_broadway()

# Never the person's own buses: a page under test that asks the landline
# service (dev.shani.Link) would otherwise D-Bus-activate the real one, and a
# Bluetooth read would reach the real adapter. A test that wants a bus starts a
# private one and names it (test_service.py, fake_bluez.py).
_NOWHERE = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "shani-link-tests-no-bus")
os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={_NOWHERE}"
os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = f"unix:path={_NOWHERE}"


def pytest_sessionfinish(session, exitstatus):
    _stop_broadway()


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _forget_the_phone_book():
    """The phone's contacts a test's Phone page read stay in that test: a
    later test's call history must not be named from them."""
    yield
    from shani_link import contacts
    contacts.remember_phone_book([])

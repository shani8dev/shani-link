"""Files the paired phone sends over Wi-Fi (KDE Connect's Share), while the
person has "Receive files from the phone over Wi-Fi" on (settings.KEYS:
`phone-lan-receive-enabled`, off by default).

While on: `lan_link.Receiver` listens on the control port for the paired phone
only, and firewalld's own `kdeconnect` service (1714-1764) is opened for this
session only - `firewall-cmd --add-service=kdeconnect` without `--permanent`,
which polkit asks the person to allow - so a restart always closes it again.
While off (and when the service stops) both are undone. The switch is re-read
every few seconds, as `obex_receive.py` does with its own.

A file goes to ~/Downloads under the Bluetooth receiver's naming rules, and a
notification says it came. Paired is trusted, as KDE Connect does.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading

from shani_link import lan_link, paths
from shani_link.notify import send_argv

logger = logging.getLogger(__name__)
KEY = "phone-lan-receive-enabled"
SWITCH_POLL_SECONDS = 5
FIREWALL = os.environ.get("SHANI_FIREWALL_CMD", "firewall-cmd")


def downloads() -> str:
    return os.environ.get("XDG_DOWNLOAD_DIR") or os.path.join(os.path.expanduser("~"), "Downloads")


def firewall(open_it: bool) -> "tuple[bool, str]":
    """KDE Connect's ports, for this session only. (True, "") when firewalld is
    not running at all - there is nothing to open then."""
    try:
        if subprocess.run([FIREWALL, "--state"], capture_output=True, timeout=10).returncode != 0:
            return True, ""
        r = subprocess.run([FIREWALL, ("--add-service" if open_it else "--remove-service") + "=kdeconnect"],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return r.returncode == 0, r.stderr.strip()


class LanReceiver:
    def __init__(self, settings, folder: "str | None" = None, port: int = lan_link.UDP_PORT) -> None:
        self.settings = settings
        self.folder = folder or downloads()
        self.port = port
        self._rx: "lan_link.Receiver | None" = None
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._watch, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._off()

    def _watch(self) -> None:
        while not self._stop.is_set():
            want = bool(self.settings.get_bool(KEY, False)) and bool(lan_link.paired_device().get("deviceId"))
            if want and self._rx is None:
                self._on()
            elif not want and self._rx is not None:
                self._off()
            self._stop.wait(SWITCH_POLL_SECONDS)

    def _on(self) -> None:
        opened, why = firewall(True)
        if not opened:
            logger.info("the firewall was not opened for KDE Connect: %s", why)
        rx = lan_link.Receiver(self.folder, self._arrived, port=self.port)
        started, why = rx.start()
        if not started:
            logger.info("could not listen for the phone: %s", why)
            firewall(False)
            return
        self._rx = rx
        lan_link.announce(lan_link._hostname(), self.port)

    def _off(self) -> None:
        if self._rx is not None:
            self._rx.stop()
            self._rx = None
            firewall(False)

    def _arrived(self, path: str, why: str) -> None:
        name = os.path.basename(path)
        argv = (send_argv("File received from your phone", f"{name} is in Downloads", "folder-download") if path
                else send_argv("A file from your phone did not arrive", why, "dialog-warning"))
        try:
            subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass

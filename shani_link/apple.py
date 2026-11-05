"""iPhones, honestly: what works with one on Linux, and how one is recognised.

What works: its files over a USB cable (usbmuxd and GVfs show it in Files once
the iPhone is unlocked and "Trust" is tapped), and Bluetooth audio. What does
not: calls and texts through it - Apple keeps those to its own devices. So
wherever an Apple device appears, the app says this once and offers no button
that cannot work for it.

An Apple device is told by its Bluetooth vendor (BlueZ's `Modalias`
`bluetooth:v004C...` or manufacturer data under company id 0x004C), by its
name ("Asha's iPhone"), or - on USB - by vendor 05ac with an iPhone or iPad
product, read from sysfs.
"""
from __future__ import annotations

import os
import re
import shutil
from typing import Iterable

#: Apple's Bluetooth SIG company id, and its USB vendor id.
BT_COMPANY = 0x004C
USB_VENDOR = "05ac"

HEADLINE = "What works with an iPhone"
WORKS = ("Its files, over a USB cable: unlock it, tap Trust, and it shows in Files.",
         "Bluetooth audio: play its sound through this computer.")
NOT_POSSIBLE = "Calls and texts through an iPhone aren't possible on Linux."
#: All of it in one paragraph, for a row's subtitle or a page's body.
SUMMARY = " ".join(WORKS) + " " + NOT_POSSIBLE

_NAME = re.compile(r"\b(iphone|ipad|ipod)\b", re.I)


def is_apple(name: str = "", modalias: str = "", manufacturer_ids: Iterable[int] = ()) -> bool:
    """Is this Bluetooth device an Apple one, by vendor or by name?"""
    if re.match(r"(bluetooth|usb):v(004C|05AC)", modalias or "", re.I):
        return True
    if BT_COMPANY in set(int(i) for i in manufacturer_ids or ()):
        return True
    return bool(_NAME.search(name or ""))


def usb_iphones(sysfs: str = "/sys/bus/usb/devices") -> "list[str]":
    """The product names of iPhones, iPads and iPods on a USB cable."""
    out = []
    try:
        entries = sorted(os.listdir(sysfs))
    except OSError:
        return out
    for entry in entries:
        base = os.path.join(sysfs, entry)
        try:
            with open(os.path.join(base, "idVendor"), encoding="utf-8") as fh:
                vendor = fh.read().strip().lower()
            if vendor != USB_VENDOR:
                continue
            with open(os.path.join(base, "product"), encoding="utf-8") as fh:
                product = fh.read().strip()
        except OSError:
            continue
        if _NAME.search(product):
            out.append(product)
    return out


def usbmuxd_present() -> bool:
    """Can this computer read an iPhone's files at all (usbmuxd installed)?"""
    return shutil.which("usbmuxd") is not None or os.path.exists("/run/usbmuxd") \
        or os.path.exists("/var/run/usbmuxd")


def summary() -> str:
    """`SUMMARY`, plus what to install when usbmuxd is missing."""
    if usbmuxd_present():
        return SUMMARY
    return SUMMARY + " (Files needs usbmuxd, which is not installed.)"

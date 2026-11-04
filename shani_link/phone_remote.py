"""Press keys on the paired phone: camera shutter, media, volume, typing.

This computer is "Chronoa Remote" (`ble_peripheral.NAME`), a Bluetooth keyboard
the phone paired with (HID over GATT - no app on the phone). The peripheral runs
in the service while the "Phone as a remote" switch (`phone-remote-enabled`) is
on; this is its client, through the socket `ble_peripheral.serve` listens on.

Moved from Shani Chronoa's `phone_remote` skill, which keeps only its consent and
calls `_run` here. The app's Phone page calls `press` directly.
"""
from __future__ import annotations

from shani_link import ble_peripheral as bp

KEYS = ("shutter", "play_pause", "next", "previous", "stop", "volume_up", "volume_down", "mute")

NOT_RUNNING = ("The phone remote is not running here - it starts with the Shani Link "
               "service while “Phone as a remote” is on (Background).")


def status() -> "tuple[bool, str]":
    """(a phone is connected and listening, the sentence saying so)."""
    try:
        answer = bp.ask("status")
    except OSError:
        return False, NOT_RUNNING
    if answer.get("error"):
        return False, f"The phone remote could not start: {answer['error']}."
    if answer.get("subscribed"):
        return True, f"The phone is connected to {bp.NAME} and listening for keys."
    return False, (f"{bp.NAME} is advertising, but no phone is connected to it. Pair "
                   f"'{bp.NAME}' from the phone's Bluetooth settings.")


def _ready() -> str:
    """"" when a phone is listening, else why nothing can be pressed."""
    connected, why = status()
    if connected:
        return ""
    if why.startswith(bp.NAME):
        return f"No phone is connected to {bp.NAME}, so nothing was pressed."
    return why


def press(key: str) -> str:
    """Press one of `KEYS` on the phone; the sentence says what happened."""
    key = str(key or "").strip().lower().replace(" ", "_")
    if key not in KEYS:
        return f"key must be one of {', '.join(KEYS)}, not {key!r}."
    problem = _ready()
    if problem:
        return problem
    try:
        answer = bp.ask("press", key=key)
    except OSError:
        return NOT_RUNNING
    if answer.get("error"):
        return f"Not pressed: {answer['error']}."
    hint = " (in the phone's camera app this takes the photo)" if key == "shutter" else ""
    return f"Pressed {key.replace('_', ' ')} on the phone{hint}."


def type_text(text: str) -> str:
    """Type ASCII text into whatever field is focused on the phone."""
    text = str(text or "")
    if not text:
        return "type needs text. Nothing was typed."
    problem = _ready()
    if problem:
        return problem.replace("pressed", "typed")
    try:
        answer = bp.ask("type", text=text)
    except OSError:
        return NOT_RUNNING
    if answer.get("error"):
        return f"Not typed: {answer['error']}."
    missing = answer.get("missing") or ""
    return (f"Typed {len(text) - len(missing)} characters into the phone's focused field."
            + (f" Could not type {missing!r}: a US keyboard has no keys for them." if missing else ""))


def _run(arguments: dict) -> str:
    """The `phone_remote` tool's arguments (no consent here: Chronoa's skill asks first)."""
    if not isinstance(arguments, dict):
        return "phone_remote needs an action, e.g. {\"action\": \"press\", \"key\": \"shutter\"}."
    action = str(arguments.get("action") or "status").strip().lower()
    if action == "status":
        return status()[1]
    if action == "press":
        return press(arguments.get("key") or "")
    if action == "type":
        return type_text(arguments.get("text") or "")
    return f"action must be status, press or type, not {action!r}."

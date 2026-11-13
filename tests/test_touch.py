"""The virtual touchscreen (`shani_link/touch.py`), against a file instead of the node.

The kernel side is ioctls and `struct input_event`s; a regular file takes the
writes and a recording `ioctl` takes the setup, so the exact bytes a phone's
finger produces can be asserted without root or a real device.
"""
import os
import struct

import pytest

from shani_link import touch


@pytest.fixture
def node(tmp_path, monkeypatch):
    """A fake `/dev/uinput`: a file, and ioctls that only record."""
    path = tmp_path / "uinput"
    path.write_bytes(b"")
    calls = []

    def fake_ioctl(fd, request, *args):
        calls.append((request, args[0] if args else None))
        return 0

    monkeypatch.setattr(touch.fcntl, "ioctl", fake_ioctl)
    screen = touch.TouchScreen(path=str(path))
    screen.open()
    return screen, path, calls


def _events(path):
    with open(path, "rb") as handle:
        data = handle.read()
    return list(touch.EVENT.iter_unpack(data))


def _values(events, ev_type, code):
    return [value for _s, _u, t, c, value in events if t == ev_type and c == code]


def test_the_device_is_a_direct_multitouch_panel(node):
    _screen, _path, calls = node
    requests = [request for request, _arg in calls]
    assert touch.UI_SET_EVBIT in requests and touch.UI_SET_KEYBIT in requests
    assert touch.UI_SET_PROPBIT in requests and touch.UI_SET_ABSBIT in requests
    assert touch.UI_DEV_SETUP in requests and touch.UI_ABS_SETUP in requests
    assert touch.UI_DEV_CREATE in requests
    # the property and the key are what make it a touchscreen and not a touchpad
    assert any(request == touch.UI_SET_PROPBIT and arg == touch.INPUT_PROP_DIRECT
               for request, arg in calls)
    assert any(request == touch.UI_SET_KEYBIT and arg == touch.BTN_TOUCH
               for request, arg in calls)


def test_a_finger_landing_is_a_slot_a_tracking_id_and_a_button(node):
    screen, path, _calls = node
    screen.down(0, 100, 200)
    events = _events(path)
    assert _values(events, touch.EV_ABS, touch.ABS_MT_SLOT) == [0]
    assert _values(events, touch.EV_ABS, touch.ABS_MT_TRACKING_ID) == [1]
    assert _values(events, touch.EV_ABS, touch.ABS_MT_POSITION_X) == [100]
    assert _values(events, touch.EV_ABS, touch.ABS_MT_POSITION_Y) == [200]
    assert _values(events, touch.EV_KEY, touch.BTN_TOUCH) == [1]
    assert events[-1][2:4] == (touch.EV_SYN, touch.SYN_REPORT)


def test_two_fingers_get_two_slots_and_two_ids(node):
    screen, path, _calls = node
    screen.down(0, 10, 10)
    screen.down(1, 20, 20)
    events = _events(path)
    assert _values(events, touch.EV_ABS, touch.ABS_MT_SLOT) == [0, 1]
    assert _values(events, touch.EV_ABS, touch.ABS_MT_TRACKING_ID) == [1, 2]


def test_a_move_keeps_the_slot_and_sends_no_new_id(node):
    screen, path, _calls = node
    screen.down(0, 10, 10)
    screen.move(0, 30, 40)
    events = _events(path)
    assert _values(events, touch.EV_ABS, touch.ABS_MT_POSITION_X) == [10, 30]
    assert _values(events, touch.EV_ABS, touch.ABS_MT_TRACKING_ID) == [1]


def test_the_button_lifts_with_the_last_finger_not_the_first(node):
    screen, path, _calls = node
    screen.down(0, 1, 1)
    screen.down(1, 2, 2)
    screen.up(0)
    assert _values(_events(path), touch.EV_KEY, touch.BTN_TOUCH) == [1, 1]
    screen.up(1)
    assert _values(_events(path), touch.EV_KEY, touch.BTN_TOUCH) == [1, 1, 0]


def test_a_lift_releases_the_tracking_id(node):
    screen, path, _calls = node
    screen.down(0, 1, 1)
    screen.up(0)
    ids = _values(_events(path), touch.EV_ABS, touch.ABS_MT_TRACKING_ID)
    assert ids == [1, -1]


# --- the layer the phone talks to ---------------------------------------------

def test_normalised_points_become_device_coordinates(node):
    screen, _path, _calls = node
    surface = touch.TouchSurface(screen)
    surface.update([{"id": 7, "x": 0.5, "y": 0.25, "pressure": 0.5}])
    events = _events(_path)
    assert _values(events, touch.EV_ABS, touch.ABS_MT_POSITION_X) == [touch.MAXIMUM // 2]
    assert _values(events, touch.EV_ABS, touch.ABS_MT_POSITION_Y) == [touch.MAXIMUM // 4]
    assert _values(events, touch.EV_ABS, touch.ABS_MT_PRESSURE) == [127]


def test_a_point_outside_the_picture_is_clamped_not_refused(node):
    screen, _path, _calls = node
    surface = touch.TouchSurface(screen)
    surface.update([{"id": 1, "x": 2.0, "y": -1.0}])
    events = _events(_path)
    assert _values(events, touch.EV_ABS, touch.ABS_MT_POSITION_X) == [touch.MAXIMUM - 1]
    assert _values(events, touch.EV_ABS, touch.ABS_MT_POSITION_Y) == [0]


def test_a_finger_that_lifts_frees_its_slot(node):
    screen, _path, _calls = node
    surface = touch.TouchSurface(screen)
    surface.update([{"id": 1, "x": 0.1, "y": 0.1}])
    surface.update([{"id": 1, "x": 0.1, "y": 0.1}, {"id": 2, "x": 0.2, "y": 0.2}])
    surface.update([{"id": 2, "x": 0.2, "y": 0.2}])          # finger 1 is gone
    assert surface._slots == {2: 1} and 0 in surface._free
    surface.update([{"id": 2, "x": 0.2, "y": 0.2}, {"id": 3, "x": 0.3, "y": 0.3}])
    assert surface._slots == {2: 1, 3: 0}, surface._slots


def test_more_than_ten_fingers_are_ignored_not_crashed(node):
    screen, _path, _calls = node
    surface = touch.TouchSurface(screen)
    surface.update([{"id": index, "x": 0.1, "y": 0.1} for index in range(14)])
    assert len(screen.active) == touch.SLOTS


def test_closing_lifts_every_finger_and_destroys_the_device(node):
    screen, path, _calls = node
    surface = touch.TouchSurface(screen)
    surface.update([{"id": 1, "x": 0.1, "y": 0.1}])
    surface.close()
    assert _values(_events(path), touch.EV_KEY, touch.BTN_TOUCH) == [1, 0]
    assert screen.active == set()


# --- the gate -----------------------------------------------------------------

def test_a_machine_without_the_node_says_which_piece_is_missing(monkeypatch):
    monkeypatch.setattr(touch.os.path, "exists", lambda _p: False)
    ok, why = touch.available()
    assert not ok and "uinput" in why and "modprobe" in why


def test_a_user_outside_the_input_group_is_told_how_to_join_it(monkeypatch):
    monkeypatch.setattr(touch.os.path, "exists", lambda _p: True)
    monkeypatch.setattr(touch.os, "access", lambda _p, _m: False)
    ok, why = touch.available()
    assert not ok and "input group" in why, why


def test_a_real_node_is_opened_and_shut_without_a_traceback(monkeypatch, tmp_path):
    monkeypatch.setattr(touch.os.path, "exists", lambda _p: True)
    monkeypatch.setattr(touch.os, "access", lambda _p, _m: True)
    monkeypatch.setattr(touch, "DEVICE", str(tmp_path / "uinput"))
    calls = []
    monkeypatch.setattr(touch.fcntl, "ioctl", lambda fd, req, *a: calls.append(req) or 0)
    screen = touch.TouchScreen(path=str(tmp_path / "uinput"))
    (tmp_path / "uinput").write_bytes(b"")
    screen.open()
    assert touch.UI_DEV_CREATE in calls
    screen.close()
    assert touch.UI_DEV_DESTROY in calls

# --- the stylus ----------------------------------------------------------------

def _fake_node(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(touch.fcntl, "ioctl",
                        lambda fd, request, *args: calls.append((request, args[0] if args else None)) or 0)
    path = tmp_path / "uinput"
    path.write_bytes(b"")
    return path, calls


def test_the_pen_is_its_own_device_with_pressure_and_tilt(tmp_path, monkeypatch):
    path, calls = _fake_node(tmp_path, monkeypatch)
    pen = touch.Pen(path=str(path))
    pen.open()
    requests = [request for request, _arg in calls]
    assert touch.UI_SET_KEYBIT in requests and touch.UI_DEV_CREATE in requests
    assert any(request == touch.UI_SET_KEYBIT and arg == touch.BTN_TOOL_PEN for request, arg in calls)
    pen.down(100, 200, pressure=200, tilt_x=300, tilt_y=-300)
    events = _events(path)
    assert _values(events, touch.EV_KEY, touch.BTN_TOOL_PEN) == [1]
    assert _values(events, touch.EV_KEY, touch.BTN_TOUCH) == [1]
    assert _values(events, touch.EV_ABS, 0x00) == [100]        # ABS_X
    assert _values(events, touch.EV_ABS, 0x01) == [200]        # ABS_Y
    assert _values(events, touch.EV_ABS, 0x18) == [200]        # ABS_PRESSURE
    assert _values(events, touch.EV_ABS, 0x1A) == [300]        # ABS_TILT_X
    assert _values(events, touch.EV_ABS, 0x1B) == [-300]       # ABS_TILT_Y


def test_the_eraser_end_is_a_different_tool(tmp_path, monkeypatch):
    path, _calls = _fake_node(tmp_path, monkeypatch)
    pen = touch.Pen(path=str(path))
    pen.open()
    pen.down(1, 1, eraser=True)
    events = _events(path)
    assert _values(events, touch.EV_KEY, 0x141) == [1]         # BTN_TOOL_RUBBER
    assert _values(events, touch.EV_KEY, 0x140) == []          # not BTN_TOOL_PEN


def test_lifting_ends_contact_then_proximity(tmp_path, monkeypatch):
    path, _calls = _fake_node(tmp_path, monkeypatch)
    pen = touch.Pen(path=str(path))
    pen.open()
    pen.down(1, 1)
    pen.up()
    events = _events(path)
    assert _values(events, touch.EV_KEY, touch.BTN_TOUCH) == [1, 0]
    assert _values(events, touch.EV_KEY, 0x140) == [1, 0]


# --- the keyboard ---------------------------------------------------------------

def test_typing_is_keycodes_and_shift(tmp_path, monkeypatch):
    path, _calls = _fake_node(tmp_path, monkeypatch)
    board = touch.Keyboard(path=str(path))
    board.open()
    board.type("aA1!")
    events = _events(path)
    # a, shift+a, 1, shift+1: down/up pairs, shift held around the shifted key
    assert _values(events, touch.EV_KEY, 30) == [1, 0, 1, 0]   # a, then shift+a
    assert _values(events, touch.EV_KEY, 42) == [1, 0, 1, 0]
    assert _values(events, touch.EV_KEY, 2) == [1, 0, 1, 0]


def test_enter_newline_and_backspace(tmp_path, monkeypatch):
    path, _calls = _fake_node(tmp_path, monkeypatch)
    board = touch.Keyboard(path=str(path))
    board.open()
    board.type("\n")
    assert _values(_events(path), touch.EV_KEY, touch.KEY_ENTER) == [1, 0]
    board.backspace(2)
    assert _values(_events(path), touch.EV_KEY, touch.KEY_BACKSPACE) == [1, 0, 1, 0]


def test_a_character_with_no_key_is_counted_not_guessed(tmp_path, monkeypatch):
    path, _calls = _fake_node(tmp_path, monkeypatch)
    board = touch.Keyboard(path=str(path))
    board.open()
    assert board.type("aé") == 1
    assert _values(_events(path), touch.EV_KEY, 30) == [1, 0]


def test_the_pen_layer_maps_points_and_lifts(tmp_path, monkeypatch):
    path, _calls = _fake_node(tmp_path, monkeypatch)
    device = touch.Pen(path=str(path))
    device.open()
    surface = touch.PenSurface(device)
    surface.update({"x": 0.5, "y": 0.5, "pressure": 0.5, "tilt_x": 10, "tilt_y": -10})
    events = _events(path)
    assert _values(events, touch.EV_ABS, 0x00) == [touch.MAXIMUM // 2]
    assert _values(events, touch.EV_ABS, 0x18) == [127]        # pressure
    assert _values(events, touch.EV_ABS, 0x1A) == [1000]       # tilt, hundredths
    surface.update({"x": 0.6, "y": 0.6, "lift": True})
    events = _events(path)
    assert _values(events, touch.EV_KEY, touch.BTN_TOUCH) == [1, 0]
    assert _values(events, touch.EV_KEY, 0x140) == [1, 0]

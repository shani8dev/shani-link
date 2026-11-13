"""The touch end of the screen stream: what the phone posts, and what the page asks.

The server is the real one on a loopback port; the encoder is not opened, because
a POST never touches it - only `/stream` does.
"""
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from shani_link import screen_stream as ss


class Recorder:
    """A stand-in for the uinput surface: it keeps what it was given."""

    def __init__(self):
        self.frames = []

    def update(self, points):
        self.frames.append(points)


def _serve(touch_surface, port=0, pen_surface=None, keyboard=None):
    stream = ss.Stream(0, 0)
    server = ss.StreamServer(stream, "127.0.0.1", port=port, touch_surface=touch_surface,
                             pen_surface=pen_surface, keyboard=keyboard)
    threading.Thread(target=server.serve, daemon=True, name="screen-stream-test").start()
    deadline = time.monotonic() + 5
    while server.httpd is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.httpd is not None, "the server never listened"
    return server, server.httpd.server_address[1]


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, response.read()


def _post(url, body, content_type="application/json"):
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


@pytest.fixture
def server():
    recorder = Recorder()
    streamer, port = _serve(recorder)
    try:
        yield recorder, f"http://127.0.0.1:{port}"
    finally:
        streamer.stop()


def test_the_page_is_told_whether_touch_is_on(server):
    recorder, base = server
    status, body = _get(base + "/info")
    assert status == 200 and json.loads(body) == {"touch": True, "pen": False, "keyboard": False}


def test_the_page_asks_before_it_listens():
    streamer, port = _serve(None)
    base = f"http://127.0.0.1:{port}"
    try:
        status, body = _get(base + "/info")
        assert status == 200 and json.loads(body) == {"touch": False, "pen": False, "keyboard": False}
        assert _post(base + "/touch", b'{"points": []}') == 404
    finally:
        streamer.stop()


def test_a_frame_of_touches_reaches_the_surface(server):
    recorder, base = server
    points = [{"id": 1, "x": 0.25, "y": 0.5, "pressure": 0.8}]
    status = _post(base + "/touch", json.dumps({"points": points}).encode())
    assert status == 204
    assert recorder.frames == [points]


def test_a_lift_is_an_empty_frame_not_a_disconnect(server):
    recorder, base = server
    _post(base + "/touch", json.dumps({"points": [{"id": 1, "x": 0.1, "y": 0.1}]}).encode())
    _post(base + "/touch", json.dumps({"points": []}).encode())
    assert recorder.frames[-1] == []


def test_a_body_that_is_not_json_is_a_sentence_and_not_a_traceback(server):
    _recorder, base = server
    assert _post(base + "/touch", b"not json") == 400
    assert _post(base + "/touch", b'{"points": [{"id": "x"}]}') == 204   # the surface drops it


def test_the_page_carries_the_touch_layer(server):
    _recorder, base = server
    _status, body = _get(base + "/")
    text = body.decode()
    assert 'id="touch"' in text and '"/touch"' in text and '"/info"' in text
    assert "touch-action:none" in text

class PenRecorder:
    def __init__(self):
        self.frames = []

    def update(self, point):
        self.frames.append(point)


class KeyRecorder:
    def __init__(self):
        self.text = []
        self.backspaces = []

    def type(self, text):
        self.text.append(text)

    def backspace(self, count):
        self.backspaces.append(count)


def test_the_pen_endpoint_reaches_the_pen_surface():
    recorder = PenRecorder()
    streamer, port = _serve(Recorder(), pen_surface=recorder)
    base = f"http://127.0.0.1:{port}"
    try:
        assert _post(base + "/pen", json.dumps({"x": 0.5, "y": 0.5}).encode()) == 204
        assert recorder.frames == [{"x": 0.5, "y": 0.5}]
        assert _post(base + "/pen", json.dumps({"lift": True}).encode()) == 204
        assert recorder.frames[-1] == {"lift": True}
    finally:
        streamer.stop()


def test_the_typing_endpoint_reaches_the_keyboard():
    board = KeyRecorder()
    streamer, port = _serve(Recorder(), keyboard=board)
    base = f"http://127.0.0.1:{port}"
    try:
        assert _post(base + "/key", json.dumps({"text": "hello"}).encode()) == 204
        assert _post(base + "/key", json.dumps({"backspace": 2}).encode()) == 204
        assert board.text == ["hello"] and board.backspaces == [2]
    finally:
        streamer.stop()


def test_the_page_is_told_which_devices_are_on():
    recorder = Recorder()
    streamer, port = _serve(recorder, pen_surface=PenRecorder(), keyboard=KeyRecorder())
    base = f"http://127.0.0.1:{port}"
    try:
        _status, body = _get(base + "/info")
        assert json.loads(body) == {"touch": True, "pen": True, "keyboard": True}
    finally:
        streamer.stop()

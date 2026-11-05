"""The one call history every line writes to, and tel: links. No GTK, no bus."""
import json
import os
import time
import threading

import pytest

from shani_link import calls_log, tel_uri


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path / "data" / "shani-link"


def test_rows_are_newest_first_and_a_missed_call_is_an_unanswered_incoming_one():
    calls_log.add("landline", "111", "out", False, when=100)
    calls_log.add("phone", "222", "in", False, when=300)
    calls_log.add("modem", "333", "in", True, seconds=65, when=200)
    got = calls_log.rows()
    assert [r["number"] for r in got] == ["222", "333", "111"]
    assert [r["missed"] for r in got] == [True, False, False]
    assert got[1]["seconds"] == 65 and got[0]["seconds"] == 0
    assert [r["number"] for r in calls_log.rows(line="modem")] == ["333"]
    assert oct(os.stat(calls_log.path()).st_mode & 0o777) == "0o600"


def test_delete_one_and_clear_all():
    a = calls_log.add("landline", "111", "out", True)
    calls_log.add("landline", "222", "out", True)
    assert calls_log.delete(a["id"]) and not calls_log.delete(a["id"])
    assert [r["number"] for r in calls_log.rows()] == ["222"]
    calls_log.clear()
    assert calls_log.rows() == []


def test_two_writers_at_once_lose_nothing():
    threads = [threading.Thread(target=lambda i=i: calls_log.add("phone", str(i), "out", True))
               for i in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls_log.rows()) == 40


def test_a_missed_call_tells_its_listeners_once():
    heard = []
    undo = calls_log.on_missed(heard.append)
    try:
        calls_log.add("landline", "111", "in", True)
        calls_log.add("landline", "222", "in", False)
        calls_log.add("landline", "333", "out", False)
    finally:
        undo()
    assert [r["number"] for r in heard] == ["222"]


def test_the_landlines_old_list_is_carried_over_once(data):
    data.mkdir(parents=True)
    (data / "landline-calls.json").write_text(json.dumps([
        {"number": "+911", "name": "A", "kind": "missed", "when": 50, "seconds": 0},
        {"number": "+912", "name": "B", "kind": "placed", "when": 40, "seconds": 9}]))
    got = calls_log.rows()
    assert [(r["number"], r["direction"], r["missed"]) for r in got] == [("+911", "in", True),
                                                                      ("+912", "out", False)]
    calls_log.add("landline", "+913", "out", True, when=60)
    assert len(calls_log.rows()) == 3, "the old rows were carried over twice or lost"


def test_the_landline_facade_logs_into_the_shared_list_named_from_contacts():
    from shani_link import contacts, landline
    contacts.book().save(contacts.Contact(full_name="Amma", phones=[("cell", "09800000001")]))
    landline._log_history("incoming", {"number": "+919800000001"})
    landline._log_history("ended", {"number": "+919800000001", "seconds": 0})
    landline._log_history("ringing", {"number": "+912"})
    landline._log_history("answered", {})
    landline._log_history("ended", {"number": "+912", "seconds": 30})
    missed = next(r for r in calls_log.rows() if r["number"] == "+919800000001")
    placed = next(r for r in calls_log.rows() if r["number"] == "+912")
    assert missed["name"] == "Amma" and missed["missed"] and missed["line"] == "landline"
    assert placed["direction"] == "out" and placed["seconds"] == 30 and not placed["missed"]
    old = landline.history()
    assert {r["kind"] for r in old} == {"missed", "placed"} and old[0]["seconds"] in (0, 30)


def test_days_read_as_people_say_them():
    now = time.mktime((2026, 11, 5, 12, 0, 0, 0, 0, -1))
    at = lambda d, h=9: time.mktime((2026, 11, d, h, 0, 0, 0, 0, -1))  # noqa: E731
    assert calls_log.day_label(at(5), now) == "Today"
    assert calls_log.day_label(at(4, 23), now) == "Yesterday"
    assert calls_log.day_label(at(2), now) == "Monday"
    assert calls_log.day_label(at(1, 0) - 86400 * 20, now) == "12 October"
    rows = [{"when": at(5)}, {"when": at(5, 8)}, {"when": at(4)}]
    assert list(calls_log.grouped(rows, now)) == ["Today", "Yesterday"]
    assert calls_log.duration(5) == "5 s" and calls_log.duration(185) == "3 min 05 s"


@pytest.mark.parametrize("uri,number,ext", [
    ("tel:+1-201-555-0123", "+12015550123", ""),
    ("tel:+91%2098765%2043210", "+919876543210", ""),
    ("tel:(020)%20123.4567", "0201234567", ""),
    ("tel:863-1234;phone-context=+1-914-555", "+19145558631234", ""),
    ("tel:7042;phone-context=example.com", "7042", ""),
    ("tel:+1-201-555-0123;ext=1234;isub=99", "+12015550123", "1234"),
    ("tel://100", "100", ""),
    ("sip:+919876543210@ims.example;user=phone", "+919876543210", ""),
    ("9876543210", "9876543210", ""),
])
def test_tel_links_per_rfc_3966(uri, number, ext):
    got = tel_uri.parse(uri)
    assert (got.number, got.extension, got.problem) == (number, ext, "")


@pytest.mark.parametrize("uri", ["tel:", "tel:call-me-maybe", "sip:alice@example.com", "mailto:a@b",
                                 "tel:12;phone-context=+1;ext=abc"])
def test_links_with_no_number_say_why(uri):
    got = tel_uri.parse(uri)
    if uri.endswith("ext=abc"):
        assert got.number == "+112" and got.extension == ""
    else:
        assert got.number == "" and got.problem

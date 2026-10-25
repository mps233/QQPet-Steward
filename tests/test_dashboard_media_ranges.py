from __future__ import annotations

from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import threading

import pytest

import dashboard


BODY = b"0123456789"
ORIGIN = "https://test-client.example"


@pytest.fixture
def media_server(tmp_path, monkeypatch):
    icons = tmp_path / "static" / "qp-icons"
    icons.mkdir(parents=True)
    (icons / "range.mp4").write_bytes(BODY)
    (icons / "empty.mp4").write_bytes(b"")
    monkeypatch.setattr(dashboard, "BASE", tmp_path)

    class MediaHandler(dashboard.Handler):
        def _cors_headers(self):
            return [("Access-Control-Allow-Origin", ORIGIN)]

    service = ThreadingHTTPServer(("127.0.0.1", 0), MediaHandler)
    thread = threading.Thread(
        target=service.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True,
    )
    thread.start()
    try:
        yield service.server_address
    finally:
        service.shutdown()
        service.server_close()
        thread.join(timeout=2)


def request(address, range_header=None, *, name="range.mp4"):
    connection = HTTPConnection(*address, timeout=3)
    try:
        headers = {"Range": range_header} if range_header is not None else {}
        connection.request("GET", f"/qp-icons/{name}", headers=headers)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def check_common_headers(headers, payload):
    assert headers["Content-Type"] == "video/mp4"
    assert headers["Accept-Ranges"] == "bytes"
    assert headers["Cache-Control"] == "max-age=3600"
    assert headers["Access-Control-Allow-Origin"] == ORIGIN
    assert int(headers["Content-Length"]) == len(payload)
    assert "Content-Encoding" not in headers


@pytest.mark.parametrize("range_header, expected, content_range", [
    ("bytes=0-1", b"01", "bytes 0-1/10"),
    ("bytes=2-5", b"2345", "bytes 2-5/10"),
    ("bytes=4-", b"456789", "bytes 4-9/10"),
    ("bytes=-3", b"789", "bytes 7-9/10"),
    ("bytes=-20", BODY, "bytes 0-9/10"),
    ("bytes=3-999", b"3456789", "bytes 3-9/10"),
    ("bytes=0002-0004", b"234", "bytes 2-4/10"),
    ("BYTES=0-1", b"01", "bytes 0-1/10"),
])
def test_valid_single_byte_ranges_have_exact_payload_and_headers(
        media_server, range_header, expected, content_range):
    status, headers, payload = request(media_server, range_header)
    assert status == 206 and payload == expected
    assert headers["Content-Range"] == content_range
    check_common_headers(headers, payload)


@pytest.mark.parametrize("range_header", [
    None, "items=0-1", "wat", "bytes=wat", "bytes=", "bytes=-", "bytes=5-2",
    "bytes=0-1,5-6", "bytes=999-,0-1", "bytes=0-1,",
])
def test_unsupported_or_malformed_ranges_are_ignored_instead_of_returning_500(
        media_server, range_header):
    status, headers, payload = request(media_server, range_header)
    assert status == 200 and payload == BODY
    assert "Content-Range" not in headers
    check_common_headers(headers, payload)


@pytest.mark.parametrize("range_header", ["bytes=10-", "bytes=99-100", "bytes=-0", "bytes=-000"])
def test_unsatisfiable_ranges_return_416_with_extent_and_empty_body(media_server, range_header):
    status, headers, payload = request(media_server, range_header)
    assert status == 416 and payload == b""
    assert headers["Content-Range"] == "bytes */10"
    check_common_headers(headers, payload)


@pytest.mark.parametrize("range_header", [None, "bytes=0-0", "bytes=-0", "bytes=-1", "bytes=9-", "bytes=wat"])
def test_empty_media_ignores_range_without_inventing_a_content_range(media_server, range_header):
    status, headers, payload = request(media_server, range_header, name="empty.mp4")
    assert status == 200 and payload == b""
    assert "Content-Range" not in headers
    check_common_headers(headers, payload)


@pytest.mark.parametrize("range_header, expected_status, expected_payload", [
    ("bytes=" + "9" * 5000 + "-", 416, b""),
    ("bytes=-" + "9" * 5000, 206, BODY),
    ("bytes=0-" + "9" * 5000, 206, BODY),
    ("bytes=" + "0" * 5000 + "1-", 206, BODY[1:]),
    ("bytes=" + "9" * 5000 + "-" + "8" * 5000, 200, BODY),
])
def test_arbitrarily_large_decimal_ranges_do_not_trigger_integer_conversion_errors(
        media_server, range_header, expected_status, expected_payload):
    status, headers, payload = request(media_server, range_header)
    assert status == expected_status and payload == expected_payload
    check_common_headers(headers, payload)

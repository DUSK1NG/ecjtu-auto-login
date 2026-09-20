from __future__ import annotations

import io
from unittest.mock import Mock

import pytest
import requests
from requests.adapters import BaseAdapter
from urllib3.response import HTTPResponse

import src.network as network
from src.network import NetworkChecker, NetworkStatus, ProbeSession, _PROBES


def response(status: int, body: bytes = b"", location: str | None = None) -> Mock:
    result = Mock()
    result.status_code = status
    result.headers = {"Location": location} if location is not None else {}
    result.iter_content.return_value = iter([body] if body else [])
    return result


def checker_with(*responses: Mock | Exception) -> tuple[NetworkChecker, Mock]:
    session = Mock()
    session.get.side_effect = responses
    return NetworkChecker(session=session, interface_check=lambda: True), session


class CountingBody(io.BytesIO):
    def __init__(self, initial_bytes: bytes) -> None:
        super().__init__(initial_bytes)
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        data = super().read(size)
        self.bytes_read += len(data)
        return data


class RedirectBodyAdapter(BaseAdapter):
    def __init__(self) -> None:
        self.urls: list[str] = []
        self.bodies: list[CountingBody] = []

    def send(self, request: requests.PreparedRequest, **kwargs) -> requests.Response:
        self.urls.append(request.url or "")
        body = CountingBody(b"x" * 100_000)
        self.bodies.append(body)
        raw = HTTPResponse(
            body=body,
            status=302,
            headers={"Location": "http://portal.example/login"},
            preload_content=False,
        )
        result = requests.Response()
        result.status_code = 302
        result.headers["Location"] = "http://portal.example/login"
        result.url = request.url
        result.request = request
        result.raw = raw
        result.connection = self
        return result

    def close(self) -> None:
        pass


def test_online_requires_exact_expected_content() -> None:
    checker, session = checker_with(response(200, b"Microsoft Connect Test"))

    result = checker.check()

    assert result.status is NetworkStatus.INTERNET_OK
    assert result.portal_url is None
    session.get.assert_called_once_with(
        _PROBES[0].url,
        allow_redirects=False,
        timeout=(1.5, 1.5),
        stream=True,
    )
    assert session.trust_env is False


def test_no_network_does_not_send_probes() -> None:
    session = Mock()
    checker = NetworkChecker(session=session, interface_check=lambda: False)

    result = checker.check()

    assert result.status is NetworkStatus.NO_NETWORK
    session.get.assert_not_called()


def test_redirect_is_reported_as_portal_without_leaking_it_in_reason() -> None:
    portal = "http://portal.example/login?token=secret#fragment"
    checker, _ = checker_with(
        response(302, location=portal),
        requests.ConnectionError("private diagnostic"),
        response(503),
        response(503),
    )

    result = checker.check()

    assert result.status is NetworkStatus.PORTAL_REQUIRED
    assert result.portal_url == "http://portal.example/login?token=secret"
    assert portal not in result.reason
    assert "private diagnostic" not in result.reason


@pytest.mark.parametrize(
    "failure",
    [
        requests.Timeout("slow response details"),
        requests.ConnectionError("connection details"),
    ],
)
def test_transport_failures_result_in_lan_only(failure: Exception) -> None:
    checker, _ = checker_with(failure, failure, failure, failure)

    result = checker.check()

    assert result.status is NetworkStatus.LAN_ONLY
    assert str(failure) not in result.reason


def test_server_unavailable_results_in_lan_only() -> None:
    checker, _ = checker_with(
        response(503), response(502), response(500), response(503)
    )

    assert checker.check().status is NetworkStatus.LAN_ONLY


def test_unexpected_body_is_not_accepted_as_internet() -> None:
    checker, _ = checker_with(
        response(200, b"almost Microsoft Connect Test"),
        response(200, b"unexpected"),
        response(200, b"unexpected"),
        response(200, b"unexpected"),
    )

    assert checker.check().status is NetworkStatus.LAN_ONLY


def test_login_page_is_portal_evidence() -> None:
    checker, _ = checker_with(
        response(200, b'<html><form><input type="password"></form></html>'),
        response(503),
        response(503),
        response(503),
    )

    assert checker.check().status is NetworkStatus.PORTAL_REQUIRED


def test_success_has_priority_over_an_earlier_redirect() -> None:
    first = response(302, location="http://portal.example/login")
    second = response(200, b"Microsoft Connect Test")
    checker, session = checker_with(first, second)

    result = checker.check()

    assert result.status is NetworkStatus.INTERNET_OK
    assert result.portal_url is None
    assert session.get.call_count == 2
    first.close.assert_called_once_with()
    second.close.assert_called_once_with()


def test_204_probe_can_confirm_internet_after_other_failures() -> None:
    checker, session = checker_with(
        response(503), response(503), response(503), response(204)
    )

    result = checker.check()

    assert result.status is NetworkStatus.INTERNET_OK
    assert session.get.call_count == 4


def test_body_deadline_is_checked_after_each_byte(monkeypatch: pytest.MonkeyPatch) -> None:
    slow = response(200, b"x")
    checker, _ = checker_with(slow, response(503), response(503), response(503))
    clock = Mock(side_effect=[0.0, 0.0, 7.0, 10.0, 10.0, 20.0, 20.0, 30.0, 30.0])
    monkeypatch.setattr(network.time, "monotonic", clock)

    result = checker.check()

    assert result.status is NetworkStatus.LAN_ONLY
    slow.iter_content.assert_called_once_with(chunk_size=1)


def test_probe_session_does_not_preread_or_follow_redirect_body() -> None:
    adapter = RedirectBodyAdapter()
    session = ProbeSession()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    checker = NetworkChecker(session=session, interface_check=lambda: True)

    result = checker.check()

    assert result.status is NetworkStatus.PORTAL_REQUIRED
    assert adapter.urls == [probe.url for probe in _PROBES]
    assert all(body.bytes_read == 0 for body in adapter.bodies)


def test_plain_requests_session_is_rejected() -> None:
    with requests.Session() as session:
        with pytest.raises(TypeError, match="ProbeSession"):
            NetworkChecker(session=session, interface_check=lambda: True)

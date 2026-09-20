import logging
from unittest.mock import Mock
import pytest
import requests
from src.config import Config
from src.controller import LoginController, State
from src.network import NetworkResult, NetworkStatus
from src.portal import AuthFailure, AuthResult, UnconfiguredPortalAdapter


def net(status):
    return NetworkResult(status, "test")


def make(statuses, result=None, adapter=None, config=None):
    checker = Mock()
    checker.check.side_effect = [net(status) for status in statuses]
    adapter = adapter or Mock()
    adapter.matches.return_value = True if isinstance(adapter, Mock) else False
    if isinstance(adapter, Mock):
        adapter.authenticate.return_value = result or AuthResult(True)
    clock = Mock(return_value=100.0)
    controller = LoginController(config or Config("user", "secret"), checker, adapter,
                                 logging.getLogger("test.controller"), clock)
    return controller, checker, adapter, clock


def test_already_online_never_authenticates():
    c, _, a, _ = make([NetworkStatus.INTERNET_OK])
    c.step()
    assert c.state == State.INTERNET_OK
    a.matches.assert_not_called()
    a.authenticate.assert_not_called()


def test_no_network_never_authenticates():
    c, _, a, _ = make([NetworkStatus.NO_NETWORK])
    c.step()
    assert c.state == State.NO_NETWORK
    a.authenticate.assert_not_called()


@pytest.mark.parametrize("status,expected", [(NetworkStatus.LAN_ONLY, State.LAN_ONLY),
                                            (NetworkStatus.PORTAL_REQUIRED, State.PORTAL_REQUIRED)])
def test_unknown_environment_never_authenticates(status, expected):
    c, _, a, _ = make([status])
    a.matches.return_value = False
    c.step()
    assert c.state == expected
    a.authenticate.assert_not_called()


def test_default_adapter_is_inert():
    a = UnconfiguredPortalAdapter()
    assert not a.matches(net(NetworkStatus.PORTAL_REQUIRED))
    assert a.authenticate("u", "p") == AuthResult(False, AuthFailure.NOT_CONFIGURED)


def test_auth_success_is_verified(caplog):
    c, checker, adapter, _ = make([NetworkStatus.PORTAL_REQUIRED, NetworkStatus.INTERNET_OK])
    with caplog.at_level(logging.INFO):
        c.step()
    assert c.state == State.INTERNET_OK
    assert checker.check.call_count == 2
    adapter.authenticate.assert_called_once_with("user", "secret")
    assert "AUTH_SUCCESS" in caplog.text
    assert c.failures == 0


@pytest.mark.parametrize("failure", [AuthFailure.INVALID_CREDENTIALS, AuthFailure.SERVER_UNAVAILABLE,
                                     AuthFailure.TIMEOUT, AuthFailure.REJECTED])
def test_auth_failure(failure):
    c, checker, _, _ = make([NetworkStatus.PORTAL_REQUIRED], AuthResult(False, failure))
    assert c.step() == 2
    assert c.state == State.AUTH_FAILED
    assert checker.check.call_count == 1


@pytest.mark.parametrize("error", [requests.Timeout("sensitive"), requests.ConnectionError("secret"),
                                   requests.HTTPError("private response")])
def test_request_errors_are_safe(error, caplog):
    c, _, a, _ = make([NetworkStatus.PORTAL_REQUIRED])
    a.authenticate.side_effect = error
    c.step()
    assert c.state == State.AUTH_FAILED
    assert str(error) not in caplog.text


def test_missing_credentials():
    c, _, a, _ = make([NetworkStatus.PORTAL_REQUIRED], config=Config())
    c.step()
    assert c.state == State.AUTH_FAILED
    a.authenticate.assert_not_called()


def test_backoff_cap_and_no_early_retry():
    c, checker, a, clock = make([], AuthResult(False, AuthFailure.INVALID_CREDENTIALS))
    checker.check.side_effect = None
    checker.check.return_value = net(NetworkStatus.PORTAL_REQUIRED)
    for i, delay in enumerate([2, 3, 5, 10, 10]):
        now = clock.return_value
        c.step()
        assert c.next_auth_at == now + delay
        assert a.authenticate.call_count == i + 1
        clock.return_value = now + delay - 1
        c.step()
        assert a.authenticate.call_count == i + 1
        clock.return_value += 1


def test_success_response_without_internet_fails():
    c, _, _, _ = make([NetworkStatus.PORTAL_REQUIRED, NetworkStatus.LAN_ONLY])
    c.step()
    assert c.state == State.AUTH_FAILED


def test_network_breaks_during_verification_then_recovers():
    c, _, a, clock = make([NetworkStatus.PORTAL_REQUIRED, NetworkStatus.NO_NETWORK,
                            NetworkStatus.NO_NETWORK, NetworkStatus.PORTAL_REQUIRED,
                            NetworkStatus.INTERNET_OK])
    c.step()
    assert c.state == State.NO_NETWORK
    c.step()
    assert a.authenticate.call_count == 1
    clock.return_value = 130
    c.step()
    assert c.state == State.INTERNET_OK
    assert a.authenticate.call_count == 2


def test_expired_session_reauthenticates():
    c, _, a, _ = make([NetworkStatus.INTERNET_OK, NetworkStatus.PORTAL_REQUIRED, NetworkStatus.INTERNET_OK])
    c.step()
    c.step()
    a.authenticate.assert_called_once()
    assert c.state == State.INTERNET_OK


def test_online_clears_backoff():
    c, _, _, _ = make([NetworkStatus.PORTAL_REQUIRED, NetworkStatus.INTERNET_OK], AuthResult(False))
    c.step()
    c.step()
    assert (c.failures, c.next_auth_at) == (0, 0)


def test_stop_event_wait_is_interruptible():
    c, _, _, _ = make([NetworkStatus.INTERNET_OK])
    stop = Mock()
    stop.is_set.side_effect = [False, True]
    c.run(stop)
    stop.wait.assert_called_once_with(10)

from unittest.mock import Mock
import logging

import pytest

from src.config import Config
from src.controller import LoginController
from src.ecjtu import EcjtuPortalAdapter
from src.network import NetworkChecker, NetworkResult, NetworkStatus
from src.portal import AuthFailure, AuthResult
from test_network import response


PORTAL = 'http://172.16.2.100/a70.htm?wlanuserip=10.20.30.40&wlanacname=ecjtu_nic_ME60'


def fast_checker(location=PORTAL, local_ip='10.20.30.40'):
    session = Mock()
    first = response(302, location=location)
    session.get.side_effect = [first, response(200, b'Microsoft Connect Test')]
    adapter_session = Mock()
    adapter = EcjtuPortalAdapter(session=adapter_session, local_ip_resolver=lambda: local_ip)
    checker = NetworkChecker(session, lambda: True, portal_validator=adapter.recognizes_redirect)
    return checker, session, first, adapter_session


def test_trusted_school_redirect_skips_slow_fallback_probes():
    checker, session, first, adapter_session = fast_checker()
    assert checker.check().status == NetworkStatus.PORTAL_REQUIRED
    assert session.get.call_count == 1
    first.close.assert_called_once()
    adapter_session.get.assert_not_called()
    adapter_session.post.assert_not_called()


@pytest.mark.parametrize('location,local_ip', [
    ('http://other.example/login', '10.20.30.40'),
    (PORTAL, '10.20.30.41'),
    ('http://172.16.2.100/a70.htm', '10.20.30.40'),
])
def test_unconfirmed_redirect_keeps_full_internet_detection(location, local_ip):
    checker, session, _, _ = fast_checker(location, local_ip)
    assert checker.check().status == NetworkStatus.INTERNET_OK
    assert session.get.call_count == 2


def test_post_auth_verification_does_not_short_circuit_on_stale_redirect():
    checker, session, _, _ = fast_checker()
    assert checker.check(allow_fast_portal=False).status == NetworkStatus.INTERNET_OK
    assert session.get.call_count == 2


def test_controller_polls_missing_interface_after_one_second():
    checker, adapter = Mock(), Mock()
    checker.check.return_value = NetworkResult(NetworkStatus.NO_NETWORK, '')
    controller = LoginController(Config(), checker, adapter, logging.getLogger('test.fast'))
    assert controller.step() == 1
    adapter.matches.assert_not_called()


def test_controller_does_not_repeat_portal_discovery_during_backoff():
    checker, adapter = Mock(), Mock()
    checker.check.return_value = NetworkResult(NetworkStatus.PORTAL_REQUIRED, '')
    adapter.matches.return_value = True
    adapter.authenticate.return_value = AuthResult(False, AuthFailure.TIMEOUT)
    clock = Mock(return_value=100.0)
    controller = LoginController(Config('user', 'secret'), checker, adapter,
                                 logging.getLogger('test.fast'), clock)
    assert controller.step() == 2
    clock.return_value = 101.0
    assert controller.step() == 1
    adapter.matches.assert_called_once()
    adapter.authenticate.assert_called_once()


def test_unknown_environment_rechecks_quickly_without_authentication():
    checker, adapter = Mock(), Mock()
    checker.check.return_value = NetworkResult(NetworkStatus.LAN_ONLY, '')
    adapter.matches.return_value = False
    controller = LoginController(Config(), checker, adapter, logging.getLogger('test.fast'))
    assert controller.step() == 1
    adapter.authenticate.assert_not_called()


def test_controller_uses_full_verification_after_auth():
    checker, adapter = Mock(), Mock()
    checker.check.side_effect = [NetworkResult(NetworkStatus.PORTAL_REQUIRED, ''),
                                 NetworkResult(NetworkStatus.INTERNET_OK, '')]
    adapter.matches.return_value = True
    adapter.authenticate.return_value = AuthResult(False, verification_required=True)
    controller = LoginController(Config('user', 'secret'), checker, adapter,
                                 logging.getLogger('test.fast'))
    assert controller.step() == 10
    checker.check.assert_called_with(allow_fast_portal=False)

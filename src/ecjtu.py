"""华东交通大学门户协议适配器。"""

from __future__ import annotations

import ipaddress
import socket
import hashlib
import time
import logging
from html.parser import HTMLParser
from collections.abc import Callable
from urllib.parse import parse_qs, urljoin, urlsplit, urlencode

import requests

from .network import NetworkResult, NetworkStatus, ProbeSession, _read_limited_body, _ProbeDeadlineExceeded
from .portal import AuthFailure, AuthResult, PortalAdapter


_PORTAL_HOST = "172.16.2.100"
_PORTAL_URL = f"http://{_PORTAL_HOST}:801/eportal/"
_REQUEST_TIMEOUT = (2.5, 5.0)
_ROUTE_TIMEOUT_SECONDS = 2.5
_AC_NAME = "ecjtu_nic_ME60"
_LOG = logging.getLogger("campus_auto_login")


def _log_auth_response(status: int, location: str | None):
    """只记录白名单数字码，不记录跳转 URL、Cookie、错误正文或令牌。"""
    try:
        query = parse_qs(urlsplit(location or "").query, max_num_fields=64)
    except ValueError:
        query = {}
    def number(key):
        values = query.get(key, [])
        value = values[0] if len(values) == 1 else ""
        return value if value.isascii() and value.isdecimal() and len(value) <= 8 else "unknown"
    _LOG.info("AUTH_DIAG response | HTTP=%s RetCode=%s ACLogOut=%s ErrorMsg_present=%s",
              status, number("RetCode"), number("ACLogOut"), "ErrorMsg" in query)


class _LoginPageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.title = []
        self.has_script = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        if tag == "script":
            src = dict(attrs).get("src", "")
            self.has_script |= urljoin(f"http://{_PORTAL_HOST}/", src) in {
                f"http://{_PORTAL_HOST}/a42.js", f"http://{_PORTAL_HOST}:80/a42.js"
            }

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)


def _is_known_login_page(body: bytes) -> bool:
    # 整页含动态值，不比较哈希；只识别现场观察到的固定模板结构。
    if len(body) > 4096:
        return False
    for encoding in ("utf-8", "gb18030"):
        text = body.decode(encoding, errors="replace")
        parser = _LoginPageParser()
        parser.feed(text)
        if ("".join(parser.title).strip() == "上网登录窗" and parser.has_script
                and all(marker in text for marker in ("/eportal/extern/test/", "DDDDD", "upass"))):
            return True
    return False


class EcjtuPortalAdapter(PortalAdapter):
    """只对已验证的华东交通大学门户探针结果提交认证。"""

    def __init__(
        self,
        isp_suffix: str = "@cmcc",
        session: requests.Session | None = None,
        local_ip_resolver: Callable[[], str] | None = None,
    ) -> None:
        if isp_suffix not in {"", "@cmcc", "@telecom", "@unicom"}:
            raise ValueError("运营商后缀只允许为空、@cmcc、@telecom 或 @unicom")
        if session is None:
            session = ProbeSession()
        elif isinstance(session, requests.Session) and not isinstance(
            session, ProbeSession
        ):
            raise TypeError("真实 requests 会话必须使用 ProbeSession")

        session.trust_env = False
        self._session = session
        self._isp_suffix = isp_suffix
        self._local_ip_resolver = local_ip_resolver or _resolve_local_ip
        self._matched_context: tuple[str, str] | None = None

    def recognizes_redirect(self, network: NetworkResult) -> bool:
        """快速识别仅使用重定向及当前路由，不请求页面、不提交凭据。"""
        if network.status != NetworkStatus.PORTAL_REQUIRED or not network.portal_url:
            return False
        context = _validated_portal_context(network.portal_url)
        return context is not None and self._current_local_ip() == context[0]

    def matches(self, network: NetworkResult) -> bool:
        self._matched_context = None
        if network.status not in {NetworkStatus.LAN_ONLY, NetworkStatus.PORTAL_REQUIRED}:
            return False

        if network.portal_url:
            portal_context = _validated_portal_context(network.portal_url)
            if portal_context is None:
                _LOG.info("PORTAL_DIAG redirect_context_invalid | 学校重定向地址或参数不匹配")
                try:
                    host = urlsplit(network.portal_url).hostname or ""
                except ValueError:
                    host = ""
                if host == "msn.com" or host.endswith(".msn.com"):
                    _LOG.info("PORTAL_DIAG msn_redirect | 改用固定学校登录页")
                    portal_context = self._discover_from_entry()
        else:
            portal_context = self._discover_from_entry()
        if portal_context is None:
            return False
        portal_ip, _ = portal_context
        local_ip = self._current_local_ip()
        if local_ip != portal_ip:
            _LOG.info("PORTAL_DIAG source_ip_mismatch | 路由源 IP 不可用或与门户 IP 不一致")
            return False

        self._matched_context = portal_context
        return True

    def _discover_from_entry(self) -> tuple[str, str] | None:
        """探针未给出门户 URL 时，只读取固定入口，绝不主动打开认证接口。"""
        entry_ip = self._current_local_ip()
        if entry_ip is None:
            _LOG.info("PORTAL_DIAG no_route_source | 无法获取当前路由源 IP")
            return None
        url = f"http://{_PORTAL_HOST}/a70.htm?" + urlencode({
            "wlanuserip": entry_ip, "wlanacip": "null", "wlanacname": _AC_NAME,
            "vlanid": "0", "ip": entry_ip, "ssid": "null", "areaID": "null",
            "mac": "00-00-00-00-00-00",
        })
        _LOG.info("PORTAL_DIAG preferred_entry | 请求指定学校 a70.htm 登录页，使用当前 IP")
        for attempt in range(2):
            response = None
            started = time.monotonic()
            try:
                response = self._session.get(url, allow_redirects=False,
                                             stream=True, timeout=_REQUEST_TIMEOUT)
                if response.status_code == 302 and attempt == 0:
                    target = urljoin(url, response.headers.get("Location", ""))
                    context = _validated_portal_context(target)
                    if context is not None:
                        return context
                    # 无查询参数的固定登录页可进行一次受限读取。
                    if target not in {f"http://{_PORTAL_HOST}/a70.htm", f"http://{_PORTAL_HOST}:80/a70.htm"}:
                        _LOG.info("PORTAL_DIAG entry_redirect_rejected | 学校入口跳转未识别")
                        return None
                    url = target
                    continue
                if response.status_code != 200:
                    _LOG.info("PORTAL_DIAG entry_http_error | HTTP=%s", response.status_code)
                    return None
                body = _read_limited_body(response, started)
                if not _is_known_login_page(body):
                    logout = any("注销页" in body.decode(enc, errors="replace") for enc in ("utf-8", "gb18030"))
                    _LOG.info("PORTAL_DIAG %s | 页面字节数=%d，指纹=%s；未提交凭据",
                              "entry_logout_page" if logout else "entry_structure_mismatch",
                              len(body), hashlib.sha256(body).hexdigest()[:16])
                    return None
                _LOG.info("PORTAL_DIAG login_page_matched | 学校登录页结构匹配")
                local_ip = self._current_local_ip()
                if local_ip != entry_ip:
                    _LOG.info("PORTAL_DIAG source_ip_mismatch | 读取登录页期间路由 IP 变化")
                    return None
                return (entry_ip, "null")
            except (requests.Timeout, _ProbeDeadlineExceeded):
                _LOG.info("PORTAL_DIAG entry_timeout | 学校入口连接或读取超时")
                return None
            except requests.RequestException:
                _LOG.info("PORTAL_DIAG entry_request_failed | 学校入口连接失败")
                return None
            finally:
                if response is not None:
                    response.close()
        return None

    def authenticate(self, username: str, password: str) -> AuthResult:
        matched_context = self._matched_context
        self._matched_context = None
        if matched_context is None:
            return AuthResult(False, AuthFailure.REJECTED)
        matched_ip, wlan_ac_ip = matched_context
        if self._current_local_ip() != matched_ip:
            return AuthResult(False, AuthFailure.REJECTED)

        account = self._qualified_account(username)
        if account is None or not password:
            return AuthResult(False, AuthFailure.REJECTED)

        params = {
            "c": "ACSetting",
            "a": "Login",
            "protocol": "http:",
            "hostname": _PORTAL_HOST,
            "iTermType": "1",
            "wlanuserip": matched_ip,
            "wlanacip": wlan_ac_ip,
            "wlanacname": _AC_NAME,
            "mac": "00-00-00-00-00-00",
            "ip": matched_ip,
            "enAdvert": "0",
            "queryACIP": "0",
            "loginMethod": "1",
        }
        data = {
            "DDDDD": f",0,{account}",
            "upass": password,
            "R1": "0",
            "R2": "0",
            "R3": "0",
            "R6": "0",
            "para": "00",
            "0MKKey": "123456",
            "buttonClicked": "",
            "redirect_url": "",
            "err_flag": "",
            "username": "",
            "password": "",
            "user": "",
            "cmd": "",
            "Login": "",
        }
        response = None
        try:
            response = self._session.post(
                _PORTAL_URL,
                params=params,
                data=data,
                headers={
                    "Origin": f"http://{_PORTAL_HOST}",
                    "Referer": f"http://{_PORTAL_HOST}/",
                },
                allow_redirects=False,
                timeout=_REQUEST_TIMEOUT,
                stream=True,
            )
            _log_auth_response(response.status_code, response.headers.get("Location"))
            if response.status_code >= 500:
                return AuthResult(False, AuthFailure.SERVER_UNAVAILABLE)
            if response.status_code == 302 and _is_trusted_result_redirect(
                response.headers.get("Location")
            ):
                return AuthResult(False, verification_required=True)
            return AuthResult(False, AuthFailure.REJECTED)
        except requests.Timeout:
            return AuthResult(False, AuthFailure.TIMEOUT)
        except requests.ConnectionError:
            return AuthResult(False, AuthFailure.SERVER_UNAVAILABLE)
        except requests.RequestException:
            return AuthResult(False, AuthFailure.REJECTED)
        finally:
            if response is not None:
                response.close()

    def _current_local_ip(self) -> str | None:
        try:
            candidate = self._local_ip_resolver()
        except (OSError, ValueError, TypeError):
            return None
        return _normal_ipv4(candidate)

    def _qualified_account(self, username: str) -> str | None:
        if not username or "," in username:
            return None
        if "@" in username:
            if not self._isp_suffix or not username.endswith(self._isp_suffix):
                return None
            if username.count("@") != 1 or username == self._isp_suffix:
                return None
            return username
        return f"{username}{self._isp_suffix}"


def _resolve_local_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route_socket:
        route_socket.settimeout(_ROUTE_TIMEOUT_SECONDS)
        route_socket.connect((_PORTAL_HOST, 801))
        return str(route_socket.getsockname()[0])


def _validated_portal_context(portal_url: str) -> tuple[str, str] | None:
    try:
        parsed = urlsplit(portal_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != _PORTAL_HOST
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 80}
            or parsed.path not in {"/a70.htm", "/a79.htm"}
            or parsed.fragment
        ):
            return None
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        return None

    if query.get("wlanacname") != [_AC_NAME]:
        return None
    user_ips = query.get("wlanuserip")
    if user_ips is None or len(user_ips) != 1:
        return None
    user_ip = _normal_ipv4(user_ips[0])
    if user_ip is None:
        return None

    wlan_ac_ips = query.get("wlanacip")
    if wlan_ac_ips is None:
        wlan_ac_ip = "null"
    elif len(wlan_ac_ips) == 1 and wlan_ac_ips[0] == "null":
        wlan_ac_ip = "null"
    elif len(wlan_ac_ips) == 1:
        wlan_ac_ip = _normal_ipv4(wlan_ac_ips[0])
        if wlan_ac_ip is None:
            return None
    else:
        return None
    return user_ip, wlan_ac_ip


def _normal_ipv4(candidate: object) -> str | None:
    if not isinstance(candidate, str):
        return None
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    if not isinstance(address, ipaddress.IPv4Address):
        return None
    if address.is_loopback or address.is_unspecified or address.is_multicast:
        return None
    return str(address)


def _is_trusted_result_redirect(location: str | None) -> bool:
    if not location:
        return False
    try:
        parsed = urlsplit(urljoin(_PORTAL_URL, location))
        return (
            parsed.scheme == "http"
            and parsed.hostname == _PORTAL_HOST
            and parsed.username is None
            and parsed.password is None
            and parsed.port in {None, 80}
            # 学校可返回不同结果页；此处不跟随跳转，也不据此判定成功。
            # 是否认证成功仍必须由独立 Internet 探针确认。
            and not parsed.fragment
        )
    except (TypeError, ValueError):
        return False

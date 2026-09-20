"""Windows network and captive-portal detection."""

from __future__ import annotations

import ctypes
import ipaddress
import os
import socket
import time
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

_LOG = logging.getLogger("campus_auto_login")


class NetworkStatus(str, Enum):
    NO_NETWORK = "no_network"
    LAN_ONLY = "lan_only"
    PORTAL_REQUIRED = "portal_required"
    INTERNET_OK = "internet_ok"


@dataclass(frozen=True)
class NetworkResult:
    status: NetworkStatus
    reason: str
    portal_url: str | None = None


@dataclass(frozen=True)
class _Probe:
    url: str
    expected_status: int
    expected_body: bytes


_PROBES = (
    _Probe(
        "http://www.msftconnecttest.com/connecttest.txt",
        200,
        b"Microsoft Connect Test",
    ),
    _Probe(
        "https://www.msftconnecttest.com/connecttest.txt",
        200,
        b"Microsoft Connect Test",
    ),
    _Probe("http://www.msftncsi.com/ncsi.txt", 200, b"Microsoft NCSI"),
    _Probe("https://connectivitycheck.gstatic.com/generate_204", 204, b""),
)

_REQUEST_TIMEOUT = (1.5, 1.5)
_PROBE_WALL_SECONDS = 6.0
_MAX_BODY_BYTES = 4096
_PORTAL_MARKERS = (
    b"<form",
    b"type=\"password\"",
    b"type='password'",
    b"captive portal",
    b"hotspot login",
    b"network login",
    "登录".encode(),
    "认证".encode(),
)


class _ProbeDeadlineExceeded(Exception):
    pass


class ProbeSession(requests.Session):
    """Session that never prepares or follows redirects for connectivity probes."""

    def resolve_redirects(self, resp: requests.Response, req: requests.PreparedRequest, **kwargs):
        # requests normally prepares Response.next even with redirects disabled;
        # that path consumes the redirect body before yielding the next request.
        return iter(())


class NetworkChecker:
    def __init__(
        self,
        session: requests.Session | None = None,
        interface_check: Callable[[], bool] | None = None,
        portal_validator: Callable[[NetworkResult], bool] | None = None,
    ) -> None:
        if session is None:
            self._session = ProbeSession()
        elif isinstance(session, requests.Session) and not isinstance(
            session, ProbeSession
        ):
            raise TypeError(
                "真实 requests 会话必须使用 ProbeSession，以限制重定向响应读取"
            )
        else:
            self._session = session
        # Connectivity checks must reflect the machine rather than proxy settings
        # inherited from the shell. TLS certificate verification stays enabled.
        self._session.trust_env = False
        self._interface_check = interface_check or _default_interface_check
        self._portal_validator = portal_validator

    def check(self, *, allow_fast_portal: bool = True) -> NetworkResult:
        try:
            has_interface = bool(self._interface_check())
        except Exception:
            has_interface = False

        if not has_interface:
            return NetworkResult(NetworkStatus.NO_NETWORK, "未检测到活动网络接口")

        portal_url: str | None = None
        portal_evidence = False

        for probe in _PROBES:
            started_at = time.monotonic()
            response = None
            try:
                response = self._session.get(
                    probe.url,
                    allow_redirects=False,
                    timeout=_REQUEST_TIMEOUT,
                    stream=True,
                )
                if time.monotonic() - started_at > _PROBE_WALL_SECONDS:
                    raise _ProbeDeadlineExceeded

                if response.status_code == probe.expected_status:
                    if probe.expected_status == 204:
                        return NetworkResult(
                            NetworkStatus.INTERNET_OK,
                            "互联网探针返回预期状态",
                        )
                    body = _read_limited_body(response, started_at)
                    if body == probe.expected_body:
                        return NetworkResult(
                            NetworkStatus.INTERNET_OK,
                            "互联网探针返回预期内容",
                        )
                    if _looks_like_portal(body):
                        portal_evidence = True
                elif 300 <= response.status_code < 400:
                    portal_evidence = True
                    if portal_url is None:
                        portal_url = _safe_redirect_url(
                            probe.url, response.headers.get("Location")
                        )
                    # Only a school-validated redirect may skip the slow probes.
                    # Authentication still revalidates the route and portal context.
                    if allow_fast_portal and portal_url and self._portal_validator:
                        candidate = NetworkResult(
                            NetworkStatus.PORTAL_REQUIRED, "已识别学校门户重定向", portal_url
                        )
                        if self._portal_validator(candidate):
                            _LOG.info("PROBE_DIAG campus_redirect | 已确认学校门户，立即进入认证")
                            return candidate
                elif response.status_code == 511:
                    portal_evidence = True
                elif response.status_code == 200:
                    body = _read_limited_body(response, started_at)
                    if _looks_like_portal(body):
                        portal_evidence = True
                _LOG.info("PROBE_DIAG unexpected_response | 探针=%s，HTTP=%s", probe.url, response.status_code)
            except (requests.Timeout, _ProbeDeadlineExceeded):
                _LOG.info("PROBE_DIAG timeout | 探针=%s", probe.url)
                continue
            except requests.RequestException:
                _LOG.info("PROBE_DIAG request_failed | 探针=%s", probe.url)
                continue
            finally:
                if response is not None:
                    response.close()

        if portal_evidence:
            return NetworkResult(
                NetworkStatus.PORTAL_REQUIRED,
                "网络响应显示可能需要门户认证",
                portal_url,
            )
        return NetworkResult(NetworkStatus.LAN_ONLY, "网络接口可用，但互联网探针均未通过")


def _read_limited_body(response: requests.Response, started_at: float) -> bytes:
    body = bytearray()
    # One-byte yields let us re-check the wall clock even for a drip response.
    # The body cap keeps the extra iterator overhead small.
    for chunk in response.iter_content(chunk_size=1):
        if time.monotonic() - started_at > _PROBE_WALL_SECONDS:
            raise _ProbeDeadlineExceeded
        if not chunk:
            continue
        remaining = _MAX_BODY_BYTES + 1 - len(body)
        body.extend(chunk[:remaining])
        if len(body) > _MAX_BODY_BYTES:
            break
    if time.monotonic() - started_at > _PROBE_WALL_SECONDS:
        raise _ProbeDeadlineExceeded
    return bytes(body)


def _looks_like_portal(body: bytes) -> bool:
    lowered = body.lower()
    return any(marker in lowered for marker in _PORTAL_MARKERS)


def _safe_redirect_url(base_url: str, location: str | None) -> str | None:
    if not location:
        return None
    try:
        candidate = urlsplit(urljoin(base_url, location))
    except ValueError:
        return None
    if candidate.scheme not in {"http", "https"} or not candidate.hostname:
        return None
    # Credentials and fragments are never useful for opening a captive portal.
    host = candidate.hostname
    if ":" in host:
        host = f"[{host}]"
    try:
        port = candidate.port
    except ValueError:
        return None
    if port is not None:
        host = f"{host}:{port}"
    return urlunsplit((candidate.scheme, host, candidate.path, candidate.query, ""))


def _default_interface_check() -> bool:
    if os.name == "nt":
        try:
            windows_result = _windows_has_active_interface()
            if windows_result is not None:
                return windows_result
        except (AttributeError, OSError, ValueError):
            pass
    return _stdlib_has_non_loopback_address()


def _stdlib_has_non_loopback_address() -> bool:
    try:
        addresses = socket.getaddrinfo(socket.gethostname(), None)
    except OSError:
        return False
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address[4][0].split("%", 1)[0])
        except ValueError:
            continue
        if not (ip.is_loopback or ip.is_unspecified or ip.is_multicast):
            return True
    return False


def _windows_has_active_interface() -> bool | None:
    """Return None when the Windows adapter API itself is unavailable."""

    from ctypes import wintypes

    class SocketAddress(ctypes.Structure):
        _fields_ = [
            ("sockaddr", ctypes.c_void_p),
            ("length", ctypes.c_int),
        ]

    class UnicastAddress(ctypes.Structure):
        pass

    UnicastAddress._fields_ = [
        ("length", wintypes.ULONG),
        ("flags", wintypes.DWORD),
        ("next", ctypes.POINTER(UnicastAddress)),
        ("address", SocketAddress),
    ]

    class AdapterAddress(ctypes.Structure):
        pass

    AdapterAddress._fields_ = [
        ("length", wintypes.ULONG),
        ("if_index", wintypes.DWORD),
        ("next", ctypes.POINTER(AdapterAddress)),
        ("adapter_name", ctypes.c_char_p),
        ("first_unicast", ctypes.POINTER(UnicastAddress)),
        ("first_anycast", ctypes.c_void_p),
        ("first_multicast", ctypes.c_void_p),
        ("first_dns_server", ctypes.c_void_p),
        ("dns_suffix", ctypes.c_wchar_p),
        ("description", ctypes.c_wchar_p),
        ("friendly_name", ctypes.c_wchar_p),
        ("physical_address", ctypes.c_ubyte * 8),
        ("physical_address_length", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("mtu", wintypes.DWORD),
        ("if_type", wintypes.DWORD),
        ("oper_status", ctypes.c_int),
    ]

    get_adapters = ctypes.windll.iphlpapi.GetAdaptersAddresses
    get_adapters.argtypes = [
        wintypes.ULONG,
        wintypes.ULONG,
        ctypes.c_void_p,
        ctypes.POINTER(AdapterAddress),
        ctypes.POINTER(wintypes.ULONG),
    ]
    get_adapters.restype = wintypes.ULONG

    size = wintypes.ULONG(15_000)
    for _ in range(2):
        buffer = ctypes.create_string_buffer(size.value)
        first = ctypes.cast(buffer, ctypes.POINTER(AdapterAddress))
        result = get_adapters(0, 0x0E, None, first, ctypes.byref(size))
        if result == 111 and size.value <= 1_048_576:
            continue
        if result != 0:
            return None
        break
    else:
        return None

    adapter = first
    for _ in range(256):
        if not adapter:
            break
        current = adapter.contents
        if current.oper_status == 1 and current.if_type != 24:
            unicast = current.first_unicast
            for _ in range(256):
                if not unicast:
                    break
                sock_addr = unicast.contents.address
                ip = _ip_from_sockaddr(sock_addr.sockaddr, sock_addr.length)
                if ip is not None and not (
                    ip.is_loopback or ip.is_unspecified or ip.is_multicast
                ):
                    return True
                unicast = unicast.contents.next
        adapter = current.next
    return False


def _ip_from_sockaddr(pointer: int | None, length: int) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    if not pointer or length < 8:
        return None
    raw = ctypes.string_at(pointer, min(length, 28))
    family = int.from_bytes(raw[:2], "little")
    try:
        if family == socket.AF_INET and len(raw) >= 8:
            return ipaddress.ip_address(raw[4:8])
        if family == socket.AF_INET6 and len(raw) >= 24:
            return ipaddress.ip_address(raw[8:24])
    except ValueError:
        return None
    return None

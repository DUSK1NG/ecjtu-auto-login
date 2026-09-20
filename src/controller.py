"""可逐步执行及注入依赖的认证状态机。"""
from enum import Enum
import logging
import time
from threading import Event
import requests
from .config import Config
from .network import NetworkChecker, NetworkResult, NetworkStatus
from .portal import AuthFailure, PortalAdapter


class State(str, Enum):
    NO_NETWORK = "NO_NETWORK"
    INTERNET_OK = "INTERNET_OK"
    PORTAL_REQUIRED = "PORTAL_REQUIRED"
    AUTHENTICATING = "AUTHENTICATING"
    AUTH_SUCCESS = "AUTH_SUCCESS"
    AUTH_FAILED = "AUTH_FAILED"
    LAN_ONLY = "LAN_ONLY"


FAILURE_MESSAGES = {
    AuthFailure.INVALID_CREDENTIALS: "认证失败：账号或密码错误，或账号状态受限；请检查配置",
    AuthFailure.TIMEOUT: "认证失败：请求超时，请检查网络或稍后重试",
    AuthFailure.SERVER_UNAVAILABLE: "认证失败：校园认证服务器不可用，请稍后重试",
    AuthFailure.REJECTED: "认证失败：服务器拒绝请求，请检查账号状态及学校协议",
    AuthFailure.NOT_CONFIGURED: "认证失败：学校 Adapter 尚未配置（TODO）",
}


class LoginController:
    RETRY_DELAYS = (2, 3, 5, 10)
    OFFLINE_INTERVAL = 1.0

    def __init__(self, config: Config, checker: NetworkChecker, adapter: PortalAdapter,
                 logger: logging.Logger, clock=time.monotonic):
        self.config, self.checker, self.adapter = config, checker, adapter
        self.logger, self.clock = logger, clock
        self.state: State | None = None
        self.failures = 0
        self.next_auth_at = 0.0

    def _transition(self, state: State, message: str):
        if self.state != state:
            self.logger.info("%s | %s", state.value, message)
        self.state = state

    def _failed(self, message: str) -> float:
        delay = self.RETRY_DELAYS[min(self.failures, len(self.RETRY_DELAYS) - 1)]
        self.failures += 1
        self.next_auth_at = self.clock() + delay
        self._transition(State.AUTH_FAILED, message)
        self.logger.warning("%s；%.0f 秒后允许重试", message, delay)
        return min(delay, self.config.check_interval)

    def _online(self):
        self.failures, self.next_auth_at = 0, 0.0
        self._transition(State.INTERNET_OK, "Internet connection verified")

    def step(self) -> float:
        """进行一次检测，返回下次检测等待秒数；调用者不应并发调用。"""
        network = self.checker.check()
        if network.status == NetworkStatus.INTERNET_OK:
            self._online()
            return self.config.check_interval
        if network.status == NetworkStatus.NO_NETWORK:
            self._transition(State.NO_NETWORK, "未检测到可用网络接口，等待连接")
            return self.OFFLINE_INTERVAL
        remaining = self.next_auth_at - self.clock()
        if remaining > 0:
            return min(remaining, self.config.check_interval)
        self.logger.info("Internet unavailable")
        try:
            matched = self.adapter.matches(network)
        except requests.Timeout:
            matched = False
            self.logger.warning("学校环境识别超时，未提交凭据")
        except requests.RequestException:
            matched = False
            self.logger.warning("学校环境识别请求失败，未提交凭据")
        if not matched:
            if network.status == NetworkStatus.PORTAL_REQUIRED:
                self._transition(State.PORTAL_REQUIRED, "疑似 Portal；学校 Adapter 未确认环境，不提交凭据")
            else:
                self._transition(State.LAN_ONLY, "已连接局域网，无 Internet；尚未确认学校认证环境")
            return self.OFFLINE_INTERVAL
        self._transition(State.PORTAL_REQUIRED, "Campus portal detected")
        if not self.config.username or not self.config.password:
            return self._failed("缺少 CAMPUS_USERNAME 或 CAMPUS_PASSWORD，请配置 .env 后重启")
        self._transition(State.AUTHENTICATING, "Authenticating")
        try:
            result = self.adapter.authenticate(self.config.username, self.config.password)
        except requests.Timeout:
            return self._failed(FAILURE_MESSAGES[AuthFailure.TIMEOUT])
        except requests.ConnectionError:
            return self._failed(FAILURE_MESSAGES[AuthFailure.SERVER_UNAVAILABLE])
        except requests.RequestException:
            return self._failed("认证请求失败：请检查接口配置、TLS 或服务器状态")
        if not result.success and not result.verification_required:
            return self._failed(FAILURE_MESSAGES.get(result.failure, "认证失败：未识别响应，请检查 Adapter"))
        if result.verification_required:
            self.logger.info("已收到校园网认证响应，结果待 Internet 验证")
        else:
            self._transition(State.AUTH_SUCCESS, "认证接口报告成功，正在验证 Internet")
        verified = self.checker.check(allow_fast_portal=False)
        if verified.status == NetworkStatus.INTERNET_OK:
            if result.verification_required:
                self._transition(State.AUTH_SUCCESS, "Authentication successful；Internet 验证通过")
            self._online()
            return self.config.check_interval
        delay = self._failed("认证后 Internet 验证失败；可能是凭据或账号状态异常、掉线或认证尚未生效")
        if verified.status == NetworkStatus.NO_NETWORK:
            self._transition(State.NO_NETWORK, "验证期间网络断开")
        return delay

    def run(self, stop: Event | None = None):
        stop = stop or Event()
        while not stop.is_set():
            stop.wait(self.step())

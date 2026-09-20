"""入口：python -m src.main [--once]。"""
import argparse
from .config import Config, app_directory
from .controller import LoginController
from .logger import setup_logger
from .network import NetworkChecker
from .portal import UnconfiguredPortalAdapter
from .ecjtu import EcjtuPortalAdapter


def main() -> int:
    parser = argparse.ArgumentParser(description="校园网自动认证工具")
    parser.add_argument("--once", action="store_true", help="只检测一次后退出")
    parser.add_argument("--check-only", action="store_true", help="强制只检测网络，绝不认证；可与 --once 联用")
    args = parser.parse_args()
    try:
        config = Config.load()
    except ValueError as error:
        parser.error(str(error))
    logger = setup_logger(app_directory() / "logs", (config.username, config.password))
    if config.adapter == "ecjtu" and not args.check_only:
        adapter = EcjtuPortalAdapter(config.isp_suffix)
        logger.info("程序启动；已启用华东交通大学 Adapter")
    else:
        adapter = UnconfiguredPortalAdapter()
        logger.info("程序启动；仅检测网络，不提交凭据")
    validator = adapter.recognizes_redirect if config.adapter == "ecjtu" and not args.check_only else None
    controller = LoginController(config, NetworkChecker(portal_validator=validator), adapter, logger)
    try:
        if args.once:
            controller.step()
        else:
            controller.run()
    except KeyboardInterrupt:
        logger.info("程序已停止")
    except Exception:
        # 最外层兜底：程序错误停止运行，不无限重试，不泄露异常负载。
        logger.error("程序发生未预期错误，已停止；请运行测试并检查配置或 Adapter 实现")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

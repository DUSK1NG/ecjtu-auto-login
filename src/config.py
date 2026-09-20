"""从固定应用目录读取配置，不依赖启动工作目录。"""
from dataclasses import dataclass, field
from pathlib import Path
import math
import os
import sys
from dotenv import load_dotenv


def app_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    check_interval: float = 10.0
    adapter: str = "none"
    isp_suffix: str = "@cmcc"

    @classmethod
    def load(cls, directory: Path | None = None) -> "Config":
        load_dotenv((directory or app_directory()) / ".env", override=False)
        try:
            interval = float(os.getenv("CHECK_INTERVAL", "10"))
            if not math.isfinite(interval) or not 5 <= interval <= 3600:
                raise ValueError
        except ValueError:
            raise ValueError("CHECK_INTERVAL 必须为 5 到 3600 秒之间的有限数值") from None
        adapter = os.getenv("CAMPUS_ADAPTER", "none").strip().lower()
        if adapter not in {"none", "ecjtu"}:
            raise ValueError("CAMPUS_ADAPTER 必须为 none 或 ecjtu")
        suffix = os.getenv("CAMPUS_ISP_SUFFIX", "@cmcc").strip()
        if suffix not in {"", "@cmcc", "@telecom", "@unicom"}:
            raise ValueError("CAMPUS_ISP_SUFFIX 仅支持 @cmcc、@telecom、@unicom 或空值")
        return cls(os.getenv("CAMPUS_USERNAME", ""), os.getenv("CAMPUS_PASSWORD", ""), interval, adapter, suffix)

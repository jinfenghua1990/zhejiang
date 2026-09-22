"""结构化日志。

约定：
- 全局唯一 stdout handler，时间戳按 ISO8601 UTC；
- Uvicorn 复用 root handler；SQL 参数在生产环境只记录 warning；
- 业务代码 `from app.core.logging import get_logger` 后用 `log.info(...)` 等；
- 故意静默吞错的健康检查代码用 warning 级别记录，不再裸 except 后 pass。
"""
from __future__ import annotations

import logging
import sys


_CONFIGURED = False
_FORMAT = "%(asctime)sZ %(levelname)-7s %(name)s :: %(message)s"


def configure_logging(level: str = "INFO") -> None:
    """幂等：可重复调用，最终状态一致。"""
    global _CONFIGURED
    root = logging.getLogger()
    if not _CONFIGURED:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt="%Y-%m-%dT%H:%M:%S"))
        root.addHandler(handler)
        _CONFIGURED = True
    root.setLevel(level.upper())
    # uvicorn 默认 propagates=True，复用 root handler。SQL 参数可能含业务/身份数据，
    # 线上仅保留 warning，避免每个查询和绑定值写入容器日志。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).setLevel(level.upper())
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """业务用 logger 工厂，自动触发配置。"""
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(name)

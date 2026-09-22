from __future__ import annotations

import os
import platform
import shutil
import subprocess
from typing import Any

_LABELS = {
    "local": "com.gino.ecommerce-dashboard.backup",
    "restore": "com.gino.ecommerce-dashboard.restore-check",
    "r2": "com.gino.ecommerce-dashboard.backup-r2",
    "kodo": "com.gino.ecommerce-dashboard.cold-backup-kodo",
    "webdav": "com.gino.ecommerce-dashboard.backup-webdav",
}


def get_schedule_status(target: str) -> dict[str, Any]:
    """返回应用当前能确认的调度状态。

    macOS 原生运行时检查 launchd；Docker / NAS / Linux 下不伪造“已定时”，
    明确要求由宿主机计划任务、cron 或编排平台负责触发。
    """
    label = _LABELS.get(target, "")
    system = platform.system().lower()
    if system != "darwin":
        return {
            "mode": "external",
            "managed": False,
            "label": label,
            "message": "当前运行环境不是 macOS launchd；请由 NAS / 系统计划任务负责自动触发",
        }

    launchctl = shutil.which("launchctl")
    if not launchctl or not label:
        return {
            "mode": "launchd",
            "managed": False,
            "label": label,
            "message": "未检测到可用的 launchd 自动备份计划",
        }

    try:
        result = subprocess.run(
            [launchctl, "print", f"gui/{os.getuid()}/{label}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
        managed = result.returncode == 0
    except Exception:
        managed = False
    return {
        "mode": "launchd",
        "managed": managed,
        "label": label,
        "message": "launchd 计划已加载" if managed else "launchd 计划未加载；请执行备份计划安装/刷新",
    }

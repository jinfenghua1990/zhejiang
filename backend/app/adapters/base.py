class AdapterNotConfigured(Exception):
    """外部系统凭证未提供。UI 必须显示未配置状态，禁止伪装已连接。"""


class AdapterError(Exception):
    """外部调用真实失败。"""


class AdapterPermissionError(AdapterError):
    """外部系统传输可达，但当前应用没有调用业务接口的权限。"""

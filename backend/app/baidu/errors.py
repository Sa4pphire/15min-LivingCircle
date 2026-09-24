class BaiduApiError(RuntimeError):
    """百度 API 返回了业务错误或参数错误。"""


class BaiduAuthError(BaiduApiError):
    """AK 无效、未授权或没有对应服务权限。"""


class BaiduQuotaError(BaiduApiError):
    """百度 API 配额已耗尽。"""


class BaiduTransientError(BaiduApiError):
    """网络超时、连接失败或百度服务临时异常。"""
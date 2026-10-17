"""M3 拒绝原因。

每个异常代表一次程序级拒绝，消息只包含判定依据，不包含凭据或敏感内容。
"""


class SecurityDenied(Exception):
    """所有 M3 拒绝的基类。"""


class ScopeDenied(SecurityDenied):
    """作用域不允许申请该能力。"""


class PathDenied(SecurityDenied):
    """路径超出允许范围或形式不可接受。"""


class ApprovalDenied(SecurityDenied):
    """审批票据缺失、失效或与本次动作不匹配。"""


class LeaseDenied(SecurityDenied):
    """写入租约缺失、失效或不属于请求者。"""

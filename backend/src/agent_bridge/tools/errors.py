"""M4 工具执行失败原因。"""


class ToolError(Exception):
    """所有 M4 工具执行失败的基类。"""


class ToolRejected(ToolError):
    """工具执行被拒绝：不存在、未实现或被安全规则拦截。"""

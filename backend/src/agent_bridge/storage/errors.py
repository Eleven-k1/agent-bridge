"""M5 存储层失败原因。"""


class StorageError(Exception):
    """所有 M5 存储失败的基类。"""


class IdempotencyConflict(StorageError):
    """相同幂等键携带了不同的请求摘要。"""


class DedupConflict(StorageError):
    """相同事件去重键携带了不同的候选事件摘要。"""


class SequenceGapDetected(StorageError):
    """作用域事实流出现缺口，不得静默跳过。"""

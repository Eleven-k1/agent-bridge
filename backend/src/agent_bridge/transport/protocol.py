"""NDJSON 线协议：消息类型常量与编解码。

传输形态为回环 TCP + 每行一个 UTF-8 JSON 对象。
M0 只规定公共语义，不规定传输格式；此协议属于 M6 实现细节，
切换正式传输库（如 websockets）时由 transport 层内部替换，不影响上层。
"""

from __future__ import annotations

import json
from typing import Any

PROTOCOL_VERSION = "0.5.1"

# 上行（客户端 -> 服务端）
MSG_AUTH = "auth"
MSG_COMMAND = "command"
MSG_RESUME = "resume"

# 下行（服务端 -> 客户端）
MSG_AUTH_OK = "auth_ok"
MSG_AUTH_FAILED = "auth_failed"
MSG_CONNECTION_STATE = "connection_state"
MSG_COMMAND_RESULT = "command_result"
MSG_EVENT = "event"
MSG_MODEL_FRAME = "model_frame"
MSG_RESUME_DONE = "resume_done"
MSG_PROTOCOL_ERROR = "protocol_error"

#: 单行消息最大长度（字节）；超限视为传输错误并断开
MAX_LINE_BYTES = 16 * 1024 * 1024


class ProtocolMessage:
    """线消息构造工具。"""

    @staticmethod
    def auth(token: str) -> dict[str, Any]:
        return {"type": MSG_AUTH, "token": token}

    @staticmethod
    def command(command: dict[str, Any]) -> dict[str, Any]:
        return {"type": MSG_COMMAND, "command": command}

    @staticmethod
    def resume(scope_type: str, scope_id: str, last_event_seq: int) -> dict[str, Any]:
        return {
            "type": MSG_RESUME,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "last_event_seq": last_event_seq,
        }


def encode_message(message: dict[str, Any]) -> bytes:
    """编码一行 NDJSON（含换行符）。"""
    return (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")


def decode_line(line: bytes) -> dict[str, Any]:
    """解码一行 JSON；空行或非法 JSON 抛 ValueError。"""
    if not line:
        raise ValueError("empty line")
    return json.loads(line.decode("utf-8"))

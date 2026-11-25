# -*- coding: utf-8 -*-
"""极简 HTTP JSON 客户端（只用标准库 urllib）。

不引入 requests：客户端要打进 exe，能少一个依赖就少一个。

所有失败都收敛成 ActivateResult，不向上抛网络异常 —— 因为调用方是启动流程，
绝不能因为"没网"而崩在堆栈上（README 硬性要求 #3）。
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

__all__ = ["ActivateResult", "post_json"]

_USER_AGENT = "ZTi-Activation/1.0"


class ActivateResult:
    """一次 HTTP 调用的结果。"""

    __slots__ = ("ok", "data", "message", "kind")

    def __init__(self, ok: bool, data: dict | None = None,
                 message: str = "", kind: str = ""):
        self.ok = ok
        self.data = data or {}
        self.message = message
        # kind: ok / network / server / bad_response
        self.kind = kind or ("ok" if ok else "server")

    def get(self, key, default=None):
        return self.data.get(key, default)

    def __repr__(self) -> str:  # pragma: no cover
        return "ActivateResult(ok=%r, kind=%r, message=%r)" % (
            self.ok, self.kind, self.message)


def post_json(url: str, payload: dict, timeout: float = 8.0) -> ActivateResult:
    """POST 一个 JSON 并解析 JSON 响应。

    明确区分三类失败，好让界面给出不同的下一步建议：
      - network       连不上/超时  → "检查网络，或改用离线授权"
      - server        服务端返回错误 → 直接显示服务端给的中文 message
      - bad_response  回的不是 JSON → "服务端异常，稍后再试"
    """
    from . import _console
    _console.debug_log("post_json -> %s (timeout=%s)" % (url, timeout))
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        })

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
        _console.debug_log("post_json <- %d bytes" % len(raw))
    except urllib.error.HTTPError as exc:
        # 服务端用 4xx 返回结构化错误，尽量把它的中文提示掏出来
        detail = ""
        try:
            raw = exc.read()
            parsed = json.loads(raw.decode("utf-8"))
            if isinstance(parsed, dict):
                detail = str(parsed.get("message") or "")
                _console.debug_log("post_json HTTPError %s: %s" % (exc.code, detail))
                return ActivateResult(False, parsed,
                                      detail or "服务端返回错误 %s" % exc.code,
                                      "server")
        except Exception:
            pass
        _console.debug_log("post_json HTTPError %s" % exc.code)
        return ActivateResult(False, None,
                              detail or "服务端返回错误 %s" % exc.code, "server")
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        _console.debug_log("post_json URLError: %r" % (reason,))
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return ActivateResult(False, None, "连接超时", "network")
        return ActivateResult(False, None, "无法连接激活服务器：%s" % reason, "network")
    except (socket.timeout, TimeoutError):
        _console.debug_log("post_json timeout")
        return ActivateResult(False, None, "连接超时", "network")
    except OSError as exc:
        _console.debug_log("post_json OSError: %r" % (exc,))
        return ActivateResult(False, None, "网络错误：%s" % exc, "network")

    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ActivateResult(False, None, "激活服务器返回的内容无法解析", "bad_response")

    if not isinstance(data, dict):
        return ActivateResult(False, None, "激活服务器返回的内容格式不对", "bad_response")

    ok = bool(data.get("ok"))
    message = str(data.get("message") or "")
    return ActivateResult(ok, data, message, "ok" if ok else "server")

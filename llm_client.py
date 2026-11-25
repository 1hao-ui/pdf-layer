# -*- coding: utf-8 -*-
"""llm_client.py — 「自动做题」的大模型接入（OpenAI 兼容 Chat Completions）。

做题模块本身不内置模型：它负责「划题区 + 出做题提示词 + 把答案写成手写」，
真正答题的 AI 有两种接法：
  1. **外部 AI 调用本服务的接口**（POST /worksheet/analyze → prompt → answer）；
  2. **本模块直接调模型**：配置好 OpenAI 兼容端点后，POST /worksheet/solve
     或 `py -3 做题.py solve` 就能一步完成「划题 → AI 作答 → 手写写回 PDF」。

配置优先级：环境变量 > 配置文件（pdf-layer/llm.json 或 ~/.handwrite_llm.json）> 默认值
--------------------------------------------------------------------
    HANDWRITE_LLM_BASE_URL   默认 https://api.deepseek.com/v1
    HANDWRITE_LLM_API_KEY    必填（也兼容 OPENAI_API_KEY / DEEPSEEK_API_KEY）
    HANDWRITE_LLM_MODEL      默认 deepseek-chat
    HANDWRITE_LLM_TIMEOUT    默认 300 秒（题多/难题时模型可能要写很久；
                             图形界面「AI 设置」里也能改，存进 llm.json）
    HANDWRITE_LLM_VISION     1/0，是否把扫描件题干截图作为图片发给模型
                             （默认 1：视觉模型效果最好；纯文本模型可设 0）
    HANDWRITE_LLM_MAX_TOKENS 默认 8192（题多时 2048 会把答案截断；
                             图形界面「AI 设置」里也能改）

配置文件 llm.json 示例（放在 pdf-layer 下，别提交到公开仓库）：
    {"base_url": "https://api.deepseek.com/v1",
     "api_key": "sk-...", "model": "deepseek-chat"}

任何 OpenAI 兼容服务都行：DeepSeek、OpenAI、通义/Kimi/GLM 兼容接口、
本地 Ollama（http://127.0.0.1:11434/v1，无需 api_key）等。
"""
from __future__ import annotations

import base64
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TIMEOUT = 300.0          # 题多/难题时模型可能要写几分钟，别过早掐断
DEFAULT_MAX_TOKENS = 8192        # 2048 在题量多时会截断 JSON

# 自有 User-Agent：不少网关（Cloudflare 前置）会直接 403 掉 urllib 默认的
# "Python-urllib/x.y"（OpenCode 返回 error code: 1010）。
DEFAULT_UA = "ZTi-Handwriting/1.2"

# OpenCode Go 网关要求每个会话带稳定 ID，否则 400 MissingSessionID。
_OPENCODE_HOSTS = ("opencode.ai", "www.opencode.ai")
_SESSION_ID = "zti-" + uuid.uuid4().hex

HERE = os.path.dirname(os.path.abspath(__file__))
# 打包版优先读 exe 同目录的 llm.json（用户看得见、可改）；源码版读 pdf-layer/llm.json
CONFIG_FILES = tuple(
    ([os.path.join(os.path.dirname(sys.executable), "llm.json")]
     if getattr(sys, "frozen", False) else [])
    + [os.path.join(HERE, "llm.json"),
       os.path.expanduser("~/.handwrite_llm.json")]
)


class LlmError(RuntimeError):
    """大模型调用失败（带建议的 HTTP 状态/提示，便于 API 层返回错误）。"""

    def __init__(self, message, code=502, hint=None):
        super().__init__(message)
        self.code = int(code)
        self.hint = hint


def _file_cfg():
    """读 llm.json（环境变量优先；文件便于把 key 存一次、不用每次设）。"""
    for path in CONFIG_FILES:
        try:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    obj = json.load(f)
                if isinstance(obj, dict):
                    return obj
        except Exception:
            continue
    return {}


def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


def _number(name, file_cfg, key, default, cast):
    """读数值配置：环境变量 > llm.json 字段 > 默认值（非法值回退默认）。"""
    raw = _env(name)
    if not raw:
        raw = file_cfg.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return default


def normalize_base_url(url):
    """把用户可能粘贴的「完整端点」收敛成 base_url。

    程序内部固定拼接 ``/chat/completions``，所以 base_url 只能填到 ``/v1``。
    用户从各家文档复制时经常连端点一起粘进来：

        https://api.deepseek.com/v1/chat/completions  ->  .../v1
        https://opencode.ai/inference/go/v1/chat/completions  ->  .../go/v1

    不收敛的话会拼成 ``/chat/completions/chat/completions``（404）。
    """
    u = (url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if u.endswith(suffix):
            u = u[: -len(suffix)].rstrip("/")
            break
    return u


def save_config(base_url, api_key, model, path=None,
                timeout=None, max_tokens=None):
    """把 AI 配置写到 llm.json（打包版写在 exe 同目录），返回路径。

    timeout / max_tokens 只在显式传入时写入；不传则保留目标文件里原值
    （这样老版本界面保存时不会把用户手改的等待上限抹掉）。
    """
    if path is None:
        for cand in CONFIG_FILES:
            d = os.path.dirname(cand)
            if d and os.path.isdir(d) and os.access(d, os.W_OK):
                path = cand
                break
        path = path or CONFIG_FILES[0]
    old = {}
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                obj = json.load(f)
            if isinstance(obj, dict):
                old = obj
    except Exception:
        old = {}
    if not old:
        old = _file_cfg()
    data = {"base_url": normalize_base_url(base_url),
            "api_key": (api_key or "").strip(),
            "model": (model or "").strip()}
    if timeout is None and old.get("timeout") is not None:
        data["timeout"] = old.get("timeout")
    if max_tokens is None and old.get("max_tokens") is not None:
        data["max_tokens"] = old.get("max_tokens")
    if timeout is not None:
        try:
            data["timeout"] = round(float(timeout), 1)
        except (TypeError, ValueError):
            pass
    if max_tokens is not None:
        try:
            data["max_tokens"] = int(max_tokens)
        except (TypeError, ValueError):
            pass
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def _is_local(base_url):
    host = urllib.parse.urlsplit(base_url).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1", "0.0.0.0")


def config():
    """读取配置：环境变量 > llm.json > 默认值。"""
    fc = _file_cfg()
    base = normalize_base_url(
        _env("HANDWRITE_LLM_BASE_URL") or _env("OPENAI_BASE_URL")
        or str(fc.get("base_url") or "") or DEFAULT_BASE_URL)
    key = (_env("HANDWRITE_LLM_API_KEY") or _env("OPENAI_API_KEY")
           or _env("DEEPSEEK_API_KEY") or str(fc.get("api_key") or ""))
    model = (_env("HANDWRITE_LLM_MODEL") or _env("OPENAI_MODEL")
             or str(fc.get("model") or "") or DEFAULT_MODEL)
    timeout = _number("HANDWRITE_LLM_TIMEOUT", fc, "timeout",
                      DEFAULT_TIMEOUT, float)
    max_tokens = _number("HANDWRITE_LLM_MAX_TOKENS", fc, "max_tokens",
                         DEFAULT_MAX_TOKENS, int)
    return {
        "base_url": base.rstrip("/"),
        "api_key": key,
        "model": model,
        "timeout": max(5.0, min(1800.0, timeout)),
        "max_tokens": max(256, min(32768, max_tokens)),
        "vision": _env("HANDWRITE_LLM_VISION", "1") not in
                  ("0", "false", "False", "no"),
    }


def available():
    c = config()
    return bool(c["base_url"] and (c["api_key"] or _is_local(c["base_url"])))


def public_config():
    """给 /capabilities、日志用的脱敏配置。"""
    c = config()
    key = c["api_key"]
    masked = (key[:4] + "…" + key[-4:]) if len(key) > 8 else ("***" if key else "")
    return {"base_url": c["base_url"], "model": c["model"],
            "configured": bool(c["base_url"]
                               and (key or _is_local(c["base_url"]))),
            "api_key": masked, "local": _is_local(c["base_url"]),
            "vision": c["vision"],
            "timeout": c["timeout"], "max_tokens": c["max_tokens"],
            "env": ["HANDWRITE_LLM_BASE_URL", "HANDWRITE_LLM_API_KEY",
                    "HANDWRITE_LLM_MODEL", "HANDWRITE_LLM_VISION",
                    "HANDWRITE_LLM_TIMEOUT", "HANDWRITE_LLM_MAX_TOKENS"],
            "config_files": list(CONFIG_FILES)}


# ---------------------------------------------------------------- HTTP
def _headers(api_key, base_url):
    """请求头：自有 UA + 鉴权；OpenCode 网关另需会话 ID。"""
    h = {"Content-Type": "application/json", "User-Agent": DEFAULT_UA}
    if api_key:
        h["Authorization"] = "Bearer " + api_key
    host = urllib.parse.urlsplit(base_url).hostname or ""
    if host in _OPENCODE_HOSTS:
        h["x-opencode-session"] = _SESSION_ID
    return h


def _error_hint(code, body):
    """把常见的接口错误翻译成可操作的建议。"""
    low = (body or "").lower()
    if code == 403 and "1010" in body:
        return ("接口前置的 Cloudflare 按客户端特征拦截了请求"
                "（error code: 1010），不是 Key 或模型名的问题。"
                "需要程序发送正常的 User-Agent（v1.2 之后的版本已内置）")
    if "missingsessionid" in low or "x-opencode-session" in low:
        return ("该网关要求 x-opencode-session 会话头。"
                "OpenCode Go 接口需用内置会话 ID 的客户端调用")
    if code == 404:
        return ("404 多为 BASE_URL 重复拼了 /chat/completions："
                "只填到 /v1 即可，程序会自动补 /chat/completions")
    if code in (401, 403):
        return "Key 无效或没有该模型的权限，检查 API Key"
    if code == 429:
        return "触发限流：稍后重试，或减少一次作答的题量"
    return "检查 HANDWRITE_LLM_BASE_URL / MODEL / API_KEY"


def _post_json(url, payload, headers, timeout):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        raise LlmError("模型接口返回 HTTP %s：%s" % (e.code, body),
                       code=502 if e.code >= 500 else 400,
                       hint=_error_hint(e.code, body))
    except (socket.timeout, TimeoutError):
        raise LlmError("模型调用超时（%.0f 秒）" % timeout, code=504,
                       hint="在「AI 设置」里调大等待上限（或环境变量 "
                            "HANDWRITE_LLM_TIMEOUT），也可以减少一次作答的题量")
    except urllib.error.URLError as e:
        raise LlmError("连不上模型接口：%s" % e.reason, code=502,
                       hint="检查网络 / BASE_URL / 代理设置")


def _crops_from_spec(spec, limit=8):
    """从 spec 里取扫描件题干的裁剪图（data URI），最多 limit 张。"""
    out = []
    for pg in (spec or {}).get("pages") or []:
        for q in pg.get("questions") or []:
            b64 = q.get("crop_png_base64")
            if b64:
                out.append("data:image/png;base64," + b64)
                if len(out) >= limit:
                    return out
    return out


def _messages(prompt, spec, vision):
    """组装 chat messages：system 提示词 + 用户题目文本（+ 扫描件截图）。

    没有截图时用纯字符串 content：纯文本模型（如 deepseek-chat）对
    ``[{type:text}]`` 这种数组形式并不都兼容，字符串最稳。
    """
    sys = ("你是严谨的工科答题助手。严格按用户给的格式与约束作答，"
           "只输出一个 JSON 对象。")
    text = prompt + "\n\n请按上面的格式直接输出 JSON。"
    crops = _crops_from_spec(spec) if vision else []
    if crops:
        content = [{"type": "text", "text": text}]
        for uri in crops:
            content.append({"type": "image_url", "image_url": {"url": uri}})
    else:
        content = text
    return [{"role": "system", "content": sys},
            {"role": "user", "content": content}]


def _extract_answers(text):
    """从模型回复里解析 {"answers": {...}}（容忍代码块/前后废话）。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        obj = json.loads(raw)
    except Exception:
        i, j = raw.find("{"), raw.rfind("}")
        if i < 0 or j <= i:
            raise LlmError("模型没有返回 JSON：%r" % raw[:200], code=502,
                           hint="换更强/更听话的模型，或检查提示词是否被截断")
        try:
            obj = json.loads(raw[i:j + 1])
        except Exception as e:
            raise LlmError("模型返回的 JSON 解析失败：%s" % e, code=502)
    answers = obj.get("answers") if isinstance(obj, dict) else None
    if not isinstance(answers, dict) or not answers:
        raise LlmError("模型 JSON 里缺少 answers 对象：%r" % str(obj)[:300],
                       code=502)
    return {str(k): v for k, v in answers.items() if isinstance(v, str)}


def solve_worksheet(spec, prompt, vision=None, model=None):
    """调模型给整份作业出答案，返回 {"answers": {...}, "meta": {...}}。"""
    c = config()
    if not c["api_key"] and not _is_local(c["base_url"]):
        raise LlmError("没有配置模型 API Key（HANDWRITE_LLM_API_KEY / "
                       "OPENAI_API_KEY / DEEPSEEK_API_KEY 任一，或 "
                       "pdf-layer/llm.json）", code=400,
                       hint="例如：$env:HANDWRITE_LLM_API_KEY=\"sk-...\"；"
                            "或 base_url 指向本地 Ollama（无需 key）")
    use_vision = c["vision"] if vision is None else bool(vision)
    use_model = model or c["model"]
    url = c["base_url"] + "/chat/completions"
    payload = {
        "model": use_model,
        "messages": _messages(prompt, spec, use_vision),
        "temperature": 0.2,
        "max_tokens": c["max_tokens"],
        "response_format": {"type": "json_object"},
    }
    headers = _headers(c["api_key"], c["base_url"])
    t0 = time.perf_counter()
    try:
        resp = _post_json(url, payload, headers, c["timeout"])
    except LlmError as e:
        msg = str(e).lower()
        if "response_format" in msg or "json_object" in msg:
            # 部分兼容服务不支持 response_format，去掉重试一次
            payload.pop("response_format", None)
            resp = _post_json(url, payload, headers, c["timeout"])
        elif "max_tokens" in msg and c["max_tokens"] > 1024:
            # 个别模型上限更低：对半降一次再试，避免直接失败
            payload["max_tokens"] = max(1024, c["max_tokens"] // 2)
            resp = _post_json(url, payload, headers, c["timeout"])
        else:
            raise
    try:
        choice = resp["choices"][0]
        text = choice["message"]["content"]
    except Exception:
        raise LlmError("模型响应结构异常：%s" % str(resp)[:300], code=502)
    finish = str(choice.get("finish_reason") or "")
    if finish == "length":
        raise LlmError(
            "模型输出被 max_tokens=%d 截断（题多/答案长时常见），"
            "JSON 不完整无法写回" % int(payload.get("max_tokens")
                                         or c["max_tokens"]),
            code=502,
            hint="把「AI 设置」里的最大输出 tokens 调大（或环境变量 "
                 "HANDWRITE_LLM_MAX_TOKENS），也可以先用 --page 分页、"
                 "减少一次作答的题量")
    answers = _extract_answers(text)
    usage = resp.get("usage") or {}
    return {
        "answers": answers,
        "meta": {
            "model": use_model,
            "base_url": c["base_url"],
            "elapsed_ms": int(round((time.perf_counter() - t0) * 1000)),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "vision": use_vision,
            "raw_chars": len(text or ""),
            "finish_reason": finish,
            "timeout_s": c["timeout"],
            "max_tokens": int(payload.get("max_tokens") or c["max_tokens"]),
        },
    }


if __name__ == "__main__":
    cfg = public_config()
    print(json.dumps(cfg, ensure_ascii=False, indent=2))
    if not available():
        print("未配置：设置 HANDWRITE_LLM_API_KEY（可选 BASE_URL/MODEL），"
              "或写 pdf-layer/llm.json 后即可自动做题")

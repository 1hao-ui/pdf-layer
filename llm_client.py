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
    HANDWRITE_LLM_TIMEOUT    默认 120 秒
    HANDWRITE_LLM_VISION     1/0，是否把扫描件题干截图作为图片发给模型
                             （默认 1：视觉模型效果最好；纯文本模型可设 0）
    HANDWRITE_LLM_MAX_TOKENS 默认 2048

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
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TIMEOUT = 120.0

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILES = (
    os.path.join(HERE, "llm.json"),                 # 推荐：pdf-layer/llm.json
    os.path.expanduser("~/.handwrite_llm.json"),    # 或用户目录
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


def _is_local(base_url):
    host = urllib.parse.urlsplit(base_url).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1", "0.0.0.0")


def config():
    """读取配置：环境变量 > llm.json > 默认值。"""
    fc = _file_cfg()
    base = (_env("HANDWRITE_LLM_BASE_URL") or _env("OPENAI_BASE_URL")
            or str(fc.get("base_url") or "") or DEFAULT_BASE_URL)
    key = (_env("HANDWRITE_LLM_API_KEY") or _env("OPENAI_API_KEY")
           or _env("DEEPSEEK_API_KEY") or str(fc.get("api_key") or ""))
    model = (_env("HANDWRITE_LLM_MODEL") or _env("OPENAI_MODEL")
             or str(fc.get("model") or "") or DEFAULT_MODEL)
    try:
        timeout = float(_env("HANDWRITE_LLM_TIMEOUT") or DEFAULT_TIMEOUT)
    except ValueError:
        timeout = DEFAULT_TIMEOUT
    try:
        max_tokens = int(_env("HANDWRITE_LLM_MAX_TOKENS") or 2048)
    except ValueError:
        max_tokens = 2048
    return {
        "base_url": base.rstrip("/"),
        "api_key": key,
        "model": model,
        "timeout": max(10.0, timeout),
        "max_tokens": max(256, max_tokens),
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
            "env": ["HANDWRITE_LLM_BASE_URL", "HANDWRITE_LLM_API_KEY",
                    "HANDWRITE_LLM_MODEL", "HANDWRITE_LLM_VISION"],
            "config_files": list(CONFIG_FILES)}


# ---------------------------------------------------------------- HTTP
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
                       hint="检查 HANDWRITE_LLM_BASE_URL / MODEL / API_KEY")
    except (socket.timeout, TimeoutError):
        raise LlmError("模型调用超时（%.0f 秒）" % timeout, code=504,
                       hint="可调大 HANDWRITE_LLM_TIMEOUT，或减少题量")
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
    """组装 chat messages：system 提示词 + 用户题目文本（+ 扫描件截图）。"""
    sys = ("你是严谨的工科答题助手。严格按用户给的格式与约束作答，"
           "只输出一个 JSON 对象。")
    text = prompt + "\n\n请按上面的格式直接输出 JSON。"
    content = [{"type": "text", "text": text}]
    if vision:
        for uri in _crops_from_spec(spec):
            content.append({"type": "image_url", "image_url": {"url": uri}})
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
    headers = {"Content-Type": "application/json"}
    if c["api_key"]:
        headers["Authorization"] = "Bearer " + c["api_key"]
    t0 = time.perf_counter()
    try:
        resp = _post_json(url, payload, headers, c["timeout"])
    except LlmError as e:
        # 部分兼容服务不支持 response_format，去掉重试一次
        if "response_format" in str(e).lower() or "json_object" in str(e).lower():
            payload.pop("response_format", None)
            resp = _post_json(url, payload, headers, c["timeout"])
        else:
            raise
    try:
        text = resp["choices"][0]["message"]["content"]
    except Exception:
        raise LlmError("模型响应结构异常：%s" % str(resp)[:300], code=502)
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
        },
    }


if __name__ == "__main__":
    cfg = public_config()
    print(json.dumps(cfg, ensure_ascii=False, indent=2))
    if not available():
        print("未配置：设置 HANDWRITE_LLM_API_KEY（可选 BASE_URL/MODEL），"
              "或写 pdf-layer/llm.json 后即可自动做题")

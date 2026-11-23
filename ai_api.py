# -*- coding: utf-8 -*-
"""ai_api.py — 手写体生成器「本地 AI 调用接口」（本地 HTTP + CLI）。

定位
----
把 page_model / symbols / hwrender 的能力包成一个本地 HTTP 服务，供
「AI 生成答案 → 渲染成手写笔迹 → 落回图片/PDF 背景」这条链路使用。
不需要任何第三方 Web 框架，只用 Python 标准库；默认只监听 127.0.0.1。

启动
----
    py -3 ai_api.py                 # http://127.0.0.1:8321
    py -3 ai_api.py --port 8322
    py -3 ai_api.py --self-test     # 临时端口起服务，跑全部端点自测
    py -3 ai_api.py --example       # 打印 curl / python 调用示例

端点（全部 JSON，UTF-8）
-----------------------
    GET  /health          健康检查 + 字体/字表可用性
    GET  /capabilities    字体/字表（汉字 500、符号 204）、字号、页面、schema、端点表
    POST /render          segments 简写 或 page_model spec → PNG/PDF（base64/路径）
    POST /render_pdf      segments/spec + 源 PDF → 叠加到指定页，保留原内容
    POST /prompt          {question} → 内置系统提示词（含动态字表）
    POST /answer          {question, answer_text} → 自动切段 + 可直接 render 的 spec
    POST /worksheet/analyze  {source, page?} → 作业页自动划题 spec + 精简题目 + crops
    POST /worksheet/answer   {source, spec?/layout?, answers} → 手写作答 PDF（base64）
    POST /worksheet/prompt   {source?/spec?} → 做题提示词（逐题作答、只输出 JSON）
POST /worksheet/solve    {source, answers?} → 自动做题：有答案直接渲染；
                         没答案时配置了模型就调模型作答再渲染，否则返回题目+提示词
GET  /llm/status         → 自动做题模型配置（脱敏）：是否已配置/模型名/端点

安全边界
--------
· 默认只监听 127.0.0.1；没有鉴权，不要暴露到公网/局域网；
· 读文件：只允许项目内文件，或用户显式传入且存在的普通文件；系统目录一律拒绝；
· 写文件：只能写 _refs/pdf-layer/out/，文件名做 sanitize，不能带路径；
· 请求体上限、单次渲染超时、渲染串行化，单次失败返回 JSON 错误、服务不崩。

依赖：Python 3.10+（实测 3.14）、Pillow、PyMuPDF、fontTools（stdlib 之外仅此）。
"""
from __future__ import annotations

import argparse
import base64
import copy
import errno
import io
import json
import math
import os
import re
import socket
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ai_prompt                      # noqa: E402
import hwrender as H                  # noqa: E402
import page_model as pm               # noqa: E402
import symbols                        # noqa: E402
import worksheet as ws                # noqa: E402  「做题」链路：划题/排版/导出
import llm_client                     # noqa: E402  自动做题的模型接入

VERSION = "1.0.0"
TOOL_NAME = "handwrite-render"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8321

OUT_DIR = os.path.join(HERE, "out")
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))

SIZE_MIN_PT = ai_prompt.SIZE_MIN_PT
SIZE_MAX_PT = ai_prompt.SIZE_MAX_PT
DEFAULT_SIZE_PT = ai_prompt.DEFAULT_SIZE_PT
DEFAULT_LINE_PT = ai_prompt.DEFAULT_LINE_PT
LINE_RATIO = ai_prompt.LINE_RATIO
A4_W, A4_H = ai_prompt.PAGE_W_PT, ai_prompt.PAGE_H_PT

MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_SEGMENTS = 80
MAX_ANSWER_SEGMENT_CHARS = 90
RENDER_TIMEOUT_S = float(os.environ.get("HANDWRITE_AI_TIMEOUT", "30") or 30)
LOCK_WAIT_S = float(os.environ.get("HANDWRITE_AI_LOCK_WAIT", "20") or 20)

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
PDF_EXTS = {".pdf"}
TMP_PREFIX = "_tmp_ai_"

_RENDER_LOCK = threading.Lock()
_QUIET = False
_FILENAME_BAD = re.compile(r"[^\w\u4e00-\u9fff.\-]+", re.UNICODE)
_CORS_ORIGIN_RE = re.compile(
    r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$", re.IGNORECASE)


# ======================================================================
# 基础工具
# ======================================================================
class ApiError(Exception):
    """带 HTTP 状态码的接口错误。"""

    def __init__(self, code, message, hint="", **extra):
        super().__init__(message)
        self.code = int(code)
        self.message = str(message)
        self.hint = str(hint or "")
        self.extra = extra

    def payload(self):
        p = {"ok": False, "error": self.message, "code": self.code}
        if self.hint:
            p["hint"] = self.hint
        p.update(self.extra)
        return p


def _utf8_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _ensure_out_dir():
    os.makedirs(OUT_DIR, exist_ok=True)
    return OUT_DIR


def _remove_quiet(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _dedupe(seq):
    seen, out = set(), []
    for s in seq:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _deny_roots():
    roots = []
    if os.name == "nt":
        for key in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
            val = os.environ.get(key)
            if val:
                roots.append(val)
    else:
        roots.extend(["/etc", "/proc", "/sys", "/dev", "/root"])
    return [os.path.normcase(os.path.abspath(p)) for p in roots if p]


_DENY_ROOTS = _deny_roots()


def _is_under(path, root):
    path = os.path.normcase(path)
    root = os.path.normcase(root)
    return path == root or path.startswith(root.rstrip("\\/") + os.sep)


def _candidate_paths(path):
    """把用户传的路径展开成候选绝对路径。

    绝对路径只取自身；相对路径依次尝试「服务进程 cwd → 项目根目录 →
    _refs/pdf-layer」，这样 GUI（cwd=pdf-layer）和脚本（cwd=项目根）都顺手。
    """
    p = os.path.expanduser(os.path.expandvars(str(path).strip()))
    if os.path.isabs(p):
        return [os.path.normpath(os.path.abspath(p))]
    out = []
    for base in (os.getcwd(), PROJECT_ROOT, HERE):
        ap = os.path.normpath(os.path.abspath(os.path.join(base, p)))
        if ap not in out:
            out.append(ap)
    return out


def _resolve_read_file(path, allowed_exts=None, what="文件"):
    """路径白名单：项目内 / 用户显式传入的存在文件；系统目录直接 403。"""
    if not isinstance(path, str) or not path.strip():
        raise ApiError(400, "%s路径为空" % what)
    candidates = _candidate_paths(path)
    for p in candidates:
        for root in _DENY_ROOTS:
            if _is_under(p, root):
                raise ApiError(
                    403, "%s路径不在白名单内（系统目录禁止读取）：%s" % (what, p),
                    hint="只允许读取项目目录内的文件，或用户明确传入的项目外普通文件")
    found = next((p for p in candidates if os.path.exists(p)), None)
    if found is None:
        found = candidates[0] if len(candidates) == 1 else candidates
        raise ApiError(404, "%s不存在：%s" % (what, found),
                       hint="相对路径依次按服务进程 cwd、项目根目录、_refs/pdf-layer 解析")
    if not os.path.isfile(found):
        raise ApiError(400, "%s不是普通文件：%s" % (what, found))
    if allowed_exts:
        ext = os.path.splitext(found)[1].lower()
        if ext not in allowed_exts:
            raise ApiError(400, "%s类型不支持：%s（允许 %s）"
                           % (what, ext or "(无扩展名)",
                              "、".join(sorted(allowed_exts))))
    return found


def _safe_out_path(filename, default_ext):
    """写文件只允许落在 out/：basename + sanitize + 强制扩展名。"""
    _ensure_out_dir()
    ext = default_ext if default_ext.startswith(".") else "." + default_ext
    base = os.path.basename(str(filename or "").replace("\\", "/").strip())
    if base.lower().endswith(ext.lower()):
        stem = base[:-len(ext)]
    else:
        stem = base
    stem = _FILENAME_BAD.sub("_", stem).strip("._- ")
    stem = stem[:80] or ("ai_render_" + time.strftime("%Y%m%d_%H%M%S"))
    path = os.path.join(OUT_DIR, stem + ext)
    if os.path.dirname(os.path.abspath(path)) != os.path.abspath(OUT_DIR):
        raise ApiError(400, "输出文件名非法：%s" % filename)
    return path


def _write_bytes(data, filename, default_ext):
    path = _safe_out_path(filename, default_ext)
    with open(path, "wb") as f:
        f.write(data)
    return path


def _as_float(value, default, name, lo=None, hi=None, warnings=None):
    if value is None:
        return float(default)
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ApiError(400, "%s 必须是数字：%r" % (name, value))
    if not math.isfinite(v):
        raise ApiError(400, "%s 必须是有限数字" % name)
    if lo is not None and v < lo:
        if warnings is not None:
            warnings.append("%s=%.3g 小于下限 %.3g，已按 %.3g 处理" % (name, v, lo, lo))
        v = float(lo)
    if hi is not None and v > hi:
        if warnings is not None:
            warnings.append("%s=%.3g 大于上限 %.3g，已按 %.3g 处理" % (name, v, hi, hi))
        v = float(hi)
    return v


def _as_int(value, default, name, lo=None, hi=None):
    if value is None or value == "":
        return int(default)
    try:
        v = int(float(value))
    except (TypeError, ValueError):
        raise ApiError(400, "%s 必须是整数：%r" % (name, value))
    if lo is not None and v < lo:
        raise ApiError(400, "%s=%d 小于下限 %d" % (name, v, lo))
    if hi is not None and v > hi:
        raise ApiError(400, "%s=%d 大于上限 %d" % (name, v, hi))
    return v


# ======================================================================
# 字表 / 覆盖率
# ======================================================================
def _pass_names(d):
    if not d or not os.path.isdir(d):
        return []
    return sorted(n for n in os.listdir(d)
                  if re.match(r"^pass\d+\.ttf$", n, re.IGNORECASE))


def _coverage(texts):
    """统计文本对自建字库的覆盖情况；han_summary 与 symbols 的输出保持一致。"""
    han, sym = ai_prompt.allowed_charset()
    text = "\n".join(texts)
    han_chars = [c for c in text if "\u4e00" <= c <= "\u9fff"]
    hit = [c for c in han_chars if c in han]
    missing = sorted({c for c in han_chars if c not in han})
    outside = sorted({c for c in text
                      if (not c.isspace())
                      and not ("\u4e00" <= c <= "\u9fff")
                      and c not in sym})
    ratio = round(len(hit) / len(han_chars), 4) if han_chars else 1.0
    warnings = []
    if missing:
        warnings.append("汉字缺 %d 个（不在 ZTi-Han 字表，将用回退字体）：%s"
                        % (len(missing), "".join(missing[:30])))
    if outside:
        warnings.append("字库外字符 %d 个（可能用回退字体/空白渲染）：%s"
                        % (len(outside), "".join(outside[:30])))
    return {
        "han_summary": symbols.han_coverage_summary(text),
        "han_total": len(han_chars),
        "han_covered": len(hit),
        "han_ratio": ratio,
        "han_missing": missing,
        "symbol_missing": outside,
        "out_of_charset": outside,
        "warnings": warnings,
    }


# ======================================================================
# 请求 → page_model spec
# ======================================================================
def _parse_background(raw):
    """解析 background：支持 None / 路径字符串 / {kind,path,page,dpi}。"""
    info = {"kind": "blank", "path": "", "page": 0, "dpi": 200,
            "page_pt": None, "pages": None}
    if raw is None or raw == "":
        return info
    if isinstance(raw, str):
        raw = {"path": raw}
    if not isinstance(raw, dict):
        raise ApiError(400, "background 必须是对象或路径字符串")
    path_raw = raw.get("path")
    ext = os.path.splitext(str(path_raw or ""))[1].lower()
    kind = str(raw.get("kind") or "").strip().lower()
    if not kind:
        kind = "pdf" if ext in PDF_EXTS else ("image" if path_raw else "blank")
    if kind not in ("blank", "image", "pdf"):
        raise ApiError(400, "background.kind 只支持 blank / image / pdf：%r" % kind)
    info["kind"] = kind
    if kind == "blank":
        return info
    if not path_raw:
        raise ApiError(400, "background.kind=%s 时必须提供 path" % kind)
    if kind == "image":
        p = _resolve_read_file(path_raw, IMAGE_EXTS, "背景图片")
        try:
            from PIL import Image
            with Image.open(p) as im:
                im.verify()
        except ApiError:
            raise
        except Exception as e:
            raise ApiError(400, "背景图片无法读取：%s（%s）" % (p, e))
        info["path"] = p
    else:  # pdf
        p = _resolve_read_file(path_raw, PDF_EXTS, "背景 PDF")
        import pymupdf as fitz
        try:
            doc = fitz.open(p)
        except Exception as e:
            raise ApiError(400, "背景 PDF 无法打开：%s（%s）" % (p, e))
        try:
            pages = doc.page_count
            if pages < 1:
                raise ApiError(400, "背景 PDF 没有页面：%s" % p)
            idx = _as_int(raw.get("page"), 0, "background.page", 0, pages - 1)
            r = doc[idx].rect
            info["page_pt"] = [round(float(r.width), 2), round(float(r.height), 2)]
            info["pages"] = pages
        finally:
            doc.close()
        info["path"] = p
        info["page"] = idx
    info["dpi"] = _as_int(raw.get("dpi"), 200, "background.dpi", 72, 600)
    return info


def _estimate_lines(text, width_pt, size_pt):
    """粗略估算一段文字在给定列宽下的行数（用于自动排版，只求不压页）。"""
    if not text:
        return 0
    cap = max(1.0, width_pt / max(1.0, size_pt) * 0.92)

    def em(ch):
        if "\u4e00" <= ch <= "\u9fff":
            return 1.0
        if ch.isspace():
            return 0.3
        if ord(ch) < 128:
            return 0.56
        return 0.85

    total = 0
    for para in re.split(r"\n+", text):
        para = para.strip()
        if not para:
            continue
        units, in_math = 0.0, False
        for ch in para:
            if ch == "$":
                in_math = not in_math
                units += 0.2
                continue
            units += em(ch) * (1.25 if in_math else 1.0)
        total += max(1, int(math.ceil(units / cap)))
    return max(1, total)


def _segments_from_body(body):
    """校验并规范化 segments，返回 (segments, warnings)。"""
    raw = body.get("segments")
    if not isinstance(raw, list) or not raw:
        raise ApiError(400, "segments 必须是非空数组")
    if len(raw) > MAX_SEGMENTS:
        raise ApiError(400, "segments 过多（%d 段 > 上限 %d 段），请拆分或分页"
                       % (len(raw), MAX_SEGMENTS))
    warnings = []
    default_size = _as_float(body.get("size_pt"), DEFAULT_SIZE_PT, "size_pt",
                             SIZE_MIN_PT, SIZE_MAX_PT, warnings)
    default_line = body.get("line_pt")
    segments = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ApiError(400, "segments[%d] 必须是对象" % i)
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ApiError(400, "segments[%d].text 必须是非空字符串" % i)
        size = _as_float(item.get("size_pt"), default_size,
                         "segments[%d].size_pt" % i,
                         SIZE_MIN_PT, SIZE_MAX_PT, warnings)
        line_default = (_as_float(default_line, round(size * LINE_RATIO, 1),
                                  "line_pt", 10, 60, warnings)
                        if default_line is not None
                        else round(size * LINE_RATIO, 1))
        line = _as_float(item.get("line_pt"), line_default,
                         "segments[%d].line_pt" % i, 10, 60, warnings)
        align = str(item.get("align") or "left").strip().lower()
        if align not in ("left", "center", "right"):
            raise ApiError(400, "segments[%d].align 只支持 left/center/right：%r"
                           % (i, align))
        seg = {"text": text.strip(), "size_pt": round(size, 2),
               "line_pt": round(line, 2), "align": align}
        if item.get("rect") is not None:
            rect = item["rect"]
            if not isinstance(rect, (list, tuple)) or len(rect) != 4:
                raise ApiError(400, "segments[%d].rect 必须是 4 个数字" % i)
            seg["rect"] = [round(_as_float(v, 0, "segments[%d].rect" % i), 2)
                           for v in rect]
        segments.append(seg)
    return segments, warnings


def _auto_paragraphs(segments, page_w, page_h, body, warnings):
    """把 segments 排成 page_model 的 paragraphs（自动纵向堆叠）。"""
    left = _as_float(body.get("margin_left_pt"), 56.0, "margin_left_pt", 0, page_w / 3)
    right_margin = _as_float(body.get("margin_right_pt"), 56.0,
                             "margin_right_pt", 0, page_w / 3)
    top = _as_float(body.get("top_pt"), 96.0, "top_pt", 0, page_h - 20)
    gap = _as_float(body.get("gap_pt"), 10.0, "gap_pt", 0, 80)
    bottom_margin = _as_float(body.get("bottom_pt"), 56.0, "bottom_pt", 0, page_h / 3)
    jitter = _as_float(body.get("jitter"), 1.0, "jitter", 0, 3, warnings)
    base_seed = _as_int(body.get("seed"), 20261118, "seed")
    right = page_w - right_margin
    width = max(20.0, right - left)
    bottom_limit = page_h - bottom_margin
    paragraphs = []
    y = top
    for i, seg in enumerate(segments):
        size, line = seg["size_pt"], seg["line_pt"]
        if seg.get("rect"):
            rect = list(seg["rect"])
            y = max(y, rect[3] + gap)
        else:
            n_lines = _estimate_lines(seg["text"], width, size)
            height = max(line * 1.15, n_lines * line * 1.08 + 4)
            rect = [round(left, 2), round(y, 2), round(right, 2),
                    round(y + height, 2)]
            y = rect[3] + gap
        if rect[3] > bottom_limit + 0.5:
            warnings.append("第 %d 段底部 %.1fpt 超出页面可用区域（%.1fpt），"
                            "可能被裁切，建议减小字号或分页"
                            % (i + 1, rect[3], bottom_limit))
        paragraphs.append({
            "text": seg["text"], "rect": rect,
            "size_pt": size, "line_pt": line, "align": seg["align"],
            "jitter": jitter, "seed": base_seed + i * 17, "ink": list(H.INK),
        })
    return paragraphs


def _normalize_spec(raw_spec, bg_info=None):
    """校验/补全 page_model spec，返回 (spec, texts, warnings)。"""
    if not isinstance(raw_spec, dict):
        raise ApiError(400, "spec 必须是 JSON 对象")
    spec = copy.deepcopy(raw_spec)
    warnings = []
    if bg_info is None:
        bg_info = _parse_background(spec.get("bg"))
    spec["bg"] = {"kind": bg_info["kind"], "path": bg_info["path"],
                  "page": bg_info["page"], "dpi": bg_info["dpi"]}
    default_w = bg_info["page_pt"][0] if bg_info.get("page_pt") else A4_W
    default_h = bg_info["page_pt"][1] if bg_info.get("page_pt") else A4_H
    w = _as_float(spec.get("width_pt"), default_w, "width_pt", 100, 3000)
    h = _as_float(spec.get("height_pt"), default_h, "height_pt", 100, 3000)
    if bg_info.get("page_pt"):
        # PDF 背景以 PDF 页面尺寸为准（段落 rect 也按该页 pt 坐标解释）
        w, h = bg_info["page_pt"]
    spec["width_pt"], spec["height_pt"] = round(w, 2), round(h, 2)
    source = spec.get("source_pdf")
    if source:
        spec["source_pdf"] = _resolve_read_file(source, PDF_EXTS, "源 PDF")
    else:
        spec["source_pdf"] = ""

    paras = spec.get("paragraphs")
    if not isinstance(paras, list) or not paras:
        raise ApiError(400, "spec.paragraphs 必须是非空数组（至少一段文字）")
    if len(paras) > MAX_SEGMENTS:
        raise ApiError(400, "paragraphs 过多（%d > %d）" % (len(paras), MAX_SEGMENTS))
    clean = []
    for i, p in enumerate(paras):
        if not isinstance(p, dict):
            raise ApiError(400, "paragraphs[%d] 必须是对象" % i)
        text = p.get("text") or ""
        if not isinstance(text, str):
            raise ApiError(400, "paragraphs[%d].text 必须是字符串" % i)
        rect = p.get("rect")
        if rect is None:
            rect = [56.0, 120.0 + 40.0 * i, w - 56.0,
                    min(h - 60.0, 120.0 + 40.0 * i + 260.0)]
        if not isinstance(rect, (list, tuple)) or len(rect) != 4:
            raise ApiError(400, "paragraphs[%d].rect 必须是 [左,上,右,下] 4 个数字" % i)
        rect = [round(_as_float(v, 0, "paragraphs[%d].rect[%d]" % (i, k)), 2)
                for k, v in enumerate(rect)]
        if abs(rect[2] - rect[0]) < 1 or abs(rect[3] - rect[1]) < 1:
            raise ApiError(400, "paragraphs[%d].rect 宽/高不能为 0" % i)
        size = _as_float(p.get("size_pt"), DEFAULT_SIZE_PT,
                         "paragraphs[%d].size_pt" % i,
                         SIZE_MIN_PT, SIZE_MAX_PT, warnings)
        line = _as_float(p.get("line_pt"), DEFAULT_LINE_PT,
                         "paragraphs[%d].line_pt" % i, 10, 60, warnings)
        align = str(p.get("align") or "left").strip().lower()
        if align not in ("left", "center", "right"):
            raise ApiError(400, "paragraphs[%d].align 只支持 left/center/right" % i)
        jitter = _as_float(p.get("jitter"), 1.0, "paragraphs[%d].jitter" % i,
                           0, 3, warnings)
        seed = _as_int(p.get("seed"), 20261118 + i * 17, "paragraphs[%d].seed" % i)
        ink = p.get("ink") or list(H.INK)
        if not isinstance(ink, (list, tuple)) or len(ink) != 3:
            raise ApiError(400, "paragraphs[%d].ink 必须是 [r,g,b]" % i)
        ink = [int(_as_float(v, 0, "paragraphs[%d].ink[%d]" % (i, k), 0, 255))
               for k, v in enumerate(ink)]
        clean.append({"text": text, "rect": rect, "size_pt": round(size, 2),
                      "line_pt": round(line, 2), "align": align,
                      "jitter": jitter, "seed": seed, "ink": ink})
    spec["paragraphs"] = clean
    texts = [p["text"] for p in clean if p["text"].strip()]
    if not texts:
        raise ApiError(400, "spec 里没有可渲染的文字")
    return spec, texts, warnings


def _build_spec(body):
    """请求体 → (spec, texts, warnings, meta)。"""
    fmt = str(body.get("format") or "png").strip().lower()
    if fmt not in ("png", "pdf"):
        raise ApiError(400, "format 只支持 png / pdf：%r" % fmt)
    rb = body.get("return_base64", True)
    meta = {"format": fmt,
            "return_base64": True if rb is None else bool(rb),
            "write": bool(body.get("write", False)),
            "filename": body.get("filename")}
    warnings = []
    if "spec" in body:
        raw_spec = body.get("spec")
        if not isinstance(raw_spec, dict):
            raise ApiError(400, "spec 必须是 JSON 对象")
        bg_info = _parse_background(raw_spec.get("bg"))
        if body.get("background") is not None:
            bg_info = _parse_background(body["background"])
            raw_spec = copy.deepcopy(raw_spec)
            raw_spec["bg"] = {"kind": bg_info["kind"], "path": bg_info["path"],
                              "page": bg_info["page"], "dpi": bg_info["dpi"]}
        spec, texts, warnings = _normalize_spec(raw_spec, bg_info)
    elif "segments" in body:
        bg_info = _parse_background(body.get("background"))
        page_w, page_h = (bg_info["page_pt"] if bg_info.get("page_pt")
                          else (A4_W, A4_H))
        segments, warnings = _segments_from_body(body)
        paragraphs = _auto_paragraphs(segments, page_w, page_h, body, warnings)
        spec = {"width_pt": page_w, "height_pt": page_h,
                "bg": {"kind": bg_info["kind"], "path": bg_info["path"],
                       "page": bg_info["page"], "dpi": bg_info["dpi"]},
                "source_pdf": "", "paragraphs": paragraphs}
        texts = [p["text"] for p in paragraphs if p["text"].strip()]
    elif "paragraphs" in body:
        raw_spec = {key: body.get(key) for key in
                    ("width_pt", "height_pt", "bg", "source_pdf", "paragraphs")}
        bg_info = _parse_background(raw_spec.get("bg"))
        spec, texts, warnings = _normalize_spec(raw_spec, bg_info)
    else:
        raise ApiError(400, "请求体需要 segments / spec / paragraphs 三者之一",
                       hint='最简写法：{"segments":[{"text":"..."}],"format":"png"}')
    if fmt == "pdf" and not spec.get("source_pdf") and spec["bg"]["kind"] == "pdf":
        spec["source_pdf"] = spec["bg"]["path"]
    return spec, texts, warnings, meta


# ======================================================================
# 渲染（串行 + 超时）
# ======================================================================
def _render_png_bytes(spec, timeout=RENDER_TIMEOUT_S):
    def job():
        page = pm.page_from_spec(spec)
        fd, tmp = tempfile.mkstemp(prefix=TMP_PREFIX, suffix=".png",
                                   dir=_ensure_out_dir())
        os.close(fd)
        try:
            pm.export_png(page, tmp, with_frames=False)
            with open(tmp, "rb") as f:
                data = f.read()
        finally:
            _remove_quiet(tmp)
        try:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as im:
                width_px, height_px = im.size
        except Exception as e:
            raise ApiError(500, "渲染出的 PNG 不合法：%s" % e)
        return {"data": data, "width_px": width_px, "height_px": height_px,
                "page_pt": [round(page.width_pt, 2), round(page.height_pt, 2)],
                "warnings": list(page._warn)}

    return _run_serialized(job, timeout)


def _render_pdf_bytes(spec, timeout=RENDER_TIMEOUT_S):
    def job():
        render_spec = copy.deepcopy(spec)
        bg = render_spec.get("bg") or {}
        source = render_spec.get("source_pdf") or ""
        page_index = int(bg.get("page") or 0)
        if source and bg.get("kind") == "pdf":
            # 有源 PDF 时手写层只需要一张同尺寸透明画布，不用把 PDF 光栅化成背景
            render_spec["bg"] = {"kind": "blank", "path": "", "page": 0, "dpi": 200}
        page = pm.page_from_spec(render_spec)
        if source:
            page.source_pdf = source
            page.bg_page = page_index
        pm.render_page(page)                 # 只为拿排版警告（红框图会丢弃）
        warnings = list(page._warn)
        fd, tmp = tempfile.mkstemp(prefix=TMP_PREFIX, suffix=".pdf",
                                   dir=_ensure_out_dir())
        os.close(fd)
        try:
            pm.export_pdf(page, tmp)
            with open(tmp, "rb") as f:
                data = f.read()
        finally:
            _remove_quiet(tmp)
        pages = 0
        try:
            import pymupdf as fitz
            doc = fitz.open(stream=data, filetype="pdf")
            pages = doc.page_count
            doc.close()
        except Exception as e:
            raise ApiError(500, "渲染出的 PDF 不合法：%s" % e)
        return {"data": data, "pages": pages,
                "page_pt": [round(page.width_pt, 2), round(page.height_pt, 2)],
                "warnings": warnings}

    return _run_serialized(job, timeout)


def _run_serialized(job, timeout):
    """渲染串行化 + 超时：超时返回 504，服务不崩。"""
    holder = {}

    def runner():
        acquired = _RENDER_LOCK.acquire(timeout=max(1.0, LOCK_WAIT_S))
        if not acquired:
            holder["error"] = ApiError(
                503, "渲染队列忙（等待超过 %.0f 秒），请稍后重试" % LOCK_WAIT_S)
            return
        try:
            holder["value"] = job()
        except BaseException as e:            # noqa: BLE001 —— 要原样回传给请求线程
            holder["error"] = e
            holder["traceback"] = traceback.format_exc()
        finally:
            _RENDER_LOCK.release()

    thread = threading.Thread(target=runner, name="ai-render", daemon=True)
    thread.start()
    thread.join(max(1.0, float(timeout)))
    if thread.is_alive():
        raise ApiError(504, "渲染超时（超过 %.0f 秒）" % timeout,
                       hint="可减小字号/段落数，或调大环境变量 HANDWRITE_AI_TIMEOUT")
    if "error" in holder:
        err = holder["error"]
        if isinstance(err, ApiError):
            raise err
        sys.stderr.write("[ai_api] 渲染异常:\n%s\n"
                         % holder.get("traceback", traceback.format_exc()))
        raise ApiError(500, "渲染失败：%s: %s" % (type(err).__name__, err))
    return holder["value"]


# ======================================================================
# 端点实现
# ======================================================================
def _health_payload(host, port):
    han, sym = ai_prompt.allowed_charset()
    return {
        "ok": True, "tool": TOOL_NAME, "version": VERSION,
        "python": sys.version.split()[0],
        "fonts": {"hanzi": ai_prompt.han_font_path() is not None,
                  "symbol": ai_prompt.symbol_font_path() is not None},
        "charset": {"hanzi_count": len(han), "symbol_count": len(sym)},
        "host": host, "port": int(port),
        "out_dir": OUT_DIR,
        "llm": llm_client.public_config(),
        "server_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def _endpoints():
    return [
        {"method": "GET", "path": "/health", "description": "健康检查与字体可用性"},
        {"method": "GET", "path": "/capabilities",
         "description": "字表/字号/页面/段落 schema/端点表"},
        {"method": "POST", "path": "/render",
         "description": "segments 简写或 page_model spec → PNG/PDF"},
        {"method": "POST", "path": "/render_pdf",
         "description": "叠加到指定 PDF 页，保留原内容"},
        {"method": "POST", "path": "/prompt",
         "description": "内置系统提示词（含动态字表）"},
        {"method": "POST", "path": "/answer",
         "description": "答案文本自动切段并生成可直接 render 的 spec"},
        {"method": "POST", "path": "/worksheet/analyze",
         "description": "作业页自动划题：题目/答题区/横线/置信度 + 扫描件 crops"},
        {"method": "POST", "path": "/worksheet/answer",
         "description": "作业页 + 答案 → 手写作答 PDF（叠加原页，保留原内容）"},
        {"method": "POST", "path": "/worksheet/prompt",
         "description": "做题提示词：逐题作答、只输出 JSON、字库/字数约束"},
        {"method": "POST", "path": "/worksheet/solve",
         "description": "自动做题一站式：划题 → 模型作答 → 手写写回 PDF（也可只传 answers 跳过模型）"},
        {"method": "GET", "path": "/llm/status",
         "description": "自动做题模型配置（脱敏）：是否已配置、模型名、端点"},
    ]


def _worksheet_endpoints():
    """做题接口的端点表（/capabilities.worksheet.endpoints 与文档共用）。"""
    return [
        {"method": "POST", "path": "/worksheet/analyze",
         "description": "导入 PDF/图片，自动划定题目与答题区域（返回 spec + 精简题目 + crops）"},
        {"method": "POST", "path": "/worksheet/answer",
         "description": "按 spec/layout + 答案渲染手写作答 PDF（原 PDF 内容保留）"},
        {"method": "POST", "path": "/worksheet/prompt",
         "description": "生成做题提示词（逐题作答、只输出 JSON、字库内字符）"},
        {"method": "POST", "path": "/worksheet/solve",
         "description": "自动做题一站式：analyze → 内置提示词 → 调模型作答 → 手写作答 PDF"},
    ]


def _capabilities_payload():
    han, sym = ai_prompt.allowed_charset()
    han_str, sym_str = ai_prompt.charset_strings()
    han_font = ai_prompt.han_font_path()
    sym_font = ai_prompt.symbol_font_path()
    han_dir = symbols.han_dir()
    sym_dir = symbols.sym_dir()
    return {
        "ok": True, "tool": TOOL_NAME, "version": VERSION,
        "character_set": {
            "hanzi_count": len(han),
            "symbol_count": len(sym),
            "hanzi": han_str,
            "symbols": sym_str,
            "source": {
                "han_font": han_font,
                "symbol_font": sym_font,
                "note": "由 fontTools 读取字体 cmap 动态生成，未硬编码字表",
            },
        },
        "fonts": {
            "han": {"available": bool(han_font), "dir": han_dir,
                    "main": han_font,
                    "passes": _pass_names(han_dir)},
            "symbol": {"available": bool(sym_font), "dir": sym_dir,
                       "main": sym_font,
                       "passes": _pass_names(sym_dir)},
        },
        "size": {"min_pt": SIZE_MIN_PT, "max_pt": SIZE_MAX_PT,
                 "default_pt": DEFAULT_SIZE_PT},
        "line": {"default_pt": DEFAULT_LINE_PT, "ratio": LINE_RATIO,
                 "hint": "line_pt 约等于 1.6–1.9 × size_pt"},
        "page": {"a4_pt": [A4_W, A4_H],
                 "a4_px": [int(A4_W * H.SCALE), int(A4_H * H.SCALE)],
                 "scale": int(H.SCALE),
                 "default_margins_pt": {"left": 56, "right": 56,
                                        "top": 96, "bottom": 56},
                 "background_kinds": ["blank", "image", "pdf"]},
        "paragraph_schema": {
            "text": "string（必填）",
            "rect": "[left, top, right, bottom] pt；缺省时自动排版",
            "size_pt": "%g–%g，默认 %g" % (SIZE_MIN_PT, SIZE_MAX_PT, DEFAULT_SIZE_PT),
            "line_pt": "默认 %g（约 1.85 × 字号）" % DEFAULT_LINE_PT,
            "align": "left | center | right",
            "jitter": "0–3，默认 1.0",
            "seed": "int，默认 20261118",
            "ink": "[r, g, b]，默认 [16, 16, 18]",
        },
        "segment_schema": ai_prompt.segment_schema(),
        "formats": ["png", "pdf"],
        "endpoints": _endpoints(),
        "limits": {"max_body_bytes": MAX_BODY_BYTES,
                   "max_segments": MAX_SEGMENTS,
                   "render_timeout_s": RENDER_TIMEOUT_S,
                   "render_lock_wait_s": LOCK_WAIT_S},
        "prompt": {"system_prompt_template": ai_prompt.SYSTEM_PROMPT,
                   "system_prompt_length": len(ai_prompt.SYSTEM_PROMPT),
                   "tool_schema": ai_prompt.tool_schema()},
        # 「做题」接口（做作业页：自动划题 → AI 答案 → 手写写回）。追加字段，
        # 不影响原有键。
        "worksheet": {
            "spec_version": ws.SPEC_VERSION,
            "llm": llm_client.public_config(),
            "endpoints": _worksheet_endpoints(),
            "cli": "做题.py",
            "modes": ["pdf", "image"],
            "spec_fields": {
                "source": "作业页路径（PDF/图片）",
                "kind": "pdf | image",
                "pages[]": "page/width_pt/height_pt/questions[]/warnings[]",
                "questions[].id": "Q1 / Q4.2.1（唯一，重复题号会带页后缀）",
                "questions[].number": "题号；扫描件为顺序号或 --numbers 指定",
                "questions[].text": "题目文字；扫描件为 null（用 crops 读图）",
                "questions[].rect": "题目区域 [l,t,r,b] pt",
                "questions[].answer_rect": "答题区域 [l,t,r,b] pt",
                "questions[].answer_lines": "横线条数（PDF）或按空白估算的可写行数（扫描件）",
                "questions[].confidence": "0–1 识别置信度",
                "questions[].crop_png_base64": "仅扫描页：题目区域裁剪 PNG（base64）",
            },
            "answers_format": ['{"1": "解：…", "4.2.1": "解：…"}',
                               '[{"id": "Q1", "text": "解：…"}]'],
            "render": {"default_size_pt": ws.SIZE_DEFAULT_PT,
                       "min_size_pt": ws.SIZE_MIN_PT,
                       "line_ratio": ws.LINE_RATIO,
                       "note": "答案从 answer_rect 顶部按 rect 宽书写；超出高度才缩字号并给 warning"},
            "scanned_pdf": "PDF 页无文字层时自动按扫描件（像素投影）分析并返回 crops",
        },
    }


def _root_payload():
    return {"ok": True, "tool": TOOL_NAME, "version": VERSION,
            "message": "手写体生成器 AI 接口（本地）",
            "endpoints": _endpoints(),
            "docs": os.path.join("docs", "AI接口说明.md")}


def _do_render(body):
    t0 = time.perf_counter()
    spec, texts, layout_warnings, meta = _build_spec(body)
    cov = _coverage(texts)
    warnings = _dedupe(list(layout_warnings) + list(cov["warnings"]))
    if meta["format"] == "png":
        out = _render_png_bytes(spec)
        data = out["data"]
        warnings = _dedupe(warnings + list(out["warnings"]))
        resp = {"ok": True, "format": "png",
                "image": {"width_px": out["width_px"],
                          "height_px": out["height_px"],
                          "scale": int(H.SCALE)},
                "page_pt": out["page_pt"],
                "paragraphs": len(spec["paragraphs"]),
                "warnings": warnings, "coverage": cov,
                "bytes": len(data)}
        if meta["return_base64"]:
            resp["png_base64"] = base64.b64encode(data).decode("ascii")
        else:
            resp["base64_omitted"] = True
        if meta["write"]:
            resp["path"] = _write_bytes(data, meta["filename"], ".png")
    else:
        out = _render_pdf_bytes(spec)
        data = out["data"]
        warnings = _dedupe(warnings + list(out["warnings"]))
        resp = {"ok": True, "format": "pdf",
                "pages": out["pages"], "page_pt": out["page_pt"],
                "paragraphs": len(spec["paragraphs"]),
                "warnings": warnings, "coverage": cov,
                "bytes": len(data)}
        if meta["return_base64"]:
            resp["pdf_base64"] = base64.b64encode(data).decode("ascii")
        else:
            resp["base64_omitted"] = True
        if meta["write"]:
            resp["path"] = _write_bytes(data, meta["filename"], ".pdf")
    resp["elapsed_ms"] = int(round((time.perf_counter() - t0) * 1000))
    return resp


def _do_render_pdf(body):
    t0 = time.perf_counter()
    pdf_raw = body.get("pdf") or body.get("source_pdf") or body.get("path")
    if not pdf_raw:
        raise ApiError(400, "缺少 pdf（源 PDF 路径），例如 {\"pdf\":\"模板数据/xxx.pdf\",\"segments\":[...]}")
    pdf_path = _resolve_read_file(pdf_raw, PDF_EXTS, "源 PDF")
    import pymupdf as fitz
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        raise ApiError(400, "源 PDF 无法打开：%s（%s）" % (pdf_path, e))
    try:
        pages = doc.page_count
        if pages < 1:
            raise ApiError(400, "源 PDF 没有页面：%s" % pdf_path)
        page_index = _as_int(body.get("page"), 0, "page", 0, pages - 1)
        r = doc[page_index].rect
        page_w, page_h = round(float(r.width), 2), round(float(r.height), 2)
    finally:
        doc.close()

    bg = {"kind": "pdf", "path": pdf_path, "page": page_index, "dpi": 200}
    warnings = []
    if "segments" in body:
        segments, warnings = _segments_from_body(body)
        paragraphs = _auto_paragraphs(segments, page_w, page_h, body, warnings)
        spec = {"width_pt": page_w, "height_pt": page_h, "bg": bg,
                "source_pdf": pdf_path, "paragraphs": paragraphs}
        texts = [p["text"] for p in paragraphs if p["text"].strip()]
    elif "spec" in body:
        raw_spec = copy.deepcopy(body["spec"])
        if not isinstance(raw_spec, dict):
            raise ApiError(400, "spec 必须是 JSON 对象")
        raw_spec["bg"] = dict(bg)
        spec, texts, warnings = _normalize_spec(raw_spec, _parse_background(bg))
        spec["width_pt"], spec["height_pt"] = page_w, page_h
        spec["source_pdf"] = pdf_path
    else:
        raise ApiError(400, "请求体需要 segments 或 spec（另需 pdf 路径）")

    out = _render_pdf_bytes(spec)
    cov = _coverage(texts)
    resp = {"ok": True, "format": "pdf",
            "pages": pages, "page": page_index,
            "output_pages": out["pages"],
            "page_pt": [page_w, page_h],
            "paragraphs": len(spec["paragraphs"]),
            "warnings": _dedupe(list(warnings) + list(cov["warnings"])
                                + list(out["warnings"])),
            "coverage": cov, "bytes": len(out["data"])}
    rb = body.get("return_base64", True)
    if rb is None or bool(rb):
        resp["pdf_base64"] = base64.b64encode(out["data"]).decode("ascii")
    if bool(body.get("write", False)):
        resp["path"] = _write_bytes(out["data"], body.get("filename"),
                                    ".pdf")
    resp["elapsed_ms"] = int(round((time.perf_counter() - t0) * 1000))
    return resp


def _do_prompt(body):
    t0 = time.perf_counter()
    question = body.get("question")
    if question is not None and not isinstance(question, str):
        raise ApiError(400, "question 必须是字符串")
    segments = body.get("segments")
    prompt = ai_prompt.builtin_prompt(question=question, segments=segments)
    han, sym = ai_prompt.allowed_charset()
    return {"ok": True, "prompt": prompt,
            "character_set": {"hanzi_count": len(han),
                              "symbol_count": len(sym)},
            "tool_schema": ai_prompt.tool_schema(),
            "tool_schema_anthropic": ai_prompt.tool_schema_anthropic(),
            "elapsed_ms": int(round((time.perf_counter() - t0) * 1000))}


def _split_answer(text):
    """把 AI 的答案文本切成 segments：优先按行/空行，超长按句末标点再切。"""
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not s:
        return []
    # 若 answer_text 本身就是带 segments 的 JSON，直接取其中的 text
    if s.startswith("{") and '"segments"' in s:
        try:
            obj = json.loads(s)
            if isinstance(obj, dict) and isinstance(obj.get("segments"), list):
                out = []
                for item in obj["segments"]:
                    t = item.get("text") if isinstance(item, dict) else item
                    if isinstance(t, str) and t.strip():
                        out.append(t.strip())
                if out:
                    return out
        except Exception:
            pass
    lines = []
    for raw in s.split("\n"):
        ln = raw.strip()
        if not ln:
            continue
        ln = re.sub(r"^[-*•·]\s+", "", ln).strip()
        ln = re.sub(r"^#+\s*", "", ln).strip()
        ln = ln.replace("**", "").replace("__", "").strip()
        if ln:
            lines.append(ln)
    out = []
    for ln in lines:
        if len(ln) <= MAX_ANSWER_SEGMENT_CHARS:
            out.append(ln)
            continue
        parts = re.split(r"(?<=[。；！？;!?])", ln)
        buf = ""
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if buf and len(buf) + len(part) > MAX_ANSWER_SEGMENT_CHARS:
                out.append(buf)
                buf = part
            else:
                buf += part
        if buf:
            out.append(buf)
    final = []
    for t in out:
        if len(t) <= MAX_ANSWER_SEGMENT_CHARS:
            final.append(t)
        else:
            final.extend(t[i:i + MAX_ANSWER_SEGMENT_CHARS]
                         for i in range(0, len(t), MAX_ANSWER_SEGMENT_CHARS))
    return [t.strip() for t in final if t.strip()]


def _do_answer(body):
    t0 = time.perf_counter()
    question = body.get("question") or ""
    if not isinstance(question, str):
        question = str(question)
    answer = body.get("answer_text")
    if answer is None:
        answer = body.get("answer")
    if isinstance(answer, dict):
        answer = json.dumps(answer, ensure_ascii=False)
    if not isinstance(answer, str) or not answer.strip():
        raise ApiError(400, "缺少 answer_text（字符串），例如 "
                            '{"question":"...","answer_text":"解：..."}')
    texts = _split_answer(answer)
    if not texts:
        raise ApiError(400, "answer_text 切分后为空")
    if len(texts) > MAX_SEGMENTS:
        raise ApiError(400, "答案段数过多（%d > %d），请精简或分页"
                       % (len(texts), MAX_SEGMENTS))
    warnings = []
    size = _as_float(body.get("size_pt"), DEFAULT_SIZE_PT, "size_pt",
                     SIZE_MIN_PT, SIZE_MAX_PT, warnings)
    line = _as_float(body.get("line_pt"), round(size * LINE_RATIO, 1),
                     "line_pt", 10, 60, warnings)
    segments = [{"text": t, "size_pt": size, "line_pt": line, "align": "left"}
                for t in texts]
    paragraphs = _auto_paragraphs(segments, A4_W, A4_H, body, warnings)
    spec = {"width_pt": A4_W, "height_pt": A4_H,
            "bg": {"kind": "blank", "path": "", "page": 0, "dpi": 200},
            "source_pdf": "", "paragraphs": paragraphs}
    out_segments = [{"text": p["text"], "size_pt": p["size_pt"],
                     "line_pt": p["line_pt"], "align": p["align"],
                     "rect": p["rect"]} for p in paragraphs]
    return {"ok": True, "question": question,
            "segments": out_segments, "spec": spec, "warnings": warnings,
            "prompt": ai_prompt.builtin_prompt(question=question or None),
            "elapsed_ms": int(round((time.perf_counter() - t0) * 1000))}


# ======================================================================
# 做题接口（worksheet）：analyze / answer / prompt
# ======================================================================
def _worksheet_source(body):
    """取并校验作业页路径（PDF 或图片，沿用读文件白名单）。"""
    raw = body.get("source") or body.get("pdf") or body.get("path")
    if not raw:
        raise ApiError(400, "缺少 source（作业页路径）",
                       hint='例如 {"source":"_work/worksheet_samples/作业A.pdf"}')
    return _resolve_read_file(raw, PDF_EXTS | IMAGE_EXTS, "作业页")


def _worksheet_page(body):
    raw = body.get("page")
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip().lower() in ("", "all", "*"):
        return None
    return _as_int(raw, 0, "page", 0, 10 ** 6)


def _worksheet_numbers(body):
    raw = body.get("numbers")
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw]
    raise ApiError(400, "numbers 必须是字符串（逗号分隔）或数组")


def _do_worksheet_analyze(body):
    t0 = time.perf_counter()
    source = _worksheet_source(body)
    page = _worksheet_page(body)
    numbers = _worksheet_numbers(body)
    dpi = None
    if body.get("dpi") is not None:
        dpi = _as_float(body.get("dpi"), 200, "dpi", 50, 600)
    try:
        spec = ws.analyze(source, page=page, dpi=dpi, numbers=numbers)
    except FileNotFoundError as e:
        raise ApiError(404, str(e))
    except ValueError as e:
        raise ApiError(400, str(e))
    questions = ws.questions_brief(spec)
    resp = {"ok": True, "source": source, "kind": spec.get("kind"),
            "spec_version": spec.get("version") or ws.SPEC_VERSION,
            "spec": spec, "questions": questions, "count": len(questions),
            "warnings": spec.get("warnings") or []}
    crops = ws.collect_crops(spec)
    if crops:
        resp["crops"] = crops
    resp["elapsed_ms"] = int(round((time.perf_counter() - t0) * 1000))
    return resp


def _do_worksheet_answer(body):
    t0 = time.perf_counter()
    source = _worksheet_source(body)
    answers = body.get("answers")
    if answers is None:
        raise ApiError(400, "缺少 answers（对象 {题号: 文本} 或 "
                            '[{"id":"Q1","text":"..."}] 数组）')
    if not isinstance(answers, (dict, list)):
        raise ApiError(400, "answers 必须是 JSON 对象或数组")
    page = _worksheet_page(body)
    numbers = _worksheet_numbers(body)

    spec = body.get("spec")
    layout_raw = body.get("layout") or body.get("layout_path")
    if spec is not None:
        if not isinstance(spec, dict):
            raise ApiError(400, "spec 必须是 JSON 对象")
        spec = copy.deepcopy(spec)
        spec["source"] = source
    elif layout_raw:
        lp = _resolve_read_file(layout_raw, {".json"}, "layout 文件")
        try:
            spec = copy.deepcopy(ws.load_spec(lp))
        except (ValueError, FileNotFoundError) as e:
            raise ApiError(400, "layout 读取失败：%s" % e)
        spec["source"] = source
    else:
        try:
            spec = ws.analyze(source, page=page, numbers=numbers)
        except FileNotFoundError as e:
            raise ApiError(404, str(e))
        except ValueError as e:
            raise ApiError(400, str(e))
    if not isinstance(spec.get("pages"), list):
        raise ApiError(400, "spec 结构不合法：需要含 pages 数组的作业页 spec")

    # 先做一次答案匹配检查：匹配不到时给 400，而不是进渲染线程后变 500
    questions = [{"id": q.get("id"), "number": q.get("number")}
                 for _pg, q in ws._iter_questions(spec)]
    if not questions:
        raise ApiError(400, "spec 里没有题目，请先 analyze 或检查 page 参数")
    try:
        matches, _w, _un, _miss = ws.normalize_answers(answers, questions)
    except ValueError as e:
        raise ApiError(400, str(e))
    if not matches:
        raise ApiError(400, "answers 没有匹配到任何题目（可用题号 1/2… "
                            '或 id Q1；扫描件可用 numbers 指定真实题号）')

    write = bool(body.get("write", False))
    rb = body.get("return_base64", True)
    filename = body.get("filename")
    _ensure_out_dir()
    fd, tmp = tempfile.mkstemp(prefix=TMP_PREFIX, suffix=".pdf", dir=OUT_DIR)
    os.close(fd)

    def job():
        pdf, warns, cov = ws.render_answers(spec, answers, out_pdf=tmp)
        with open(tmp, "rb") as f:
            data = f.read()
        return data, warns, cov

    try:
        try:
            data, warnings, coverage = _run_serialized(job, RENDER_TIMEOUT_S)
        except ApiError:
            raise
        except ValueError as e:
            raise ApiError(400, str(e))
    finally:
        _remove_quiet(tmp)

    pages = 0
    try:
        import pymupdf as fitz
        doc = fitz.open(stream=data, filetype="pdf")
        pages = doc.page_count
        doc.close()
    except Exception:
        pass
    resp = {"ok": True, "format": "pdf", "pages": pages, "source": source,
            "spec_version": spec.get("version") or ws.SPEC_VERSION,
            "warnings": warnings, "coverage": coverage, "bytes": len(data)}
    if rb is None or bool(rb):
        resp["pdf_base64"] = base64.b64encode(data).decode("ascii")
    else:
        resp["base64_omitted"] = True
    if write:
        resp["path"] = _write_bytes(data, filename, ".pdf")
    resp["elapsed_ms"] = int(round((time.perf_counter() - t0) * 1000))
    return resp


def _do_worksheet_prompt(body):
    t0 = time.perf_counter()
    spec = body.get("spec")
    if spec is not None and not isinstance(spec, dict):
        raise ApiError(400, "spec 必须是 JSON 对象")
    if spec is None:
        raw_source = (body.get("source") or body.get("pdf")
                      or body.get("path"))
        if not raw_source:
            raise ApiError(400, "需要 source（作业页路径）或 spec",
                           hint='例如 {"source":"_work/worksheet_samples/作业A.pdf"}')
        source = _resolve_read_file(raw_source, PDF_EXTS | IMAGE_EXTS, "作业页")
        try:
            spec = ws.analyze(source, page=_worksheet_page(body),
                              numbers=_worksheet_numbers(body))
        except FileNotFoundError as e:
            raise ApiError(404, str(e))
        except ValueError as e:
            raise ApiError(400, str(e))
    prompt = ai_prompt.worksheet_prompt(spec)
    han, sym = ai_prompt.allowed_charset()
    return {"ok": True, "prompt": prompt, "prompt_length": len(prompt),
            "questions": len(ws.questions_brief(spec or {})),
            "spec_version": (spec or {}).get("version") or ws.SPEC_VERSION,
            "character_set": {"hanzi_count": len(han),
                              "symbol_count": len(sym)},
            "elapsed_ms": int(round((time.perf_counter() - t0) * 1000))}


def _do_worksheet_solve(body):
    """自动做题一站式：划题 → 内置提示词 → 模型作答 → 手写作答 PDF。

    请求体
    ------
    source       作业页路径（PDF/图片），必填
    page/numbers 同 /worksheet/analyze（扫描件题号）
    answers      可选：{题号: 文本}；传了就跳过模型直接渲染
    spec         可选：复用已 analyze 的 spec（此时不再重新划题）
    write/filename/return_base64  同 /worksheet/answer
    include_prompt  true 时把提示词也返回（默认 False，响应更小）

    返回
    ----
    有答案可渲染 → {"ok":true, "pdf_base64":..., "coverage":..., "answers_meta":...}
    没答案但配置了模型 → 自动调模型作答后同上（"auto_answered":true）
    没答案也没模型 → {"ok":true, "needs_answers":true, "questions":[...],
                       "prompt":..., "hint":...}（外部 AI 补 answers 后再调）
    """
    t0 = time.perf_counter()
    source = _worksheet_source(body)
    page = _worksheet_page(body)
    numbers = _worksheet_numbers(body)

    spec = body.get("spec")
    if spec is not None:
        if not isinstance(spec, dict):
            raise ApiError(400, "spec 必须是 JSON 对象")
        spec = copy.deepcopy(spec)
        spec["source"] = source
    else:
        try:
            spec = ws.analyze(source, page=page, numbers=numbers)
        except FileNotFoundError as e:
            raise ApiError(404, str(e))
        except ValueError as e:
            raise ApiError(400, str(e))
    if not isinstance(spec.get("pages"), list) or not ws.questions_brief(spec):
        raise ApiError(400, "没有识别到题目：请检查 page/numbers 或改传 spec")

    prompt = ai_prompt.worksheet_prompt(spec)
    questions = ws.questions_brief(spec)

    answers = body.get("answers")
    answers_meta = None
    auto_answered = False
    if answers is None:
        if not llm_client.available():
            resp = {"ok": True, "needs_answers": True,
                    "source": source,
                    "spec_version": spec.get("version") or ws.SPEC_VERSION,
                    "questions": questions,
                    "character_set": {
                        "hanzi_count": len(ai_prompt.allowed_charset()[0]),
                        "symbol_count": len(ai_prompt.allowed_charset()[1])},
                    "prompt": prompt,
                    "prompt_length": len(prompt),
                    "llm": llm_client.public_config(),
                    "hint": ("这台机器还没有配置答题模型：设置 HANDWRITE_LLM_API_KEY"
                             "（可选 HANDWRITE_LLM_BASE_URL/HANDWRITE_LLM_MODEL）后"
                             "再调本端点即可全自动；或者把题目交给外部 AI，按 prompt 生成 "
                             '{"answers": {...}} 后带 answers 再调本端点。'),
                    "elapsed_ms": int(round((time.perf_counter() - t0) * 1000))}
            if body.get("include_prompt", True) is False:
                resp.pop("prompt", None)
            return resp
        try:
            got = llm_client.solve_worksheet(spec, prompt)
        except llm_client.LlmError as e:
            raise ApiError(e.code, str(e), hint=e.hint)
        answers = got["answers"]
        answers_meta = got.get("meta")
        auto_answered = True
    elif not isinstance(answers, (dict, list)):
        raise ApiError(400, "answers 必须是 JSON 对象或数组")

    body2 = dict(body)
    body2["spec"] = spec
    body2["answers"] = answers
    res = _do_worksheet_answer(body2)
    res["auto_answered"] = auto_answered
    if answers_meta:
        res["answers_meta"] = answers_meta
    if body.get("include_answers"):
        res["answers"] = answers
    if body.get("include_prompt"):
        res["prompt"] = prompt
    res["questions"] = questions
    res["elapsed_ms"] = int(round((time.perf_counter() - t0) * 1000))
    return res


def _llm_status_payload():
    cfg = llm_client.public_config()
    return {"ok": True, "configured": cfg["configured"], "llm": cfg,
            "endpoints": {
                "auto_solve": "POST /worksheet/solve {source}",
                "manual": "POST /worksheet/analyze → /worksheet/prompt → "
                          "/worksheet/answer",
            },
            "env": {"HANDWRITE_LLM_API_KEY": "必填（或 OPENAI_API_KEY / "
                                             "DEEPSEEK_API_KEY）",
                    "HANDWRITE_LLM_BASE_URL": "OpenAI 兼容端点，默认 DeepSeek",
                    "HANDWRITE_LLM_MODEL": "模型名，默认 deepseek-chat",
                    "HANDWRITE_LLM_VISION": "1/0：扫描件截图是否发给模型"}}


# ======================================================================
# HTTP 服务
# ======================================================================
class AiHandler(BaseHTTPRequestHandler):
    server_version = "handwrite-ai/" + VERSION
    protocol_version = "HTTP/1.1"

    # ---------------------------------------------------------- 路由
    POST_ROUTES = ("/render", "/render_pdf", "/prompt", "/answer",
                   "/worksheet/analyze", "/worksheet/answer",
                   "/worksheet/prompt", "/worksheet/solve")
    GET_ROUTES = ("/", "/help", "/health", "/capabilities", "/llm/status")

    def _method_not_allowed(self, allow):
        self._send_json(405, {"ok": False, "code": 405,
                              "error": "方法不允许：%s %s，请使用 %s"
                                       % (self.command,
                                          urllib.parse.urlsplit(self.path).path,
                                          allow),
                              "hint": "端点表见 GET /capabilities"})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        try:
            route = urllib.parse.urlsplit(self.path).path.rstrip("/") or "/"
            if route in self.POST_ROUTES:
                self._method_not_allowed("POST")
                return
            if route in ("/", "/help"):
                self._send_json(200, _root_payload())
            elif route == "/health":
                host, port = self._host_port()
                self._send_json(200, _health_payload(host, port))
            elif route == "/capabilities":
                self._send_json(200, _capabilities_payload())
            elif route == "/llm/status":
                self._send_json(200, _llm_status_payload())
            else:
                raise ApiError(404, "未知端点：GET %s" % route,
                               hint="可用端点见 GET /capabilities")
        except ApiError as e:
            self._send_json(e.code, e.payload())
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:                # noqa: BLE001
            self._unexpected(e)

    def do_POST(self):
        try:
            route = urllib.parse.urlsplit(self.path).path.rstrip("/") or "/"
            if route in self.GET_ROUTES:
                self._method_not_allowed("GET")
                return
            if route == "/render":
                self._send_json(200, _do_render(self._read_json()))
            elif route == "/render_pdf":
                self._send_json(200, _do_render_pdf(self._read_json()))
            elif route == "/prompt":
                self._send_json(200, _do_prompt(self._read_json()))
            elif route == "/answer":
                self._send_json(200, _do_answer(self._read_json()))
            elif route == "/worksheet/analyze":
                self._send_json(200, _do_worksheet_analyze(self._read_json()))
            elif route == "/worksheet/answer":
                self._send_json(200, _do_worksheet_answer(self._read_json()))
            elif route == "/worksheet/prompt":
                self._send_json(200, _do_worksheet_prompt(self._read_json()))
            elif route == "/worksheet/solve":
                self._send_json(200, _do_worksheet_solve(self._read_json()))
            else:
                raise ApiError(404, "未知端点：POST %s" % route,
                               hint="可用端点见 GET /capabilities")
        except ApiError as e:
            self._send_json(e.code, e.payload())
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:                # noqa: BLE001
            self._unexpected(e)

    def do_PUT(self):
        self._method_not_allowed("GET, POST, OPTIONS")

    def do_DELETE(self):
        self._method_not_allowed("GET, POST, OPTIONS")

    def do_PATCH(self):
        self._method_not_allowed("GET, POST, OPTIONS")

    # ---------------------------------------------------------- 工具
    def _host_port(self):
        addr = self.server.server_address
        return str(addr[0]), int(addr[1])

    def _read_json(self):
        raw_len = self.headers.get("Content-Length")
        if raw_len is None:
            raise ApiError(400, "缺少 Content-Length（请用 POST + JSON body）")
        try:
            n = int(raw_len)
        except (TypeError, ValueError):
            raise ApiError(400, "Content-Length 非法：%r" % raw_len)
        if n <= 0:
            return {}
        if n > MAX_BODY_BYTES:
            raise ApiError(413, "请求体过大（%.1f MB > 上限 %.1f MB）"
                           % (n / 1048576.0, MAX_BODY_BYTES / 1048576.0))
        raw = self.rfile.read(n)
        if len(raw) < n:
            raise ApiError(400, "请求体不完整（只收到 %d/%d 字节）" % (len(raw), n))
        try:
            obj = json.loads(raw.decode("utf-8-sig"))
        except UnicodeDecodeError:
            raise ApiError(400, "请求体不是 UTF-8 文本")
        except json.JSONDecodeError as e:
            raise ApiError(400, "请求体不是合法 JSON：%s" % e)
        if not isinstance(obj, dict):
            raise ApiError(400, "请求体必须是 JSON 对象")
        return obj

    def _cors_headers(self):
        origin = self.headers.get("Origin")
        if origin and _CORS_ORIGIN_RE.match(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods",
                             "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers",
                             "Content-Type, Accept")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Vary", "Origin")

    def _send_json(self, code, payload):
        try:
            body = json.dumps(payload, ensure_ascii=False,
                              allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as e:
            code = 500
            body = json.dumps({"ok": False, "code": 500,
                               "error": "响应序列化失败：%s" % e},
                              ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self._cors_headers()
        self.end_headers()
        try:
            self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _unexpected(self, exc):
        try:
            sys.stderr.write("[ai_api] 未处理异常:\n%s\n" % traceback.format_exc())
        except Exception:
            pass
        try:
            self._send_json(500, {"ok": False, "code": 500,
                                  "error": "内部错误：%s: %s"
                                           % (type(exc).__name__, exc)})
        except Exception:
            pass

    def log_message(self, fmt, *args):
        if _QUIET:
            return
        try:
            sys.stderr.write("[ai_api] %s %s\n"
                             % (time.strftime("%H:%M:%S"), fmt % args))
        except Exception:
            pass


# ======================================================================
# 服务启动
# ======================================================================
def _port_available(host, port):
    """Windows 的 SO_REUSEADDR 允许重复绑定同一端口（会静默抢不到连接），
    所以在真正启动前先用独占方式试绑一次，给「端口占用」一个明确错误。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        s.close()


def make_server(host=DEFAULT_HOST, port=DEFAULT_PORT):
    _ensure_out_dir()
    port = int(port)
    if port != 0 and not _port_available(host, port):
        raise OSError(errno.EADDRINUSE, "端口 %d 已被占用" % port)
    httpd = ThreadingHTTPServer((host, port), AiHandler)
    httpd.daemon_threads = True
    return httpd


def start_background_server(host=DEFAULT_HOST, port=0):
    """在后台线程起服务，返回 (httpd, thread, base_url)。端口 0 = 随机临时端口。"""
    httpd = make_server(host, port)
    thread = threading.Thread(target=httpd.serve_forever,
                              kwargs={"poll_interval": 0.05},
                              name="ai-api-server", daemon=True)
    thread.start()
    h, p = httpd.server_address[0], httpd.server_address[1]
    host_part = "[%s]" % h if ":" in str(h) else str(h)
    return httpd, thread, "http://%s:%d" % (host_part, p)


def _serve(host, port):
    _utf8_console()
    try:
        httpd = make_server(host, port)
    except OSError as e:
        print("[ai_api] 启动失败：%s" % e, file=sys.stderr)
        if (getattr(e, "errno", None) in (48, 98, 10048)
                or "10048" in str(e)
                or "address already in use" in str(e).lower()):
            print("[ai_api] 端口 %d 已被占用。请关闭占用进程，或换端口："
                  "py -3 ai_api.py --port %d" % (port, port + 1),
                  file=sys.stderr)
        return 2
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print("[ai_api] 手写体 AI 接口 v%s（%s）" % (VERSION, TOOL_NAME))
    print("[ai_api] 服务已启动: http://%s:%d" % (display_host, port))
    print("[ai_api] 端点: GET /health  GET /capabilities  "
          "POST /render  /render_pdf  /prompt  /answer  "
          "/worksheet/analyze  /worksheet/answer  /worksheet/prompt")
    print("[ai_api] 文档: %s" % os.path.join(HERE, "docs", "AI接口说明.md"))
    print("[ai_api] 按 Ctrl+C 停止")
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[ai_api] 收到 Ctrl+C，正在停止……")
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0


# ======================================================================
# 自测
# ======================================================================
def _http(base, method, path, payload=None, raw=None, origin=None, timeout=60):
    """本地自测用的 urllib 客户端，返回 (status_code, json_obj, headers)。"""
    headers = {"Accept": "application/json"}
    data = None
    if raw is not None:
        data = raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    elif payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(base + path, data=data,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            code = resp.status
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as e:
        body = e.read()
        code = e.code
        hdrs = {k.lower(): v for k, v in e.headers.items()}
    except Exception as e:                    # noqa: BLE001
        return None, {"ok": False, "error": "连接失败：%s" % e}, {}
    try:
        obj = json.loads(body.decode("utf-8"))
    except Exception:
        obj = {"_raw": body[:300].decode("utf-8", "replace")}
    return code, obj, hdrs


def run_self_test(base=None):
    """--self-test：临时端口起服务，逐个端点打通并校验；返回进程退出码。"""
    global _QUIET
    _utf8_console()
    _QUIET = True                        # 自测时关闭每个请求的访问日志
    print("=" * 78)
    print("handwrite-render AI 接口自测 v%s" % VERSION)
    print("python: %s" % sys.version.replace("\n", " "))
    print("汉字字体: %s" % (ai_prompt.han_font_path() or "缺失"))
    print("符号字体: %s" % (ai_prompt.symbol_font_path() or "缺失"))
    _ensure_out_dir()
    print("输出目录: %s" % OUT_DIR)
    checks, artifacts = [], []

    def check(name, cond, detail=""):
        ok = bool(cond)
        checks.append({"name": name, "ok": ok, "detail": str(detail)[:800]})
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name,
                             ("  " + str(detail)) if detail else ""))
        return ok

    own_server = base is None
    httpd = thread = None
    if own_server:
        try:
            httpd, thread, base = start_background_server(DEFAULT_HOST, 0)
        except OSError as e:
            print("[FAIL] 无法启动临时服务：%s" % e)
            return 1
        print("[selftest] 临时服务: %s (port=%d)"
              % (base, httpd.server_address[1]))
    try:
        # ---------------- 1. /health
        code, js, _ = _http(base, "GET", "/health")
        check("GET /health 返回 200 且 ok=true",
              code == 200 and js.get("ok") is True,
              "tool=%s version=%s" % (js.get("tool"), js.get("version")))
        check("GET /health 工具名/字体状态正确",
              js.get("tool") == TOOL_NAME
              and js.get("fonts", {}).get("hanzi") is True
              and js.get("fonts", {}).get("symbol") is True)

        # ---------------- 2. /capabilities
        code, cap, _ = _http(base, "GET", "/capabilities")
        cs = cap.get("character_set") or {}
        hanzi_str = cs.get("hanzi") or ""
        symbols_str = cs.get("symbols") or ""
        check("GET /capabilities 返回汉字 500 个",
              code == 200 and cs.get("hanzi_count") == 500
              and len(hanzi_str) == 500,
              "hanzi_count=%s len=%d" % (cs.get("hanzi_count"), len(hanzi_str)))
        check("GET /capabilities 返回符号 204 个",
              cs.get("symbol_count") == 204 and len(symbols_str) == 204,
              "symbol_count=%s len=%d" % (cs.get("symbol_count"),
                                          len(symbols_str)))
        check("capabilities 含页面/字号/段落 schema/端点表",
              cap.get("page", {}).get("a4_pt") == [A4_W, A4_H]
              and cap.get("size", {}).get("default_pt") == DEFAULT_SIZE_PT
              and isinstance(cap.get("paragraph_schema"), dict)
              and len(cap.get("endpoints") or []) >= 6,
              "endpoints=%d" % len(cap.get("endpoints") or []))

        # ---------------- 3. /prompt
        question = "求三相电路的线电流。"
        code, pr, _ = _http(base, "POST", "/prompt", {"question": question})
        prompt = pr.get("prompt") or ""
        check("POST /prompt 返回 200 且提示词非空",
              code == 200 and pr.get("ok") is True and len(prompt) > 1000,
              "prompt %d 字符" % len(prompt))
        check("提示词含动态字表（500 汉字 / 204 符号）",
              "500 个" in prompt and "204 个" in prompt
              and set(hanzi_str) <= set(prompt)
              and set(symbols_str) <= set(prompt),
              "汉字表/符号表完整注入")
        check("提示词含考试精炼约束",
              all(k in prompt for k in ("考试", "精炼", "复述题干",
                                        "emoji", "Markdown")))
        check("提示词含字库外字禁令",
              "字库外" in prompt and "禁止" in prompt
              and "改写" in prompt)
        check("提示词含题干与 JSON 结构化输出要求",
              question in prompt and '"segments"' in prompt
              and "answer_text" in prompt and "size_pt" in prompt)

        # ---------------- 4. /render → PNG
        sample_text = "三相电路的暂态过程分析\n解：线电流为22A"
        body = {"segments": [{"text": "三相电路的暂态过程分析"},
                             {"text": "解：线电流为22A"}],
                "format": "png"}
        code, js, _ = _http(base, "POST", "/render", body)
        png_data = b""
        png_size = None
        try:
            png_data = base64.b64decode(js.get("png_base64") or "",
                                        validate=True)
            from PIL import Image
            with Image.open(io.BytesIO(png_data)) as im:
                png_size = im.size
        except Exception:
            pass
        check("POST /render 返回合法 PNG base64",
              code == 200 and js.get("ok") is True
              and png_data[:8] == b"\x89PNG\r\n\x1a\n",
              "bytes=%d" % len(png_data))
        check("渲染尺寸为 2380×3368（A4 @ SCALE=4）",
              png_size == (2380, 3368), "size=%s" % (png_size,))
        cov = js.get("coverage") or {}
        expect_cov = symbols.han_coverage_summary(sample_text)
        check("coverage 与 symbols.han_coverage_summary 对齐",
              cov.get("han_summary") == expect_cov,
              "%s" % cov.get("han_summary"))
        check("coverage 汉字覆盖 100%、无缺字",
              cov.get("han_ratio") == 1.0 and not cov.get("han_missing")
              and cov.get("han_total", 0) > 0,
              "ratio=%s total=%s" % (cov.get("han_ratio"),
                                     cov.get("han_total")))
        if png_data:
            path = os.path.join(OUT_DIR, "ai_render.png")
            with open(path, "wb") as f:
                f.write(png_data)
            artifacts.append(path)

        # ---------------- 5. /render → PDF
        code, js, _ = _http(base, "POST", "/render",
                            {"segments": [{"text": "三相电路暂态过程分析"}],
                             "format": "pdf"})
        pdf_data = b""
        pdf_pages = 0
        try:
            pdf_data = base64.b64decode(js.get("pdf_base64") or "",
                                        validate=True)
            if pdf_data[:5] == b"%PDF-":
                import pymupdf as fitz
                doc = fitz.open(stream=pdf_data, filetype="pdf")
                pdf_pages = doc.page_count
                doc.close()
        except Exception:
            pass
        check("POST /render format=pdf 返回合法 PDF",
              code == 200 and js.get("ok") is True
              and pdf_data[:5] == b"%PDF-" and pdf_pages >= 1,
              "pages=%d bytes=%d" % (pdf_pages, len(pdf_data)))
        if pdf_data:
            path = os.path.join(OUT_DIR, "ai_render.pdf")
            with open(path, "wb") as f:
                f.write(pdf_data)
            artifacts.append(path)

        # ---------------- 6. /render_pdf（保留原内容）
        import pymupdf as fitz
        src = os.path.join(OUT_DIR, "ai_source.pdf")
        doc = fitz.open()
        pg = doc.new_page(width=A4_W, height=A4_H)
        pg.insert_text((72, 100), "Original printed text", fontsize=18)
        doc.save(src)
        doc.close()
        code, js, _ = _http(base, "POST", "/render_pdf",
                            {"pdf": src, "page": 0,
                             "segments": [{"text": "解：线电流为22A"}],
                             "write": True, "filename": "ai_overlay.pdf"})
        overlay_data = b""
        overlay_pages = 0
        overlay_text = ""
        try:
            overlay_data = base64.b64decode(js.get("pdf_base64") or "",
                                            validate=True)
            doc = fitz.open(stream=overlay_data, filetype="pdf")
            overlay_pages = doc.page_count
            overlay_text = doc[0].get_text()
            doc.close()
        except Exception:
            pass
        overlay_path = js.get("path") or ""
        check("POST /render_pdf 返回合法 PDF 且页数≥1",
              code == 200 and js.get("ok") is True
              and overlay_data[:5] == b"%PDF-" and overlay_pages >= 1,
              "pages=%d" % overlay_pages)
        check("render_pdf 保留源 PDF 原内容",
              "Original printed text" in overlay_text,
              "get_text=%r" % overlay_text.strip()[:40])
        check("render_pdf 写入 out/ 且文件名受控",
              bool(overlay_path) and os.path.exists(overlay_path)
              and os.path.dirname(os.path.abspath(overlay_path))
              == os.path.abspath(OUT_DIR),
              overlay_path)
        if overlay_path and os.path.exists(overlay_path):
            artifacts.append(overlay_path)

        # ---------------- 7. /answer
        answer_text = ("三相电路为星形联结\n"
                       "解：U相=220V，I=220/10=22A\n"
                       "因此线电流为22A")
        code, ans, _ = _http(base, "POST", "/answer",
                             {"question": question,
                              "answer_text": answer_text})
        segs = ans.get("segments") or []
        spec = ans.get("spec") or {}
        paras = spec.get("paragraphs") or []
        check("POST /answer 切出非空 segments",
              code == 200 and ans.get("ok") is True and len(segs) >= 3
              and all(s.get("text") for s in segs),
              "%d 段" % len(segs))
        check("/answer 生成可直接 render 的 spec（paragraphs/rect 完整）",
              len(paras) == len(segs)
              and all(len(p.get("rect") or []) == 4 for p in paras)
              and all(p.get("text") for p in paras),
              "paragraphs=%d" % len(paras))
        spec_path = os.path.join(OUT_DIR, "ai_answer_spec.json")
        with open(spec_path, "w", encoding="utf-8") as f:
            json.dump(ans, f, ensure_ascii=False, indent=2)
        artifacts.append(spec_path)

        # ---------------- 8. /render 接受 page_model spec
        code, js, _ = _http(base, "POST", "/render",
                            {"spec": spec, "format": "png"})
        ans_png = b""
        ans_size = None
        try:
            ans_png = base64.b64decode(js.get("png_base64") or "",
                                       validate=True)
            from PIL import Image
            with Image.open(io.BytesIO(ans_png)) as im:
                ans_size = im.size
        except Exception:
            pass
        check("POST /render 接受 page_model spec",
              code == 200 and js.get("ok") is True and ans_size == (2380, 3368),
              "size=%s" % (ans_size,))
        if ans_png:
            path = os.path.join(OUT_DIR, "ai_answer.png")
            with open(path, "wb") as f:
                f.write(ans_png)
            artifacts.append(path)

        # ---------------- 9. 错误处理
        code, js, _ = _http(base, "GET", "/nope")
        check("未知路由返回 404 + ok=false",
              code == 404 and js.get("ok") is False and js.get("error"))
        code, js, _ = _http(base, "GET", "/render")
        check("方法不对返回 405 + ok=false",
              code == 405 and js.get("ok") is False)
        code, js, _ = _http(base, "POST", "/render", raw=b"{bad json")
        check("非法 JSON 返回 400 + ok=false",
              code == 400 and js.get("ok") is False)
        code, js, _ = _http(base, "POST", "/render", {"segments": []})
        check("空 segments 返回 400 + ok=false",
              code == 400 and js.get("ok") is False)
        sysdir = os.environ.get("SystemRoot") or "C:\\Windows"
        code, js, _ = _http(base, "POST", "/render",
                            {"segments": [{"text": "abc"}],
                             "background": {"kind": "image",
                                            "path": os.path.join(sysdir,
                                                                 "win.ini")}})
        check("系统目录路径被白名单拒绝（403）",
              code == 403 and js.get("ok") is False,
              "path=%s" % os.path.join(sysdir, "win.ini"))
        code, js, _ = _http(base, "POST", "/render_pdf",
                            {"pdf": src, "page": 99,
                             "segments": [{"text": "abc"}]})
        check("PDF 页码越界返回 400 + ok=false",
              code == 400 and js.get("ok") is False)

        # ---------------- 10. CORS
        origin = "http://127.0.0.1:56959"
        code, js, hdrs = _http(base, "OPTIONS", "/render", origin=origin)
        check("CORS 预检允许本机 Origin",
              code == 204
              and hdrs.get("access-control-allow-origin") == origin,
              "allow-origin=%s" % hdrs.get("access-control-allow-origin"))
    finally:
        if own_server and httpd is not None:
            httpd.shutdown()
            httpd.server_close()
            if thread is not None:
                thread.join(timeout=3)

    passed = sum(1 for c in checks if c["ok"])
    total = len(checks)
    report_path = os.path.join(OUT_DIR, "ai_selftest_report.json")
    report = {"ok": passed == total, "passed": passed, "total": total,
              "base": base, "checks": checks,
              "artifacts": artifacts + [report_path],
              "version": VERSION,
              "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")}
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("-" * 78)
    for a in report["artifacts"]:
        size = os.path.getsize(a) if os.path.exists(a) else -1
        print("[artifact] %s (%d bytes)" % (a, size))
    print("结果: %d/%d %s" % (passed, total,
                              "全部通过" if passed == total else "有失败"))
    return 0 if passed == total else 1


# ======================================================================
# CLI
# ======================================================================
EXAMPLE_TEXT = """\
【启动服务】
  py -3 ai_api.py                    # 默认 127.0.0.1:8321
  py -3 ai_api.py --port 8322        # 换端口
  py -3 ai_api.py --self-test        # 全端点自测（临时端口）
  py -3 ai_api.py --example          # 打印本示例

【健康检查】
  curl http://127.0.0.1:8321/health
  curl http://127.0.0.1:8321/capabilities

【渲染 PNG（segments 简写）】
  curl -X POST http://127.0.0.1:8321/render ^
    -H "Content-Type: application/json" ^
    -d "{\\"segments\\":[{\\"text\\":\\"三相电路的暂态过程分析\\"},{\\"text\\":\\"解：线电流为22A\\"}],\\"format\\":\\"png\\"}"

【取提示词】
  curl -X POST http://127.0.0.1:8321/prompt ^
    -H "Content-Type: application/json" ^
    -d "{\\"question\\":\\"求三相电路的线电流。\\"}"

【叠加到 PDF 指定页】
  curl -X POST http://127.0.0.1:8321/render_pdf ^
    -H "Content-Type: application/json" ^
    -d "{\\"pdf\\":\\"模板数据\\\\example.pdf\\",\\"page\\":0,\\"segments\\":[{\\"text\\":\\"解：线电流为22A\\"}]}"

【Python 客户端】直接运行：py -3 ai_client_example.py
完整文档：docs/AI接口说明.md

【做题：作业页自动划题（PDF 或扫描图片）】
  curl -X POST http://127.0.0.1:8321/worksheet/analyze ^
    -H "Content-Type: application/json" ^
    -d "{\\"source\\":\\"_work/worksheet_samples/作业A.pdf\\"}"

【做题：答案写回手写作答 PDF（原内容保留）】
  curl -X POST http://127.0.0.1:8321/worksheet/answer ^
    -H "Content-Type: application/json" ^
    -d "{\\"source\\":\\"_work/worksheet_samples/作业A.pdf\\",\\"answers\\":{\\"1\\":\\"解：线电压等于相电压\\"},\\"write\\":true,\\"filename\\":\\"worksheet_answered.pdf\\"}"

【做题：生成逐题作答提示词】
  curl -X POST http://127.0.0.1:8321/worksheet/prompt ^
    -H "Content-Type: application/json" ^
    -d "{\\"source\\":\\"_work/worksheet_samples/作业A.pdf\\"}"
做题命令行：py -3 做题.py analyze/answer/run（见 docs/做题接口.md）
"""


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="ai_api.py",
        description="手写体生成器本地 AI 接口（本地 HTTP + CLI）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：py -3 ai_api.py --self-test")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help="监听地址，默认 127.0.0.1（只允许本机访问）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="监听端口，默认 8321")
    parser.add_argument("--self-test", action="store_true",
                        help="用临时端口起服务并跑全部端点自测")
    parser.add_argument("--example", action="store_true",
                        help="打印 curl / python 调用示例后退出")
    parser.add_argument("--version", action="version",
                        version="%(prog)s " + VERSION)
    args = parser.parse_args(argv)
    _utf8_console()
    if args.example:
        print(EXAMPLE_TEXT)
        return 0
    if args.self_test:
        return run_self_test()
    return _serve(args.host, args.port)


if __name__ == "__main__":
    sys.exit(main())

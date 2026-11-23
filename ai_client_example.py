# -*- coding: utf-8 -*-
"""ai_client_example.py — 手写体 AI 接口的 urllib 最小客户端示例。

用法
----
    py -3 ai_api.py                  # 先启动服务（默认 127.0.0.1:8321）
    py -3 ai_client_example.py       # 再跑本示例

也可以直接运行：如果 8321 端口没有服务，本脚本会自动在**本进程内**用
随机临时端口起一个服务演示完整流程（跑完自动关闭），所以一条命令就能看到
全部端点输出。想强制连已有服务可加 --no-auto-start。

演示的调用顺序
--------------
    GET  /health        健康检查 / 字体可用性
    GET  /capabilities  字表（汉字 500 / 符号 204）、字号、页面、端点
    POST /prompt        {question} → 内置系统提示词 + tool_schema
    POST /answer        {question, answer_text} → 自动切段 + page_model spec
    POST /render        spec → PNG（写 out/ai_client_render.png）+ PDF
    POST /render_pdf    把答案叠加到刚生成的 PDF 上（写 out/ai_client_overlay.pdf）

只依赖标准库 urllib；产物写在 源码/pdf-layer/out/ 下。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

OUT_DIR = os.path.join(HERE, "out")
DEFAULT_BASE = os.environ.get("AI_API_BASE", "http://127.0.0.1:8321")


def _utf8_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _ping(base, timeout=2.0):
    try:
        with urllib.request.urlopen(base + "/health", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def api(base, method, path, payload=None, timeout=120):
    """发一个 JSON 请求；HTTP 错误或 ok=false 时抛 RuntimeError。"""
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(base + path, data=data,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw, code = resp.read(), resp.status
    except urllib.error.HTTPError as e:
        raw, code = e.read(), e.code
    except urllib.error.URLError as e:
        raise RuntimeError("无法连接 %s：%s（先运行 py -3 ai_api.py）"
                           % (base, getattr(e, "reason", e)))
    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception:
        raise RuntimeError("响应不是 JSON（HTTP %d）：%r" % (code, raw[:200]))
    if code >= 400 or not obj.get("ok", False):
        raise RuntimeError("%s %s 失败（HTTP %d）：%s"
                           % (method, path, code, obj.get("error")))
    return obj


def _write(name, data):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    with open(path, "wb") as f:
        f.write(data)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="手写体 AI 接口 urllib 客户端示例")
    parser.add_argument("--base", default=DEFAULT_BASE,
                        help="服务地址，默认 %s" % DEFAULT_BASE)
    parser.add_argument("--no-auto-start", action="store_true",
                        help="连不上时不自动起临时服务，直接报错")
    args = parser.parse_args(argv)
    _utf8_console()

    base = args.base.rstrip("/")
    own_server = None
    t0 = time.perf_counter()
    try:
        health = _ping(base)
        if health is None:
            if args.no_auto_start:
                print("[client] %s 没有服务；请先运行 py -3 ai_api.py" % base)
                return 1
            print("[client] %s 没有服务，改为在本进程内用临时端口起一个演示服务……"
                  % base)
            import ai_api                       # 延迟导入：连得上就不需要
            ai_api._QUIET = True                # 演示时关掉逐请求访问日志
            httpd, thread, base = ai_api.start_background_server("127.0.0.1", 0)
            own_server = (httpd, thread)
            print("[client] 临时服务: %s" % base)
            health = _ping(base)
            if health is None:
                print("[client] 临时服务启动失败")
                return 1

        # ---------------- 1. /health
        print("\n[1/6] GET /health")
        print("      tool=%s version=%s python=%s"
              % (health.get("tool"), health.get("version"),
                 health.get("python")))
        print("      字体: 汉字=%s 符号=%s  字表: 汉字 %s / 符号 %s"
              % (health.get("fonts", {}).get("hanzi"),
                 health.get("fonts", {}).get("symbol"),
                 health.get("charset", {}).get("hanzi_count"),
                 health.get("charset", {}).get("symbol_count")))

        # ---------------- 2. /capabilities
        print("\n[2/6] GET /capabilities")
        cap = api(base, "GET", "/capabilities")
        cs = cap.get("character_set", {})
        print("      汉字 %d 个（字串 %d 字符），符号 %d 个（字串 %d 字符）"
              % (cs.get("hanzi_count"), len(cs.get("hanzi") or ""),
                 cs.get("symbol_count"), len(cs.get("symbols") or "")))
        print("      A4 = %s pt / %s px，默认字号 %s pt，行距 %s pt"
              % (cap["page"]["a4_pt"], cap["page"]["a4_px"],
                 cap["size"]["default_pt"], cap["line"]["default_pt"]))
        print("      汉字表前 40 字: %s" % (cs.get("hanzi") or "")[:40])
        print("      可用端点: %s"
              % ", ".join("%s %s" % (e["method"], e["path"])
                          for e in cap.get("endpoints", [])))

        # ---------------- 3. /prompt
        question = "求三相电路的线电流。"
        print("\n[3/6] POST /prompt  question=%r" % question)
        pr = api(base, "POST", "/prompt", {"question": question})
        prompt = pr["prompt"]
        print("      提示词 %d 字符；工具 %s"
              % (len(prompt), pr["tool_schema"]["name"]))
        print("      建议用法: messages=[{role:system, content:<prompt>}], "
              "tools=[%s]" % pr["tool_schema"]["name"])
        print("      提示词开头: %s" % prompt[:120].replace("\n", " "))

        # ---------------- 4. /answer
        answer_text = ("三相电路为星形联结\n"
                       "解：U相=220V，I=220/10=22A\n"
                       "因此线电流为22A")
        print("\n[4/6] POST /answer  自动切段")
        ans = api(base, "POST", "/answer",
                  {"question": question, "answer_text": answer_text})
        for i, seg in enumerate(ans["segments"], 1):
            print("      段%d rect=%-28s %s"
                  % (i, str(seg["rect"]), seg["text"]))
        spec = ans["spec"]
        spec_path = _write("ai_client_answer_spec.json",
                           json.dumps(ans, ensure_ascii=False,
                                      indent=2).encode("utf-8"))
        print("      已保存 spec: %s" % spec_path)

        # ---------------- 5. /render（PNG + PDF）
        print("\n[5/6] POST /render  把 spec 渲染成 PNG / PDF")
        rp = api(base, "POST", "/render", {"spec": spec, "format": "png"})
        png = base64.b64decode(rp["png_base64"])
        png_path = _write("ai_render.png", png)
        print("      PNG %s，%d×%d px，%d 字节"
              % (png_path, rp["image"]["width_px"],
                 rp["image"]["height_px"], len(png)))
        print("      尺寸: %d×%d px；覆盖率: %s"
              % (rp["image"]["width_px"], rp["image"]["height_px"],
                 rp["coverage"]["han_summary"]))
        print("      渲染耗时 %d ms；warnings=%d"
              % (rp["elapsed_ms"], len(rp.get("warnings") or [])))
        for w in (rp.get("warnings") or [])[:3]:
            print("        ! %s" % w)

        rx = api(base, "POST", "/render", {"spec": spec, "format": "pdf"})
        pdf_data = base64.b64decode(rx["pdf_base64"])
        pdf_path = _write("ai_client_render.pdf", pdf_data)
        print("      PDF %s，%d 页，%d 字节"
              % (pdf_path, rx["pages"], len(pdf_data)))

        # ---------------- 6. /render_pdf（叠加到刚生成的 PDF 上）
        print("\n[6/6] POST /render_pdf  叠加到 PDF 指定页（保留原内容）")
        ov = api(base, "POST", "/render_pdf",
                 {"pdf": pdf_path, "page": 0,
                  "segments": [{"text": "解：线电流为22A"}]})
        overlay = base64.b64decode(ov["pdf_base64"])
        ov_path = _write("ai_client_overlay.pdf", overlay)
        print("      %s，源 PDF %d 页 → 输出 %d 页；耗时 %d ms"
              % (ov_path, ov["pages"], ov["output_pages"],
                 ov["elapsed_ms"]))
        print("      覆盖率: %s" % ov["coverage"]["han_summary"])

        print("\n全部 6 个端点调用成功，共用时 %.2f 秒。"
              % (time.perf_counter() - t0))
        print("产物目录: %s" % OUT_DIR)
        return 0
    except RuntimeError as e:
        print("\n[client] 调用失败：%s" % e)
        return 1
    except Exception as e:                    # noqa: BLE001
        print("\n[client] 未预期错误：%s: %s" % (type(e).__name__, e))
        return 1
    finally:
        if own_server is not None:
            httpd, thread = own_server
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    sys.exit(main())

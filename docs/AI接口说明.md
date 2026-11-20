# 手写体生成器 AI 接口说明（v1.0.0）

> 适用文件：`_refs/pdf-layer/ai_api.py`、`ai_prompt.py`、`ai_client_example.py`
> 运行环境：Windows + `py -3`（Python 3.10+，实测 3.14）；除 Pillow / PyMuPDF / fontTools 外只用标准库。
> 默认地址：`http://127.0.0.1:8321`（只监听本机）

---

## 0. 这套接口是什么

把手写体渲染器（`page_model` + `symbols` + `hwrender`）包成一个**本地 HTTP 服务**，让 AI / 脚本可以：

1. 拿到「只能用哪些字」的约束（汉字 500、符号 204，从字体 cmap 动态读取）；
2. 拿到内置的答题提示词与 function-calling 工具描述；
3. 把 AI 输出的分段文字直接渲染成**手写笔迹 PNG / PDF**；
4. 把手写层**叠加到扫描件 / 已有 PDF 的指定页**，保留原内容；
5. 把一段答案文本自动切段、算好版面，生成可直接渲染的 `page_model` spec。

文件职责：

| 文件 | 作用 |
|---|---|
| `ai_api.py` | HTTP 服务 + CLI + 全部端点 + `--self-test` 自测 |
| `ai_prompt.py` | `allowed_charset()`、`SYSTEM_PROMPT`、`builtin_prompt()`、`tool_schema()` |
| `ai_client_example.py` | 纯 urllib 的最小客户端，演示 6 个端点，可直接运行 |
| `docs/AI接口说明.md` | 本文档 |

---

## 1. 快速开始

### 1.1 启动服务

```powershell
cd _refs\pdf-layer
py -3 ai_api.py                     # http://127.0.0.1:8321
py -3 ai_api.py --port 8322         # 换端口（GUI 按钮约定用 8321，别改）
py -3 ai_api.py --host 127.0.0.1    # 默认值；不要改成 0.0.0.0（无鉴权）
py -3 ai_api.py --example           # 仅打印 curl / python 调用示例
py -3 ai_api.py --self-test         # 临时端口起服务，跑全部端点自测
```

启动成功会打印（GUI 的「启动 AI 接口」按钮可以按这些行判断就绪）：

```
[ai_api] 手写体 AI 接口 v1.0.0（handwrite-render）
[ai_api] 服务已启动: http://127.0.0.1:8321
[ai_api] 端点: GET /health  GET /capabilities  POST /render  /render_pdf  /prompt  /answer
[ai_api] 文档: C:\Users\a\Desktop\手写体生成器\_refs\pdf-layer\docs\AI接口说明.md
[ai_api] 按 Ctrl+C 停止
```

服务是前台进程，`Ctrl+C` 停止。端口被占用时会返回明确错误并以退出码 2 结束：

```
[ai_api] 启动失败：[WinError 10048] 通常每个套接字地址(协议/网络地址/端口)只允许使用一次。
[ai_api] 端口 8321 已被占用。请关闭占用进程，或换端口：py -3 ai_api.py --port 8322
```

### 1.2 自测

```powershell
cd _refs\pdf-layer
py -3 ai_api.py --self-test
```

自测会在**随机临时端口**起一个独立服务，逐项验证：`/health`、`/capabilities`（汉字=500、符号=204）、`/prompt`（字表注入 + 考试精炼 + 字库外字禁令）、`/render`（合法 PNG、2380×3368、coverage 与 `symbols.han_coverage_summary` 一致）、`/render`（PDF）、`/render_pdf`（保留原 PDF 文字）、`/answer`（切段 + spec）、404/400/403/405 等错误分支、CORS 预检。自测产物写在 `out/`：

- `out/ai_render.png` / `out/ai_render.pdf`：空白 A4 手写渲染结果；
- `out/ai_overlay.pdf`：叠加了手写层的源 PDF（自测时源 PDF 是临时生成的）；
- `out/ai_answer.png` / `out/ai_answer_spec.json`：`/answer` 结果及其渲染；
- `out/ai_selftest_report.json`：每项检查的通过情况（机器可读）。

### 1.3 示例客户端

```powershell
cd _refs\pdf-layer
py -3 ai_client_example.py                  # 连 127.0.0.1:8321；连不上会自动起临时服务演示
py -3 ai_client_example.py --base http://127.0.0.1:8322
py -3 ai_client_example.py --no-auto-start  # 强制要求已有服务
```

示例依次调用 `/health` → `/capabilities` → `/prompt` → `/answer` → `/render`(PNG) → `/render`(PDF) → `/render_pdf`，产物写到 `out/ai_render.png`、`out/ai_client_render.pdf`、`out/ai_client_overlay.pdf`、`out/ai_client_answer_spec.json`。

---

## 2. 通用约定

- **协议**：HTTP/1.1，请求/响应全部 JSON，UTF-8；`Content-Type: application/json; charset=utf-8`。
- **成功**：HTTP 200，JSON 里 `"ok": true`。
- **失败**：`{"ok": false, "error": "人类可读原因", "code": 400, "hint": "可选建议"}`，HTTP 状态码与 `code` 一致。
- **CORS**：只对 `http://127.0.0.1:*`、`http://localhost:*`、`http://[::1]:*` 回显 `Access-Control-Allow-Origin`；其它 Origin 不发 CORS 头（浏览器会拦截）。
- **请求体上限** 8 MB；`segments` 上限 80 段；单次渲染默认超时 30 秒（可用环境变量调整，见 §8）。
- 渲染是**串行**的（字体/公式引擎有全局缓存，串行最稳）；并发请求排队，排队超时返回 503。

---

## 3. 端点总览

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/health` | 健康检查、字体/字表可用性、版本 |
| GET | `/capabilities` | 字表（汉字 500 / 符号 204）+ 字串、字号/行距、A4 尺寸、段落 schema、端点表 |
| POST | `/render` | segments 简写或 `page_model` spec → PNG/PDF（base64 或写 `out/`） |
| POST | `/render_pdf` | 渲染并叠加到源 PDF 的指定页，保留原内容 |
| POST | `/prompt` | `{question}` → 内置系统提示词（含动态字表）+ 工具 schema |
| POST | `/answer` | `{question, answer_text}` → 自动切段 + 可直接 POST `/render` 的 spec |
| GET | `/` | 接口自述（端点列表），可当连通性探测 |
| OPTIONS | 任意 | CORS 预检，返回 204 |

---

## 4. 端点详解

### 4.1 GET /health

```bash
curl http://127.0.0.1:8321/health
```

```json
{
  "ok": true,
  "tool": "handwrite-render",
  "version": "1.0.0",
  "python": "3.14.0",
  "fonts": {"hanzi": true, "symbol": true},
  "charset": {"hanzi_count": 500, "symbol_count": 204},
  "host": "127.0.0.1",
  "port": 8321,
  "out_dir": "C:\\Users\\a\\Desktop\\手写体生成器\\_refs\\pdf-layer\\out",
  "server_time": "2026-11-20T20:52:08"
}
```

### 4.2 GET /capabilities

返回完整能力清单。关键字段：

| 字段 | 含义 |
|---|---|
| `character_set.hanzi_count` / `symbol_count` | 汉字 500 / 符号 204（从 cmap 动态读，字体补字后自动变） |
| `character_set.hanzi` / `symbols` | 字表原文（可直接拼进提示词或做前端校验） |
| `fonts.han` / `fonts.symbol` | 字体目录、主字体、可用 passes |
| `size` | 字号范围 `8–32` pt，默认 `12.4` |
| `line` | 默认行距 `23.0` pt，约 `1.85 × 字号` |
| `page` | `a4_pt=[595,842]`、`a4_px=[2380,3368]`、`scale=4`、默认页边距 |
| `paragraph_schema` / `segment_schema` | 段落/分段字段说明（JSON Schema） |
| `endpoints` | 端点表 |
| `prompt.tool_schema` | `tool_schema()` 原文，可直接给模型 |

```bash
curl http://127.0.0.1:8321/capabilities
```

### 4.3 POST /render

**两种入参**，任选其一：

**A. segments 简写**（推荐给 AI 用；自动排版到 A4）：

```json
{
  "segments": [
    {"text": "三相电路的暂态过程分析", "size_pt": 12.4, "line_pt": 23.0, "align": "left"},
    {"text": "解：线电流为22A"}
  ],
  "background": {"kind": "blank"},
  "format": "png",
  "return_base64": true
}
```

**B. page_model spec**（GUI / 精确坐标用；字段与 `page_model.page_to_spec()` 完全一致）：

```json
{
  "spec": {
    "width_pt": 595, "height_pt": 842,
    "bg": {"kind": "blank", "path": "", "page": 0, "dpi": 200},
    "source_pdf": "",
    "paragraphs": [
      {"text": "三相电路的暂态过程分析", "rect": [56, 96, 539, 150], "size_pt": 12.4, "line_pt": 23.0, "align": "left"},
      {"text": "解：线电流为22A", "rect": [56, 170, 539, 224], "size_pt": 12.4, "line_pt": 23.0, "align": "left"}
    ]
  },
  "format": "png"
}
```

**可选参数**：

| 参数 | 类型 | 说明 |
|---|---|---|
| `format` | `png` / `pdf` | 默认 `png` |
| `return_base64` | bool | 默认 `true`；`false` 时只返回尺寸/耗时等元信息 |
| `write` | bool | `true` 时把结果写入 `out/`，响应多一个 `path` |
| `filename` | string | `write=true` 时指定文件名（只取 basename，强制 `.png/.pdf`，不能带路径） |
| `background` | object | `{"kind":"blank"|"image"|"pdf","path":"...","page":0,"dpi":200}`；kind=image 时按等比铺满 A4，kind=pdf 时页面尺寸取该 PDF 页 |
| `size_pt` / `line_pt` / `jitter` / `seed` | number/int | 全局默认值；segments 里可逐段覆盖 |
| `margin_left_pt` / `margin_right_pt` / `top_pt` / `bottom_pt` / `gap_pt` | number | 自动排版边距，默认 56 / 56 / 96 / 56 / 10 |

**响应（PNG）**：

```json
{
  "ok": true,
  "format": "png",
  "png_base64": "iVBORw0KGgoAAAANSUhEUg...",
  "image": {"width_px": 2380, "height_px": 3368, "scale": 4},
  "page_pt": [595.0, 842.0],
  "paragraphs": 2,
  "bytes": 76507,
  "warnings": [],
  "coverage": {
    "han_summary": "汉字覆盖 100%（16/16）",
    "han_total": 16, "han_covered": 16, "han_ratio": 1.0,
    "han_missing": [], "symbol_missing": [], "out_of_charset": [],
    "warnings": []
  },
  "elapsed_ms": 217
}
```

**响应（PDF）**：`format=pdf` 时用 `pdf_base64` / `pages` / `page_pt` 取代 `png_base64` / `image`；`background.kind=pdf` 且 `format=pdf` 时默认保留源 PDF 原内容。

**curl（PowerShell 用 `curl.exe`，注意引号转义）**：

```powershell
curl.exe -X POST http://127.0.0.1:8321/render `
  -H "Content-Type: application/json" `
  -d '{\"segments\":[{\"text\":\"三相电路的暂态过程分析\"},{\"text\":\"解：线电流为22A\"}],\"format\":\"png\"}'
```

**curl（bash / Git Bash）**：

```bash
curl -s -X POST http://127.0.0.1:8321/render \
  -H "Content-Type: application/json" \
  -d '{"segments":[{"text":"三相电路的暂态过程分析"},{"text":"解：线电流为22A"}],"format":"png"}' \
  | python -c "import sys,json,base64; d=json.load(sys.stdin); open('render.png','wb').write(base64.b64decode(d['png_base64'])); print(d['coverage'])"
```

**Python（urllib，无第三方依赖）**：

```python
import base64, json, urllib.request

body = {"segments": [{"text": "三相电路的暂态过程分析"},
                     {"text": "解：线电流为22A"}],
        "format": "png"}
req = urllib.request.Request(
    "http://127.0.0.1:8321/render",
    data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
    headers={"Content-Type": "application/json; charset=utf-8"},
    method="POST")
with urllib.request.urlopen(req, timeout=120) as resp:
    data = json.loads(resp.read().decode("utf-8"))
print(data["coverage"]["han_summary"], data["image"])
open("out/ai_render.png", "wb").write(base64.b64decode(data["png_base64"]))
```

### 4.4 POST /render_pdf

把渲染结果**叠加到源 PDF 的指定页**（`page` 从 0 开始），其余页原样保留；源 PDF 的原有文字/图形不会被覆盖，手写层画在最上面。

```json
{
  "pdf": "模板数据/某份扫描件.pdf",
  "page": 0,
  "segments": [{"text": "解：线电流为22A"}],
  "write": true,
  "filename": "answer_overlay.pdf"
}
```

也支持 `"spec": {...}`（`rect` 按该 PDF 页的 pt 坐标解释；页面尺寸以 PDF 页为准）。

**响应**：

```json
{
  "ok": true,
  "format": "pdf",
  "pdf_base64": "JVBERi0xLjQK...",
  "pages": 1,
  "page": 0,
  "output_pages": 1,
  "page_pt": [595.0, 842.0],
  "paragraphs": 1,
  "bytes": 50823,
  "warnings": [],
  "coverage": {"han_summary": "汉字覆盖 100%（5/5）", "han_total": 5, "han_covered": 5, "han_ratio": 1.0, "han_missing": [], "symbol_missing": [], "out_of_charset": [], "warnings": []},
  "path": "C:\\...\\_refs\\pdf-layer\\out\\answer_overlay.pdf",
  "elapsed_ms": 321
}
```

```bash
curl -s -X POST http://127.0.0.1:8321/render_pdf \
  -H "Content-Type: application/json" \
  -d '{"pdf":"模板数据/example.pdf","page":0,"segments":[{"text":"解：线电流为22A"}],"write":true}'
```

### 4.5 POST /prompt

返回内置系统提示词（已注入**真实字表**、字号/版面约定、JSON 输出要求）与工具 schema：

```bash
curl -s -X POST http://127.0.0.1:8321/prompt \
  -H "Content-Type: application/json" \
  -d '{"question":"求三相电路的线电流。"}'
```

```json
{
  "ok": true,
  "prompt": "你是「手写体答题器」……（完整提示词，约 2300 字符）",
  "character_set": {"hanzi_count": 500, "symbol_count": 204},
  "tool_schema": {"name": "render_handwriting", "description": "...", "parameters": {...}},
  "tool_schema_anthropic": {"name": "render_handwriting", "description": "...", "input_schema": {...}},
  "elapsed_ms": 1
}
```

请求体还可带 `{"segments":[...]}`，会作为「已有分段（供修订参考）」注入提示词。

### 4.6 POST /answer

把一段答案文本按行/空行自动切段，算好 A4 版面，返回可直接 POST `/render` 的 `spec` 和提示词：

```bash
curl -s -X POST http://127.0.0.1:8321/answer \
  -H "Content-Type: application/json" \
  -d '{"question":"求三相电路的线电流。","answer_text":"三相电路为星形联结\n解：U相=220V，I=220/10=22A\n因此线电流为22A"}'
```

```json
{
  "ok": true,
  "question": "求三相电路的线电流。",
  "segments": [
    {"text": "三相电路为星形联结", "size_pt": 12.4, "line_pt": 22.94, "align": "left", "rect": [56.0, 96.0, 539.0, 124.73]},
    {"text": "解：U相=220V，I=220/10=22A", "size_pt": 12.4, "line_pt": 22.94, "align": "left", "rect": [56.0, 134.73, 539.0, 163.46]},
    {"text": "因此线电流为22A", "size_pt": 12.4, "line_pt": 22.94, "align": "left", "rect": [56.0, 173.46, 539.0, 202.19]}
  ],
  "spec": {"width_pt": 595.0, "height_pt": 842.0, "bg": {"kind": "blank", "path": "", "page": 0, "dpi": 200}, "source_pdf": "", "paragraphs": [
    {"text": "三相电路为星形联结", "rect": [56.0, 96.0, 539.0, 124.73], "size_pt": 12.4, "line_pt": 22.94, "align": "left"},
    {"text": "解：U相=220V，I=220/10=22A", "rect": [56.0, 134.73, 539.0, 163.46], "size_pt": 12.4, "line_pt": 22.94, "align": "left"},
    {"text": "因此线电流为22A", "rect": [56.0, 173.46, 539.0, 202.19], "size_pt": 12.4, "line_pt": 22.94, "align": "left"}
  ]},
  "warnings": [],
  "prompt": "……",
  "elapsed_ms": 3
}
```

切段规则：优先按换行/空行；去掉 Markdown 列表符（`-`、`*`、`#`）与加粗记号；单行超过 90 字按句末标点再切，仍超长则硬切。文本里若本身是带 `segments` 的 JSON，会直接采用其中的 `text`。

门槛：答案段数 > 80 会返回 400（建议分页或精简）。

---

## 5. 把提示词/工具交给 AI 的推荐做法

### 5.1 最小可用流程

```python
import json, urllib.request
import ai_prompt

QUESTION = "求三相电路的线电流。"
messages = [
    {"role": "system", "content": ai_prompt.builtin_prompt(question=QUESTION)},
    {"role": "user", "content": QUESTION},
]
tools = [ai_prompt.tool_schema()]        # OpenAI 兼容；Anthropic 用 tool_schema_anthropic()
# → 调你的模型（DeepSeek / OpenAI / Claude …），模型应返回工具调用 render_handwriting
# → 把 tool_call.arguments（segments/format/background）原样 POST /render
```

要点：

1. **系统提示词用 `builtin_prompt(question=...)`**，不要直接手抄字表：它每次从字体 cmap 读最新字表（500/204），并附带考试精炼约束、公式子集、JSON 输出格式。
2. **工具用 `tool_schema()`**：`name=render_handwriting`，`parameters` 是标准 JSON Schema；Anthropic Messages API 用 `tool_schema_anthropic()`（`parameters` 换成 `input_schema`）。也可直接 `GET /capabilities` 取 `prompt.tool_schema`。
3. 模型返回的 JSON 里 `segments` 直接交给 `POST /render`（可原样带 `size_pt/line_pt/align`）。
4. **渲染前先校验字库**：`POST /render` 的响应里有 `coverage.out_of_charset` / `coverage.han_missing`。非空时说明模型违规或字库暂缺，可把缺字列表回填给模型让它改写后重试。
5. 模型没给 `segments` 而给了整段文本时，先 `POST /answer` 切段，再渲染它的 `spec`。

### 5.2 OpenAI 兼容（DeepSeek 等）示意

```python
resp = client.chat.completions.create(
    model="deepseek-chat",
    messages=[{"role": "system", "content": ai_prompt.builtin_prompt(question=q)},
              {"role": "user", "content": q}],
    tools=[ai_prompt.tool_schema()],
    tool_choice="auto",
)
call = resp.choices[0].message.tool_calls[0]
args = json.loads(call.function.arguments)   # {"segments":[...], "format":"png"}
# POST http://127.0.0.1:8321/render  body=args
```

### 5.3 Anthropic Messages API 示意

```python
resp = client.messages.create(
    model="claude-sonnet-4-5",
    system=ai_prompt.builtin_prompt(question=q),
    messages=[{"role": "user", "content": q}],
    tools=[ai_prompt.tool_schema_anthropic()],
)
block = next(b for b in resp.content if b.type == "tool_use")
# block.input = {"segments":[...], ...} → POST /render
```

### 5.4 提示词里已写死的约束（理解用）

- 汉字只允许 `allowed_charset()` 里的 500 字；字库外汉字必须换同义词/换说法/用符号公式表达/删修饰语；
- 数字、字母、标点、运算符只允许 204 个符号表内的字符（符号表含全部 ASCII 可打印字符、希腊字母、常用数学运算符，以及 `、。《》【】！（），：；？` 等全角符号）；
- 考试答题风格：先结论后步骤、不复述题干、不客套、不用 emoji / Markdown 装饰；
- 公式只用 `$...$` 行内子集：`\frac \sqrt \sum \prod \int \times \cdot \pm \approx \le \ge \ne \infty \angle \alpha..\omega \Delta \Omega ^ _`，不支持 TikZ / `\begin{}...\end{}` / 自定义宏；
- 输出严格 JSON：`{"answer_text":"...","segments":[{"text":"...","size_pt":12.4,"line_pt":23.0,"align":"left"}]}`。

---

## 6. 错误码

| HTTP | code | 何时出现 | 处理建议 |
|---|---|---|---|
| 200 | — | 成功 | `ok=true` |
| 204 | — | CORS 预检成功 | 浏览器自动处理 |
| 400 | 400 | JSON 非法、缺少 `segments/spec/pdf`、字段类型错、字号/页边距越界、PDF 页码越界、答案为空/段数过多 | 按 `error`/`hint` 修请求 |
| 403 | 403 | 读取系统目录（`C:\Windows`、`Program Files` 等） | 换项目内文件或用户自己的文件 |
| 404 | 404 | 未知端点；或读取的文件不存在 | 查 `/capabilities` 端点表 / 检查路径 |
| 405 | 405 | 方法不对（如 GET /render） | 用表里的方法 |
| 413 | 413 | 请求体 > 8 MB | 图片/PDF 传路径，不要上传二进制 |
| 500 | 500 | 渲染/导出内部异常（字体损坏、PDF 写入失败等） | 看服务端 stderr 的 traceback；缩小请求重试 |
| 503 | 503 | 渲染队列等待超时（并发太多） | 降并发或重试 |
| 504 | 504 | 单次渲染超时（默认 30 s） | 减小字号/段数，或调大 `HANDWRITE_AI_TIMEOUT` |

错误响应统一为：

```json
{"ok": false, "error": "segments 必须是非空数组", "code": 400, "hint": "..."}
```

---

## 7. 安全边界

这是**本机自用**接口，没有鉴权，设计上不对外网暴露：

- **只听 127.0.0.1**（`--host` 默认值）。改成 `0.0.0.0` 会打印提示但仍然无鉴权，请勿在不可信网络这样做。
- **读文件白名单**：允许项目目录（`手写体生成器\`）内任意存在的普通文件；项目外只允许用户显式传入且存在的普通文件；系统目录（`%SystemRoot%`、`Program Files`、`Program Files (x86)`、`ProgramData`）一律 403。相对路径依次按「服务进程 cwd → 项目根目录 → `_refs/pdf-layer`」解析。背景图/PDF 还会校验扩展名并尝试打开。
- **写文件只能落 `out/`**：`write=true` 时文件名只取 basename、非法字符替换为 `_`、扩展名强制为 `.png/.pdf`，不可能写到 `out/` 之外。
- **不做磁盘缓存/不上传**：所有渲染在本机内存完成，不联网。
- **大请求防护**：请求体 ≤ 8 MB；segments ≤ 80；渲染串行 + 30s 超时；单请求异常只影响该请求，服务继续运行。
- **CORS 只放行本机 Origin**，其他网页即使知道端口也拿不到响应头。
- 若需要给局域网/其他机器用，请自己加反向代理 + 鉴权，不要直接放开 `--host`。

---

## 8. 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `HANDWRITE_AI_TIMEOUT` | `30` | 单次渲染超时秒数，超时返回 504 |
| `HANDWRITE_AI_LOCK_WAIT` | `20` | 渲染队列排队上限秒数，超时返回 503 |
| `ZTIHAN_DIR` / `ZTISYM_DIR` | 自动探测 | 指定 `ZTi-Han` / `ZTi-Sym` 字体目录（`symbols.py` 支持） |
| `AI_API_BASE` | `http://127.0.0.1:8321` | `ai_client_example.py` 默认服务地址 |

---

## 9. 常见问题（FAQ）

**Q1：端口 8321 被占用怎么办？**
A：`py -3 ai_api.py --port 8322`，客户端用 `--base http://127.0.0.1:8322`。GUI 按钮约定默认 8321，改端口要同步改按钮参数。

**Q2：`/capabilities` 里字表不是 500 / 204？**
A：字表从字体 cmap 动态读。若显示 0 或更少，说明 `字体输出\ZTi-Han` / `ZTi-Sym` 缺失或字体损坏；检查 `GET /health` 的 `fonts`、`/capabilities` 的 `fonts.*.dir`。本项目实测 pass2 为 500 / 204。

**Q3：渲染结果里某些字变成别的字体/空白？**
A：该字不在 500 字表内，`hwrender` 会按回退字体链（老 ZTi → 龙藏 → 马善政）补，补不到就空白。看响应 `coverage.han_missing` / `out_of_charset`，让 AI 改写或换字。

**Q4：AI 返回了 Markdown 代码块包着的 JSON？**
A：`/render` 要求请求体是纯 JSON，先把代码块剥掉再 POST；或把整段模型输出作为 `answer_text` 传给 `/answer`（它会尝试解析 `{"segments":...}` 并去掉 `#`/`*` 等装饰）。

**Q5：`/render_pdf` 的 `page` 从几开始？**
A：从 0 开始，和 PyMuPDF 一致。越界返回 400 并带总页数。

**Q6：`/render` 返回 `warnings` 有「第 N 段超出框高」怎么办？**
A：自动排版给的高度是估算值；公式多/字号大时可能估少。可显式给 `segments[i].rect`，或加大 `gap_pt`、减小 `size_pt`。文字仍会照常绘制（不会崩），只是可能压到下一段。

**Q7：PowerShell 里 curl 的中文 JSON 乱码/转义失败？**
A：用 `curl.exe`（不是 `curl` 别名）并写 `\"` 转义；或直接用 `ai_client_example.py` / Python 发请求；控制台先 `$env:PYTHONIOENCODING='utf-8'`。

**Q8：GUI 怎么起这个服务？**
A：用 `subprocess.Popen([sys.executable, "ai_api.py"])`，工作目录 `_refs/pdf-layer`；轮询 `GET /health` 直到 200 即就绪；停止时 `terminate()`。端口/参数保持默认（`--host 127.0.0.1 --port 8321`），不要改约定。

**Q9：能一次渲染很多段/多页吗？**
A：当前一个请求只渲染**一页**（A4 或指定 PDF 页）。多页请按页循环调用 `/render` 或对多页 PDF 每页调 `/render_pdf`；单请求最多 80 段。

**Q10：修改了字体（补字/换 pass）后要重启服务吗？**
A：要。字表在进程内缓存（`allowed_charset()`），重启 `ai_api.py` 后 `/capabilities` 与提示词会反映新字表。

---

## 10. 附：JSON 字段速查

### `segments[]`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `text` | string | 是 | 一段手写文字；公式用 `$...$`；不要含换行符（要换行就拆段） |
| `size_pt` | number | 否 | 8–32，默认 12.4 |
| `line_pt` | number | 否 | 10–60，默认 `1.85×字号` ≈ 23.0 |
| `align` | string | 否 | `left` / `center` / `right`，默认 left |
| `rect` | number[4] | 否 | `[左,上,右,下]` pt；不给则由服务自动纵向排版 |

### `page_model` spec

| 字段 | 类型 | 说明 |
|---|---|---|
| `width_pt` / `height_pt` | number | 页面尺寸，默认 A4 595×842；PDF 背景时以 PDF 页为准 |
| `bg` | object | `{"kind":"blank|image|pdf","path":"...","page":0,"dpi":200}` |
| `source_pdf` | string | 导出 PDF 时把该 PDF 当底图/保留原内容 |
| `paragraphs[]` | object[] | `{text, rect:[l,t,r,b], size_pt, line_pt, align, jitter, seed, ink}` |

### `coverage`

| 字段 | 说明 |
|---|---|
| `han_summary` | 与 `symbols.han_coverage_summary()` 完全一致的摘要，如「汉字覆盖 100%（16/16）」 |
| `han_total` / `han_covered` / `han_ratio` | 汉字总数 / 命中数 / 覆盖率（0–1） |
| `han_missing` | 不在 ZTi-Han 字表的汉字列表（会走回退字体） |
| `out_of_charset` | 既不是汉字、也不在符号表里的字符列表（标点/emoji 等，可能渲染异常） |

---

## 11. 变更记录

- **v1.0.0（2026-11-20）**：首版。`ai_api.py`（6 端点 + `--self-test` + `--example`）、`ai_prompt.py`（动态字表 / SYSTEM_PROMPT / tool_schema）、`ai_client_example.py`、本文档。自测 28/28 通过；PNG 2380×3368、PDF/叠加 PDF 合法，`/render_pdf` 保留源 PDF 文字。

---

## 七、做题接口

> 本节是**增量扩展**：原有 6 个端点与 28 项自测保持不变。完整字段、请求/响应示例、
> 扫描件处理细节见 [`docs/做题接口.md`](做题接口.md)。

「做题」= 导入作业页（PDF 或扫描件）→ 自动划定每题题目区/答题区 → AI 逐题作答 →
手写排进各题答题区并导出叠加原 PDF 的作答结果。全程无 GUI 窗口，由 CLI 或 HTTP 驱动。

### 7.1 涉及文件

| 文件 | 作用 |
|---|---|
| `worksheet.py` | 数据层：`analyze()` 划题、`render_answers()` 手写排版、`load_spec/save_spec` |
| `做题.py` | headless CLI：`analyze` / `answer` / `run`（不 import tkinter） |
| `ai_api.py` | 新增 3 个 POST 端点 + `/capabilities.worksheet`（不破坏原 28 项自测） |
| `ai_prompt.py` | 新增 `worksheet_prompt(spec)`：逐题作答提示词 |
| `docs/做题接口.md` | 做题接口完整文档 |

### 7.2 三个新端点

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/worksheet/analyze` | `{source, page?, dpi?, numbers?}` → `{ok, spec, questions, crops?, warnings}` |
| POST | `/worksheet/answer` | `{source, spec?/layout?, answers, write?, filename?}` → `{ok, pdf_base64, pages, warnings, coverage}` |
| POST | `/worksheet/prompt` | `{source?/spec?}` → `{ok, prompt}`（逐题作答、只输出 `{"answers":{...}}`） |

`answers` 支持 `{"1": "解：…", "4.2.1": "解：…"}` 与 `[{"id": "Q1", "text": "…"}]`；
`analyze` 的 spec 可直接作为 `answer` 的 `spec` 入参复用。路径白名单、请求体 ≤ 8MB、
错误格式、渲染串行/超时全部沿用原有实现。

### 7.3 `/capabilities` 追加字段

原有键不变，末尾新增：

```json
{"worksheet": {
  "spec_version": "1.1",
  "endpoints": [{"method": "POST", "path": "/worksheet/analyze"}, "..."],
  "cli": "做题.py",
  "modes": ["pdf", "image"],
  "render": {"default_size_pt": 12.4, "min_size_pt": 8.0, "line_ratio": 1.85},
  "scanned_pdf": "PDF 页无文字层时自动按扫描件分析并返回 crops"
}}
```

### 7.4 CLI 快速开始

```powershell
cd _refs\pdf-layer
py -3 做题.py analyze 作业.pdf --out out/作业_layout.json
py -3 做题.py answer 作业.pdf --layout out/作业_layout.json ^
    --answers answers.json --out out/手写作答.pdf --png out/预览.png
py -3 做题.py run 作业.pdf --answers answers.json --out out/手写作答.pdf
```

扫描件没有文字层时题号用顺序号占位；要写真实题号（如 `4.2.1`）加
`--numbers "4.2.1,4.2.2,4.2.4,..."`。答案渲染默认 12.4pt（超过答题区高度才缩到
8pt，仍放不下给 warning 不截断），答案从答题区顶部、按答题区宽度书写。

做题模块的自测独立于 `ai_api.py --self-test`：

```powershell
py -3 ..\..\_work\test_worksheet.py
```

它同时回归验证 `py -3 ai_api.py --self-test` 仍为 **28/28**。


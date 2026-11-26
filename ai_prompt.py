# -*- coding: utf-8 -*-
"""ai_prompt.py — 手写体生成器的「AI 提示词 + 工具 schema」层。

给上层（ai_api.py / GUI / 外部 AI 客户端）提供这几样东西：

    allowed_charset()   从字体 cmap 动态读出的 (汉字表, 符号表)；
    reload_charset()    字库改了想立刻生效时调（缓存自带文件指纹，一般不用管）；
    is_han_char()       按 Unicode 码段判断是不是汉字（含扩展 A/B）；
    SYSTEM_PROMPT       中文系统提示词模板（字表/字号等由 builtin_prompt 注入）；
    builtin_prompt()    可以直接丢给大模型的完整系统提示词；
    worksheet_prompt()  做题用的逐题作答提示词；
    tool_schema()       OpenAI / Anthropic function-calling 兼容的工具描述。

设计约束
--------
1. **绝不硬编码字表或字数**：字表由 fontTools 读当前字体的 cmap 得到，缓存带
   文件指纹（mtime + size），补字、换库、原地重建 pass2.ttf 后自动失效；
   汉字判定覆盖基本区 + 扩展 A/B + 兼容区 + 々〇，扩展 A 的字不会被当成符号；
2. 提示词只描述渲染器**真的支持**的能力：$...$ 行内公式，\\frac \\sqrt \\sum
   \\int \\times \\cdot ^ _ 等，不支持 TikZ / 自定义宏 / \\begin 环境；
3. 输出必须是结构化 JSON，方便上层直接构造 page_model spec。

用法
----
    import ai_prompt

    han, sym = ai_prompt.allowed_charset()
    print(len(han), len(sym))                 # 500 204

    prompt = ai_prompt.builtin_prompt(question="求三相电路的线电流。")
    tools = [ai_prompt.tool_schema()]         # 直接塞进 chat completion 的 tools

命令行自检（不依赖 ai_api.py）::

    py -3 ai_prompt.py
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import hwrender as H            # noqa: E402  (只取默认字号/行距/墨色常量)
import symbols                  # noqa: E402

# ------------------------------------------------------------------ 字库路径
#
# 字库是「用户可以自己改」的：字库工作台补字、换库、原地重建 pass2.ttf 都会让
# cmap 变。所以缓存不能按路径永久记住，必须带上文件指纹（mtime + size）：
# 同名文件被覆盖时也能立刻重读，不用重启程序。
_CMAP_CACHE = {}                # abs path -> ((mtime_ns, size), frozenset)
_CHARSET_CACHE = None           # (字体指纹, (frozenset 汉字, frozenset 符号))

# CJK 汉字所在的 Unicode 码段。只认基本区会把用户补写的扩展 A 字判成「符号」，
# 这里与 fontlib.charsets 允许选字的范围保持一致。
_CJK_RANGES = (
    (0x3400, 0x4DBF),      # 扩展 A（字库工作台可选）
    (0x4E00, 0x9FFF),      # 基本区
    (0xF900, 0xFAFF),      # 兼容汉字
    (0x20000, 0x2A6DF),    # 扩展 B
    (0x2A700, 0x2EBEF),    # 扩展 C–F
    (0x30000, 0x323AF),    # 扩展 G–H
)
_CJK_SINGLE = frozenset((0x3005, 0x3007, 0x303B))   # 々 〇 〻


def is_han_char(ch):
    """这个字符算不算汉字（看码段，不看字库有没有）。"""
    if not ch:
        return False
    cp = ord(ch[0])
    return cp in _CJK_SINGLE or any(a <= cp <= b for a, b in _CJK_RANGES)


def _is_cjk(ch):
    """兼容旧名：等价于 is_han_char()。"""
    return is_han_char(ch)


def _stamp(path):
    """字体文件指纹（mtime + 大小）；路径为空或读不到返回 None。"""
    if not path:
        return None
    try:
        st = os.stat(path)
    except (OSError, TypeError):
        return None
    return (st.st_mtime_ns, st.st_size)


def _cmap(path):
    """字体 Unicode 覆盖集合；读不到返回空集合（不抛异常）。

    按「路径 + 文件指纹」缓存：用户原地重建同名字体后自动失效。
    读失败不缓存，下次调用还会重试（建库过程中字体可能正被占用）。
    """
    if not path:
        return set()
    key = os.path.abspath(path)
    stamp = _stamp(path)
    cached = _CMAP_CACHE.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    try:
        from fontTools.ttLib import TTFont
        chars = set(TTFont(path).getBestCmap() or {})
    except Exception:
        return set()
    _CMAP_CACHE[key] = (stamp, chars)
    return chars


def han_font_path():
    """汉字主字体路径（通常 pass2.ttf）；不可用返回 None。"""
    return symbols.han_main_font()


def symbol_font_path():
    """符号主字体路径（pass2 优先，缺了退到任一遍）；不可用返回 None。"""
    fn = getattr(symbols, "sym_main_font", None)
    if callable(fn):
        try:
            p = fn()
        except Exception:
            p = None
        if p and os.path.exists(p):
            return p
    d = symbols.sym_dir()
    if not d:
        return None
    for name in ("pass2.ttf", "pass1.ttf", "pass3.ttf", "pass4.ttf"):
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return None


# 字库索引.json 的指纹：换库/新增库时注册表要跟着立刻生效，
# 不然 symbols 会一直返回旧库目录。
_REGISTRY_WATCH = {"stamp": None}


def _refresh_registry():
    """字体输出/字库索引.json 变了就强制重读注册表（不存在就跳过）。"""
    try:
        import fontlib.registry as registry
        path = registry.registry_path()
    except Exception:
        return
    stamp = _stamp(path)
    if _REGISTRY_WATCH["stamp"] == stamp:
        return
    try:
        registry.load(force=True)
    except Exception:
        return
    _REGISTRY_WATCH["stamp"] = stamp


def _font_signature():
    """当前这套字体的指纹；字库一变它就变，缓存跟着失效。"""
    _refresh_registry()
    return tuple((p, _stamp(p)) for p in (han_font_path(), symbol_font_path()))


def reload_charset():
    """丢掉字表缓存，下次 allowed_charset() 重读字体与字库注册表。

    字库工作台补字/换库后调一次，整个程序不用重启。
    """
    global _CHARSET_CACHE
    _CHARSET_CACHE = None
    _CMAP_CACHE.clear()
    _REGISTRY_WATCH["stamp"] = None


def allowed_charset():
    """返回 (hanzi_set, symbol_set)：从字体 cmap 动态读取。

    汉字表 = 汉字主字体 cmap 里的 CJK 字符（含扩展 A 等码段）；
    符号表 = 符号主字体 cmap 里的其它字符（ASCII、希腊字母、运算符、标点等）。

    缓存带字体指纹：补字、换库、原地重建字体后自动重读，不会像以前那样
    整个进程一直用旧字表。每次返回新的 set 副本，调用方改它不会污染缓存。
    """
    global _CHARSET_CACHE
    key = _font_signature()
    if _CHARSET_CACHE is None or _CHARSET_CACHE[0] != key:
        han, sym = set(), set()
        p = han_font_path()
        if p:
            han = {chr(cp) for cp in _cmap(p) if is_han_char(chr(cp))}
        p = symbol_font_path()
        if p:
            sym = {chr(cp) for cp in _cmap(p)} - han
        _CHARSET_CACHE = (key, (frozenset(han), frozenset(sym)))
    han, sym = _CHARSET_CACHE[1]
    return set(han), set(sym)


def charset_strings():
    """返回 (汉字串, 符号串)：按 Unicode 码位排序，便于稳定注入提示词/接口。"""
    han, sym = allowed_charset()
    return "".join(sorted(han)), "".join(sorted(sym))


def _wrap_chars(s, per_line):
    """把一长串字符按固定长度折行，避免提示词里出现一条几万像素的巨行。"""
    s = s or ""
    if not s:
        return "（空）"
    return "\n".join(s[i:i + per_line] for i in range(0, len(s), per_line))


# ------------------------------------------------------------------ 系统提示词
SYSTEM_PROMPT = r"""你是「手写体答题器」：根据题干写出答案，答案会被直接渲染成手写笔迹。

【一、输出：只给一个 JSON 对象】
{"answer_text": "纯文本答案，供人工确认", "segments": [{"text": "第一段", "size_pt": {default_size_pt}, "line_pt": {default_line_pt}, "align": "left"}]}
- 每个分点、每个公式行单独一段，一般 2–8 段；text 里不能有换行符。
- size_pt：正文 {default_size_pt}，注释用 {size_min_pt}–{default_size_pt}；
  line_pt 取字号的 1.6–1.9 倍（正文默认 {default_line_pt}）；align 一律 left。
- 不要代码块，不要 JSON 以外的任何文字。

【二、用字：以字表为准】
- 汉字以「汉字表」（{han_count} 个）为准，数字、字母、标点、运算符以「符号表」
  （{sym_count} 个）为准。表外的字会回退到别的字库渲染（风格可能不统一、个别会留白），
  能换说法就换，别硬写生僻字；emoji 与生僻标点不用。
- 写完逐字核对一遍字表。

【三、公式：写 $...$，不要用汉字名或裸符号代替】
- 根号：$\sqrt{被开方数}$，被开方数整个放进花括号 —— $\sqrt{3}$、$\sqrt{P^2+Q^2}$。
- 除法：$\frac{分子}{分母}$（上下结构）—— $\frac{U}{R}$、$\frac{220}{10}$；不要写 220/10。
- 幂与下标：$U^2$、$U_{相}$、$I^2R$、$\int_0^t i\,dt$。
- 可用：\frac \sqrt \sum \prod \int \times \cdot \pm \approx \le \ge \ne \infty \angle
  \alpha~\omega \Delta \Omega 和 ^ _；不要 \begin 环境、TikZ、自定义宏、\text{}、$$...$$。
- 公式里的中文（如 $U_{线}$）按普通汉字渲染，只能用字表内的字。

【四、像考试答卷】
- 先给结论或关键步骤：计算题给式子与结果，证明题给推理链。
- 精炼：不要客套话，不要复述题干，不要解释背景，不要写总结性废话。
- 分点用 1. 2. 3. 或 (1) (2) (3)，每个分点一到两行，行文短。
- 不用 Markdown 装饰（# * - > 表格 加粗），不用 emoji。
- 公式是答案的一部分，直接写式子，不要「根据公式可得」这类空话。

【汉字表（{han_count} 个，从当前字体 cmap 动态注入）】
{hanzi_list}

【符号表（{sym_count} 个，从当前字体 cmap 动态注入）】
{symbol_list}

{question}
"""

# 提示词里用到的字号/版面约定；和 hwrender 保持一致，GUI 改默认值时这里跟着改。
SIZE_MIN_PT = 8.0
SIZE_MAX_PT = 32.0
DEFAULT_SIZE_PT = float(H.DEFAULT_SIZE_PT)
DEFAULT_LINE_PT = float(H.DEFAULT_LINE_PT)
LINE_RATIO = 1.85
PAGE_W_PT, PAGE_H_PT = 595.0, 842.0


def _question_block(question=None, segments=None):
    """拼【题干】段；没有题干也明确说一声，免得模型自己编题。"""
    q = str(question).strip() if question is not None else ""
    block = "【题干】\n" + (q or "（未提供题干：只输出答案 JSON，不要复述题目。）")
    if segments:
        if isinstance(segments, str):
            js = segments.strip()
        else:
            try:
                js = json.dumps(segments, ensure_ascii=False, indent=2)
            except Exception:
                js = str(segments)
        block += "\n\n【已有分段（可整段替换）】\n" + js
    return block + "\n\n请直接输出 JSON。"


def builtin_prompt(question=None, segments=None):
    """生成完整中文系统提示词：真实字表 + 版面约定 + JSON 输出要求。

    参数
    ----
    question : 可选，题干；提供后会替换模板里的 {question} 占位。
    segments : 可选，已有分段（dict/list/JSON 字符串均可），作为修订参考注入。
    """
    han, sym = allowed_charset()
    han_str, sym_str = charset_strings()
    mapping = {
        "{han_count}": str(len(han)),
        "{sym_count}": str(len(sym)),
        "{hanzi_list}": (_wrap_chars(han_str, 40)
                         if han_str else "（汉字库缺失：请确认 字体输出\\ZTi-Han 是否存在）"),
        "{symbol_list}": (_wrap_chars(sym_str, 60)
                          if sym_str else "（符号库缺失：请确认 字体输出\\ZTi-Sym 是否存在）"),
        "{default_size_pt}": "%g" % DEFAULT_SIZE_PT,
        "{default_line_pt}": "%g" % DEFAULT_LINE_PT,
        "{size_min_pt}": "%g" % SIZE_MIN_PT,
        "{size_max_pt}": "%g" % SIZE_MAX_PT,
        "{question}": _question_block(question, segments),
    }
    out = SYSTEM_PROMPT
    for key, val in mapping.items():
        out = out.replace(key, val)
    return out


# ------------------------------------------------------------------ 工具 schema
def segment_schema():
    """segment 的 JSON Schema（供 tool_schema / capabilities 复用）。"""
    return {
        "type": "object",
        "properties": {
            "text": {"type": "string",
                     "description": "这一段手写文字；公式用 $...$ 包住，不含换行符"},
            "size_pt": {"type": "number", "minimum": SIZE_MIN_PT,
                        "maximum": SIZE_MAX_PT, "default": DEFAULT_SIZE_PT,
                        "description": "字号（pt），正文 %g" % DEFAULT_SIZE_PT},
            "line_pt": {"type": "number", "minimum": 10, "maximum": 60,
                        "default": DEFAULT_LINE_PT,
                        "description": "行距（pt），约字号的 1.6–1.9 倍"},
            "align": {"type": "string", "enum": ["left", "center", "right"],
                      "default": "left", "description": "对齐方式"},
        },
        "required": ["text"],
        "additionalProperties": False,
    }


def background_schema():
    return {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["blank", "image", "pdf"],
                     "default": "blank", "description": "背景类型"},
            "path": {"type": "string", "description": "图片/PDF 路径（kind 非 blank 时必填）"},
            "page": {"type": "integer", "minimum": 0, "default": 0,
                     "description": "PDF 背景的页码，从 0 开始"},
        },
        "required": ["kind"],
        "additionalProperties": False,
    }


def tool_schema():
    """OpenAI / Anthropic function-calling 兼容的工具描述。

    返回 dict 形如 {"name", "description", "parameters"}；Anthropic 的
    tools 需要把 parameters 换成 input_schema，可用 tool_schema_anthropic()。
    """
    han, sym = allowed_charset()
    return {
        "name": "render_handwriting",
        "description": (
            "把手写风格的答案渲染成 PNG/PDF：透明笔迹层可直接叠在图片、扫描件或"
            "指定 PDF 页的原版面上（不破坏原内容）。segments 是分段文字与字号/行距，"
            "返回 base64、汉字覆盖率与缺字警告。字库仅 %d 汉字 + %d 符号，"
            "表外字符会回退或渲染空白。" % (len(han), len(sym))
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "segments": {
                    "type": "array", "minItems": 1,
                    "maxItems": 60,
                    "description": "按逻辑分点/分行的文字段：一段话或一行公式",
                    "items": segment_schema(),
                },
                "background": background_schema(),
                "format": {"type": "string", "enum": ["png", "pdf"],
                           "default": "png",
                           "description": "输出格式：png，或 pdf（保留源页原内容）"},
                "return_base64": {"type": "boolean", "default": True,
                                  "description": "是否返回 base64（false 只给路径与元信息）"},
            },
            "required": ["segments"],
            "additionalProperties": False,
        },
    }


def tool_schema_anthropic():
    """Anthropic Messages API 的 tools 条目（parameters → input_schema）。"""
    s = tool_schema()
    return {"name": s["name"], "description": s["description"],
            "input_schema": s["parameters"]}


def prompt_meta():
    """给 /capabilities、前端展示用的提示词元信息。"""
    han, sym = allowed_charset()
    return {
        "hanzi_count": len(han),
        "symbol_count": len(sym),
        "default_size_pt": DEFAULT_SIZE_PT,
        "default_line_pt": DEFAULT_LINE_PT,
        "size_range_pt": [SIZE_MIN_PT, SIZE_MAX_PT],
        "page_pt": [PAGE_W_PT, PAGE_H_PT],
        "tool_name": tool_schema()["name"],
    }


# ======================================================================
# 做题（worksheet）提示词
# ======================================================================
WORKSHEET_CHARS_PER_LINE_REF = 13.3   # 12.4pt 手写汉字平均步进（pt/字）
WORKSHEET_SIZE_PT = 12.4              # 做题默认字号，用于估算可写字数
WORKSHEET_LINE_RATIO = 1.85           # 行距 = 字号 × 1.85


def _worksheet_question_lines(spec):
    """把 spec 的题目摊平成 [(题号, 文字, answer_lines, 每行字数, 总字数)]。

    answer_mode=blank 时答案写到新插的空白页上，不应再用原页那个可能只有
    一两行的 answer_rect 限制 AI；这里按整页空白答题页的可写量给预算。
    """
    blank = str((spec or {}).get("answer_mode") or "").strip().lower() == "blank"
    out = []
    for pg in (spec or {}).get("pages") or []:
        for q in pg.get("questions") or []:
            rect = q.get("answer_rect") or [56.0, 120.0, 539.0, 300.0]
            try:
                width = max(1.0, float(rect[2]) - float(rect[0]))
                height = max(1.0, float(rect[3]) - float(rect[1]))
            except (TypeError, ValueError, IndexError):
                width, height = 483.0, 180.0
            if blank:
                page_w = float(pg.get("width_pt") or 595.0)
                page_h = float(pg.get("height_pt") or 842.0)
                width = max(120.0, page_w - 112.0)      # 空白页左右各 56pt
                height = max(60.0, page_h - 104.0 - 8.0)  # 上 56 / 下 48
                lines = max(1, int(height / (WORKSHEET_SIZE_PT *
                                             WORKSHEET_LINE_RATIO)))
            else:
                lines = int(q.get("answer_lines") or 0)
                if lines <= 0:
                    lines = max(1, int(height / (WORKSHEET_SIZE_PT *
                                                 WORKSHEET_LINE_RATIO)))
            per_line = max(1, int(width / WORKSHEET_CHARS_PER_LINE_REF))
            out.append({
                "number": str(q.get("number") or q.get("id") or ""),
                "id": str(q.get("id") or ""),
                "text": q.get("text"),
                "lines": lines,
                "per_line": per_line,
                "max_chars": lines * per_line,
                "page": pg.get("page", 0),
            })
    return out


def worksheet_prompt(spec=None, include_charset=True, max_questions=80):
    """根据作业页 spec 生成「逐题作答」提示词（做题接口 /worksheet/prompt 用）。

    与 builtin_prompt 的区别：这里一次给多道题，要求模型只输出
    ``{"answers": {"题号": "答案文本"}}``，并把每题的「答题区可写行数 /
    每行字数 / 可写字数上限」注入，避免答案写不下。

    参数
    ----
    spec            : worksheet.analyze() 的返回值；None 时输出规则模板。
    include_charset : 是否注入完整汉字/符号表（默认 True；表很长但约束最强）。
    max_questions   : 最多注入多少题（防提示词过长），默认 80。
    """
    han, sym = allowed_charset()
    han_str, sym_str = charset_strings()
    items = _worksheet_question_lines(spec)[:max(1, int(max_questions))]
    has_crop = any(bool(q.get("crop_png_base64"))
                   for pg in (spec or {}).get("pages") or []
                   for q in pg.get("questions") or [])

    # 只有真的带了截图才提截图，避免绝大多数题目被无关指令干扰
    crop_note = ("- 题干可能是 null（扫描页无文字层）：这类题的题干以题目区域截图给出，"
                 "按截图理解题意后作答，仍然只输出 JSON。\n" if has_crop else "")

    head = r"""你是「手写作业答题器」：下面是一份作业的版面分析结果，请逐题作答；
答案会被渲染成手写笔迹，写进每题答题区。

【一、输出：只给一个 JSON 对象】
{"answers": {"1": "第一题的答案", "4.2.1": "……"}}
- 键原样使用【题目清单】里的题号，不编造、不漏题。
- 值是纯文本：不含换行符、代码块、Markdown（# * - 表格 加粗）、emoji。
- 不要客套话，不要复述题干，不要解释题目背景。
{crop_note}
【二、用字：以字表为准】
- 汉字以「汉字表」（{han_count} 个）为准，数字、字母、标点、运算符以「符号表」
  （{sym_count} 个）为准。表外的字会回退到别的字库渲染（风格可能不统一、个别会留白），
  能换说法就换，别硬写生僻字；emoji 与生僻标点不用。
- 写完逐字核对一遍字表。

【三、公式：写 $...$，不要用汉字名或裸符号代替】
- 根号：$\sqrt{被开方数}$，被开方数整个放进花括号 —— $\sqrt{3}$、$\sqrt{P^2+Q^2}$；
  不要只写一个 √ 再接字（那样根号只盖住一个字），也不要写「根号3」。
- 除法：$\frac{分子}{分母}$（上下结构）—— $\frac{U}{R}$、$\frac{220}{10}$；不要写 220/10。
- 幂与下标：$U^2$、$U_{相}$、$I^2R$。
- 可用：\frac \sqrt \sum \prod \int \times \cdot \pm \approx \le \ge \ne \infty \angle
  \alpha~\omega \Delta \Omega 和 ^ _；不要 \begin 环境、TikZ、自定义宏、\text{}、$$...$$。
- 只把算式包进 $...$；纯数字与单位照写（22A，不写 $22A$）。
- 不要用「根号、除以、平方、等于」等汉字代替 √ / ^ = 等符号（题目要求文字叙述时除外）。

【四、像学生写作业】
- 每题以「解：」开头；有数值或结论的题，结尾用「答：……」收口。
- 先一句依据（如「由换路定则可知」），再写公式与代入，再给结果和单位；不要只堆等式。
- 用「，」「。」自然断句，不要压成一行短句；也不要写总结废话。
- 每题不超过【题目清单】给的「可写字数」，宁短勿长（超了会缩字号或溢出）。
- 答案按答题区宽度自动换行：不要自己加缩进、分栏或强制换行。
"""
    head = head.replace("{crop_note}", crop_note)

    if include_charset:
        head += "\n【汉字表（{han_count} 个，从当前字体 cmap 动态注入）】\n"
        head += (_wrap_chars(han_str, 40) if han_str
                 else "（汉字库缺失：请确认 字体输出\\ZTi-Han 是否存在）")
        head += "\n\n【符号表（{sym_count} 个，从当前字体 cmap 动态注入）】\n"
        head += (_wrap_chars(sym_str, 60) if sym_str
                 else "（符号库缺失：请确认 字体输出\\ZTi-Sym 是否存在）")
        head += "\n"

    if items:
        lines = ["\n【题目清单（共 %d 题，按此顺序作答）】" % len(items)]
        for i, it in enumerate(items, 1):
            lines.append("第 %d 题  题号 %s" % (i, it["number"] or it["id"]))
            text = (it["text"] or "").strip()
            if text:
                lines.append("  题干：%s" % text)
            else:
                lines.append("  题干：（无文字层，见题目区域截图）")
            lines.append("  答题区：约 %d 行 × 每行约 %d 字 ≈ %d 字"
                         % (it["lines"], it["per_line"], it["max_chars"]))
        lines.append("")
        lines.append("请直接输出 {\"answers\": {...}}。")
        body = "\n".join(lines)
    else:
        body = ("\n【题目清单】\n（未提供题目：只输出空对象 {\"answers\": {}}，"
                "不要编造题目。）\n")

    out = head + body
    mapping = {
        "{han_count}": str(len(han)),
        "{sym_count}": str(len(sym)),
    }
    for key, val in mapping.items():
        out = out.replace(key, val)
    return out



if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    han, sym = allowed_charset()
    print("汉字表 %d 个；符号表 %d 个" % (len(han), len(sym)))
    print("汉字字体:", han_font_path())
    print("符号字体:", symbol_font_path())
    prompt = builtin_prompt(question="求三相电路的线电流。")
    print("提示词长度: %d 字符" % len(prompt))
    print("提示词前 300 字:\n" + prompt[:300])
    print("工具:", json.dumps(tool_schema(), ensure_ascii=False)[:300], "...")

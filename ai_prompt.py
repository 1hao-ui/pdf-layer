# -*- coding: utf-8 -*-
"""ai_prompt.py — 手写体生成器的「AI 提示词 + 工具 schema」层。

给上层（ai_api.py / GUI / 外部 AI 客户端）提供三样东西：

    allowed_charset()   从字体 cmap 动态读出的 (汉字表, 符号表)；
    SYSTEM_PROMPT       中文系统提示词模板（字表/字号等由 builtin_prompt 注入）；
    builtin_prompt()    可以直接丢给大模型的完整系统提示词；
    tool_schema()       OpenAI / Anthropic function-calling 兼容的工具描述。

设计约束
--------
1. **绝不硬编码字表**：ZTi-Han / ZTi-Sym 的字表由 fontTools 读 cmap 得到，
   字体更新（补字、换 pass）后提示词自动跟着变；
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
_CHARSET_CACHE = None           # ((frozenset 汉字), (frozenset 符号))
_CMAP_CACHE = {}


def _cmap(path):
    """字体 Unicode 覆盖集合；读不到返回空集合（不抛异常）。"""
    if not path:
        return set()
    key = os.path.abspath(path)
    cached = _CMAP_CACHE.get(key)
    if cached is None:
        try:
            from fontTools.ttLib import TTFont
            cached = set(TTFont(path).getBestCmap() or {})
        except Exception:
            cached = set()
        _CMAP_CACHE[key] = cached
    return cached


def _is_cjk(ch):
    return "\u4e00" <= ch <= "\u9fff"


def han_font_path():
    """ZTi-Han 主字体路径（通常 pass2.ttf）；不可用返回 None。"""
    return symbols.han_main_font()


def symbol_font_path():
    """ZTi-Sym 符号字体路径（优先 pass2.ttf）；不可用返回 None。"""
    d = symbols.sym_dir()
    if not d:
        return None
    for name in ("pass2.ttf", "pass1.ttf", "pass3.ttf", "pass4.ttf"):
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return None


def allowed_charset():
    """返回 (hanzi_set, symbol_set)：从字体 cmap 动态读取，结果缓存。

    汉字表 = ZTi-Han 主字体 cmap 里的基本区汉字；
    符号表 = ZTi-Sym 主字体 cmap 里的全部字符（ASCII 标点/数字/字母、
             希腊字母、运算符、中文标点等）。

    每次返回新的 set 副本，调用方可以随意修改，不会污染缓存。
    """
    global _CHARSET_CACHE
    if _CHARSET_CACHE is None:
        han, sym = set(), set()
        p = han_font_path()
        if p:
            han = {chr(cp) for cp in _cmap(p) if 0x4E00 <= cp <= 0x9FFF}
        p = symbol_font_path()
        if p:
            sym = {chr(cp) for cp in _cmap(p)} - han
        _CHARSET_CACHE = (frozenset(han), frozenset(sym))
    han, sym = _CHARSET_CACHE
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
SYSTEM_PROMPT = r"""你是「手写体答题器」：根据题干生成一份可以直接渲染成手写笔迹的答案。

【第一优先级：只能使用字库内的字符】
1. 你输出的每一个汉字都必须来自下面「汉字表」（共 {han_count} 个）。字表外的汉字一律禁止输出，即使意思更贴切。
2. 数字、拉丁字母、希腊字母、标点、运算符必须来自下面「符号表」（共 {sym_count} 个）。符号表以外的字符一律禁止：emoji、特殊排版字符、生僻标点都不允许。
3. 遇到字库外的汉字，必须改写而不是硬写，方法是：
   - 换同义词或近义词（如「阐述」→「说明」，「因为」→「由于」）；
   - 换一种说法，把生僻概念拆成字表内的常用词；
   - 用公式、符号、字母或数字表达（如「线电压」可写成 $U_{线}$）；
   - 实在无法表达就删掉该修饰语，只保留能说清的核心结论。
4. 标点请用符号表内的半角符号 , . ; : ? ! ( )，也可用符号表内的全角标点（、。《》【】！（），：；？ 等）；不要用符号表以外的新奇标点或 emoji。
5. 提交前逐字自检：只要有一个汉字不在汉字表内，就改写那一句。

【第二优先级：考试答题风格】
- 按考试/作业答题的方式写：先给结论，再写必要步骤；计算题给式子与结果，证明题给关键推理链。
- 精炼：不要客套话（「好的」「以下是」「希望有帮助」），不要复述题干，不要解释题目背景，不要写总结性废话。
- 分点用 1. 2. 3. 或 (1) (2) (3)，每个分点一到两行，行文短、句子直。
- 不要出现 emoji，不要用 Markdown 装饰（#、*、-、>、表格、代码块、加粗）。
- 公式是答案的一部分，直接写式子，不要写「根据公式可得」这类空话。

【公式写法（只支持这个子集）】
- 行内公式用 $...$，例如 $I=\frac{U}{R}$、$\sqrt{3}$、$U_{相}$、$I^2R$、$\sum P$、$\int_0^t i\,dt$。
- 可用命令：\frac{}{}、\sqrt{}、\sum、\prod、\int、\times、\cdot、\pm、\approx、\le、\ge、\ne、\infty、\angle、\alpha 到 \omega、\Delta、\Omega，以及上下标 ^ 和 _。
- 不要用 TikZ、\begin{} 到 \end{} 的环境、自定义宏、\text{中文}、\left \right 等未支持写法；不要用 $$...$$ 大公式。
- 公式里的中文（如 $U_{线}$）会按普通汉字渲染，只能用汉字表内的字。

【输出格式：只输出一个 JSON 对象，不要代码块，不要任何解释】
{"answer_text": "答案纯文本，供人工确认", "segments": [{"text": "第一段文字", "size_pt": 12.4, "line_pt": 23.0, "align": "left"}]}
- segments 按逻辑分点/分行：每个分点、每个公式行单独成段，一般 2 到 8 段。
- size_pt：正文 {default_size_pt}，小字注释 {size_min_pt} 到 {default_size_pt}，标题最大 {size_max_pt}。
- line_pt：约为字号的 1.6 到 1.9 倍，正文默认 {default_line_pt}。
- align：left / center / right，正文一律 left。
- 每个 segment 的 text 不能含换行符；需要换行请拆成两个 segment。

【汉字表（{han_count} 个，由 allowed_charset() 从字体 cmap 动态注入）】
{hanzi_list}

【符号表（{sym_count} 个，由 allowed_charset() 从字体 cmap 动态注入）】
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
    if question is None or not str(question).strip():
        block = "【题干】\n（未提供题干：只输出答案 JSON，不要复述题目。）"
    else:
        block = "【题干】\n" + str(question).strip()
    if segments:
        if isinstance(segments, str):
            block += "\n\n【已有分段（供修订参考，可整段替换）】\n" + segments.strip()
        else:
            try:
                js = json.dumps(segments, ensure_ascii=False, indent=2)
            except Exception:
                js = str(segments)
            block += "\n\n【已有分段（供修订参考，可整段替换）】\n" + js
    block += "\n\n请直接输出 JSON。"
    return block


def builtin_prompt(question=None, segments=None):
    """生成完整中文系统提示词：真实字表 + 版面约定 + JSON 输出要求。

    参数
    ----
    question : 可选，题干；提供后会替换模板里的 {question_block} 占位。
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
                     "description": "这一段要手写的文字；公式用 $...$ 包住，不要含换行符"},
            "size_pt": {"type": "number", "minimum": SIZE_MIN_PT,
                        "maximum": SIZE_MAX_PT, "default": DEFAULT_SIZE_PT,
                        "description": "字号（pt），正文默认 %g" % DEFAULT_SIZE_PT},
            "line_pt": {"type": "number", "minimum": 10, "maximum": 60,
                        "default": DEFAULT_LINE_PT,
                        "description": "行距（pt），约字号的 1.6 到 1.9 倍"},
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
            "把手写风格的答案渲染成 PNG 或 PDF：透明的笔迹层可以直接落在"
            "图片背景、扫描件或指定 PDF 页面的原版面上（不破坏原内容）。"
            "输入是按逻辑分段的文字与字号/行距，返回图片或 PDF 的 base64、"
            "汉字覆盖率与缺字警告。汉字只支持字库内 %d 字、符号 %d 个，"
            "字库外字符会被回退字体渲染或渲染成空白，调用前请按提示词约束检查。"
            % (len(han), len(sym))
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "segments": {
                    "type": "array", "minItems": 1,
                    "maxItems": 60,
                    "description": "按逻辑分点/分行的文字段，每段一段话或一行公式",
                    "items": segment_schema(),
                },
                "background": background_schema(),
                "format": {"type": "string", "enum": ["png", "pdf"],
                           "default": "png",
                           "description": "输出格式：png 图片或 pdf（可保留源 PDF 原内容）"},
                "return_base64": {"type": "boolean", "default": True,
                                  "description": "是否在响应里返回 base64（false 时只返回路径/元信息）"},
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
    no_text = [it for it in items if not (it["text"] or "").strip()]

    head = r"""你是「手写作业答题器」：下面是一份作业/试卷的版面分析结果，请**逐题作答**，
只输出一个 JSON 对象，答案会被渲染成手写笔迹写进每题的答题区。

【输出格式（唯一允许的输出）】
{"answers": {"1": "第一题的答案", "2": "第二题的答案"}}
- 键必须原样使用【题目清单】里给出的「题号」（例如 "1"、"2"、"4.2.1"）。
- 值是一题的答案纯文本：不要换行符、不要代码块、不要 JSON 以外的任何文字。
- 不要输出解释、客套话、Markdown（# * - > 表格 加粗）、emoji、图片链接。
- 只作答【题目清单】里出现的题；不要编造题号，不要漏题。

【第一优先级：只能使用字库内字符】
1. 每个汉字都必须来自下面「汉字表」（共 {han_count} 个）；字表外的汉字一律禁止。
2. 数字、字母、希腊字母、标点、运算符必须来自「符号表」（共 {sym_count} 个）；
   emoji、特殊排版字符、生僻标点一律禁止。
3. 遇到字库外的汉字要改写而不是硬写：换同义词；换一种说法拆成常用词；
   用公式/符号/字母/数字表达；实在不行删掉修饰语，保留核心结论。
4. 提交前逐字自检：只要一个汉字不在表内就改写那一句。

【第二优先级：像人写作业那样作答（不要像答题机器）】
- 每题以「解：」开头；问数值/结论的题，结尾用「答：……」把结果说清楚。
- 分步写：先用一句自然的话说明依据（如「由换路定则可知」「星形联结时线电流等于相电流」），
  再写公式与代入，再给结果和单位；必要的文字承上启下，不要只堆一串等式。
- 不要把答案压成一行短句；按人的书写习惯用「，」「。」自然断句，读起来像学生手写的作业。
- 但保持考试尺度：不要复述题干、不要解释题目背景、不要客套话或总结废话；每题答案控制在【题目清单】给出的「可写字数」以内，宁短勿长，写不下会被缩字号或溢出。
- 算式写法要像手写：**除法写成分数式 $\frac{分子}{分母}$（上下结构），不要用斜杠 / ；
  很简单的算式也可用 ÷**。乘号用 ×，开方用 √，单位照写（A、V、Ω、kW、kvar、kVA）。
- 公式只支持子集：$...$ 行内公式，可用 \frac{}{}、\sqrt{}、\sum、\prod、\int、
  \times、\cdot、\pm、\approx、\le、\ge、\ne、\infty、\angle、\alpha~\omega、
  \Delta、\Omega，以及上下标 ^ 和 _；不要 TikZ/\begin 环境/自定义宏/\text{中文}/$$...$$。

【作答要求】
- 答案从每题答题区顶部开始写、按答题区宽度自动换行，所以直接用自然段文字即可，
  不要自己加换行、缩进或分栏。
- 扫描件/图片页的题干可能是 null（无文字层）：这类题的题干以题目区域截图给出，
  请按截图理解题意后作答，仍然只输出 JSON，不要描述图片。
"""

    if include_charset:
        head += "\n【汉字表（{han_count} 个，由 allowed_charset() 从字体 cmap 动态注入）】\n"
        head += (_wrap_chars(han_str, 40) if han_str
                 else "（汉字库缺失：请确认 字体输出\\ZTi-Han 是否存在）")
        head += "\n\n【符号表（{sym_count} 个，由 allowed_charset() 从字体 cmap 动态注入）】\n"
        head += (_wrap_chars(sym_str, 60) if sym_str
                 else "（符号库缺失：请确认 字体输出\\ZTi-Sym 是否存在）")
        head += "\n"

    if items:
        lines = ["\n【题目清单（共 %d 题；按此顺序作答）】" % len(items)]
        for i, it in enumerate(items, 1):
            lines.append("第 %d 题  题号 %s" % (i, it["number"] or it["id"]))
            text = (it["text"] or "").strip()
            if text:
                lines.append("  题干：%s" % text)
            else:
                lines.append("  题干：（无文字层，见题目区域截图 crop_png_base64）")
            lines.append("  答题区：约 %d 行，每行约 %d 字，合计约 %d 字"
                         % (it["lines"], it["per_line"], it["max_chars"]))
        lines.append("")
        lines.append("请直接输出 {\"answers\": {...}}，键用上面的题号。")
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
    if no_text and has_crop:
        out += ("\n注：有 %d 题没有文字层，题干以题目区域截图（crop_png_base64）提供。"
                % len(no_text))
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

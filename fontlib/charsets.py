# -*- coding: utf-8 -*-
"""charsets.py — 「这次要写哪些字」的唯一来源。

用户要能自己决定自建库包含哪些字，所以这里提供三层：

1. **标准字表**：GB2312 一级（3755 常用）/ GB2312 全集（6763）。
   GB2312 是算法可枚举的（按区位码解码），不依赖任何外部数据文件，
   也不会因为换了台电脑就缺字表。覆盖现代汉语 99.99% 的用字。
2. **差集**：从标准字表里去掉「当前字库已经有的字」，只补缺的——
   这是最省力的用法，也是「补字模板」的默认来源。
3. **自定义**：粘贴正文 / 读 txt、csv、xlsx 里的字 / 指定一段语料。

生僻字的界定：不在 GB2312 里的字（含扩展区）视为生僻，默认不进模板；
用户仍可以显式加进来（例如专业术语里的生僻字）。
"""
from __future__ import annotations

import os
import re

# ---------------------------------------------------------------- 基本判断
_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_VISIBLE_EXTRA = set("　、。《》【】！（），：；？“”‘’…—·￥℃‰′″")


def is_cjk(ch):
    return bool(_CJK.match(ch))


def is_visible(ch):
    """能不能写进模板：排除空白、控制符、代理对、私用区、替换符。"""
    if not ch or len(ch) != 1:
        return False
    cp = ord(ch)
    if ch.isspace() and ch != "\u3000":
        return False
    if cp < 0x20 or 0x7F <= cp <= 0x9F:
        return False
    if 0xD800 <= cp <= 0xF8FF:      # 代理对 + 私用区
        return False
    if cp in (0xFFFD, 0xFEFF):
        return False
    return True


def normalize(chars):
    """去重、去不可见字符，保持原顺序。"""
    out, seen = [], set()
    for ch in chars or []:
        if not is_visible(ch) or ch in seen:
            continue
        seen.add(ch)
        out.append(ch)
    return out


# ---------------------------------------------------------------- 标准字表
_gb_cache = {}


def _gb2312(level=None):
    """GB2312 汉字表（按区位码/拼音序）。

    level=1 → 一级 3755 字（常用）；level=2 → 二级 3008 字（次常用）；
    None → 全集 6763 字。
    """
    key = level or 0
    if key in _gb_cache:
        return list(_gb_cache[key])
    chars = []
    for hi in range(0xB0, 0xF8):
        for lo in range(0xA1, 0xFF):
            try:
                s = bytes((hi, lo)).decode("gb2312")
            except (UnicodeDecodeError, LookupError):
                continue
            if len(s) == 1 and is_cjk(s):
                chars.append(s)
    if level == 1:
        chars = [c for c in chars if c.encode("gb2312")[0] <= 0xD7]
    elif level == 2:
        chars = [c for c in chars if c.encode("gb2312")[0] >= 0xD8]
    _gb_cache[key] = tuple(chars)
    return list(chars)


def gb2312_level1():
    """3755 个常用汉字（一级字库）。"""
    return _gb2312(1)


def gb2312_level2():
    """3008 个次常用汉字（二级字库）。"""
    return _gb2312(2)


def gb2312_all():
    """6763 个 GB2312 汉字（一级 + 二级）。"""
    return _gb2312(None)


def cjk_unified():
    """CJK 基本区全部汉字（20902 个，含大量生僻字，不推荐整本写）。"""
    return [chr(cp) for cp in range(0x4E00, 0x9FA6)]


def is_rare(ch):
    """不在 GB2312 里的汉字算生僻（对非汉字恒为 False）。"""
    if not is_cjk(ch):
        return False
    try:
        ch.encode("gb2312")
        return False
    except UnicodeEncodeError:
        return True


# ---------------------------------------------------------------- 自定义来源
def from_text(text):
    """从一段正文里取所有可见字符（用于「按我常写的内容建库」）。"""
    return normalize(text or "")


def from_file(path):
    """读 txt / csv / json / xlsx 里的字。

    * txt：整篇取字
    * csv / json：取所有字符串字段里的字
    * xlsx：所有单元格文本
    """
    path = str(path)
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            text = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    for v in row:
                        if isinstance(v, str):
                            text.append(v)
            wb.close()
            return from_text("\n".join(text))
        except Exception:
            return []
    if ext == ".json":
        import json
        try:
            with open(path, encoding="utf-8") as fp:
                data = json.load(fp)
        except Exception:
            return []
        buf = []

        def walk(x):
            if isinstance(x, str):
                buf.append(x)
            elif isinstance(x, dict):
                for v in x.values():
                    walk(v)
            elif isinstance(x, (list, tuple)):
                for v in x:
                    walk(v)
        walk(data)
        return from_text("\n".join(buf))
    # 其余按文本处理（UTF-8，失败再试 GBK）
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with open(path, encoding=enc) as fp:
                return from_text(fp.read())
        except UnicodeDecodeError:
            continue
        except OSError:
            return []
    return []


# ---------------------------------------------------------------- 字体覆盖
def font_chars(font_path):
    """某个字体文件 cmap 里的字符集合；读不到返回空集。"""
    try:
        from fontTools.ttLib import TTFont
        cm = TTFont(font_path).getBestCmap() or {}
        return {chr(cp) for cp in cm}
    except Exception:
        return set()


def covered(chars, font_paths):
    """chars 里能被打中任意一套字库覆盖的那部分。"""
    pool = set()
    for p in font_paths or []:
        pool |= font_chars(p)
    return [c for c in normalize(chars) if c in pool]


def missing_from(chars, font_paths):
    """chars 里所有字库都没有的那部分（保持原顺序）。"""
    pool = set()
    for p in font_paths or []:
        pool |= font_chars(p)
    return [c for c in normalize(chars) if c not in pool]


# ---------------------------------------------------------------- 预设
def presets(font_paths=None):
    """界面下拉框用的预设；``font_paths`` 给定时提供「补当前缺字」。"""
    out = [
        {"id": "missing", "label": "补当前字库缺的字（推荐）",
         "desc": "GB2312 全集 6763 字里，当前启用字库还没有的字。"},
        {"id": "gb2312-l1", "label": "常用汉字 3755（GB2312 一级）",
         "desc": "现代汉语最常用的一级字库，按拼音排。"},
        {"id": "gb2312", "label": "常用 + 次常用 6763（GB2312 全集）",
         "desc": "不含生僻字；日常与专业内容基本够用。"},
        {"id": "cjk", "label": "CJK 基本区 20902（含生僻，慎选）",
         "desc": "整本写工作量极大，只建议挑选其中需要的段。"},
        {"id": "custom", "label": "自定义（粘贴正文 / 选文件）",
         "desc": "按自己的语料建库，最省力。"},
    ]
    return out


def resolve(preset, text="", path="", font_paths=None, exclude_existing=False,
            drop_rare=True):
    """把界面选择解析成最终字表（有序、去重）。

    参数
    ----
    preset   presets() 里的 id
    text     自定义正文
    path     自定义文件
    font_paths  现有字库的字体路径（用于差集）
    exclude_existing  是否去掉这些字库里已有的字
    drop_rare  是否剔除 GB2312 之外的生僻字
    """
    if preset == "custom":
        chars = from_file(path) if path else from_text(text)
    elif preset == "missing":
        chars = gb2312_all()
    elif preset == "gb2312-l1":
        chars = gb2312_level1()
    elif preset == "gb2312":
        chars = gb2312_all()
    elif preset == "cjk":
        chars = cjk_unified()
    else:
        chars = from_text(text)
    if drop_rare:
        chars = [c for c in chars if not is_rare(c)]
    if exclude_existing or preset == "missing":
        chars = missing_from(chars, font_paths or [])
    return normalize(chars)


# ---------------------------------------------------------------- 统计
def describe(chars):
    """字数分类统计（界面显示 / 报告用）。"""
    counts = {"总计": 0, "汉字": 0, "数字": 0, "拉丁": 0,
              "标点符号": 0, "其他": 0, "生僻字": 0}
    for ch in normalize(chars):
        counts["总计"] += 1
        if is_cjk(ch):
            counts["汉字"] += 1
            if is_rare(ch):
                counts["生僻字"] += 1
        elif ch.isdigit():
            counts["数字"] += 1
        elif ch.isascii() and ch.isalpha():
            counts["拉丁"] += 1
        elif not ch.isalnum():
            counts["标点符号"] += 1
        else:
            counts["其他"] += 1
    return counts


def to_text(chars, per_line=20):
    """字表导出成 txt（前 20 字一行，后面带序号/U+码位）。"""
    chars = normalize(chars)
    lines = []
    for i in range(0, len(chars), per_line):
        lines.append("".join(chars[i:i + per_line]))
    lines.append("")
    for i, ch in enumerate(chars, 1):
        lines.append("%4d  %s  U+%04X" % (i, ch, ord(ch)))
    return "\n".join(lines) + "\n"


def save_list(chars, path):
    """字表存盘：txt + 同名 csv（Excel 直接打开）。"""
    chars = normalize(chars)
    with open(path, "w", encoding="utf-8") as fp:
        fp.write(to_text(chars))
    csv_path = os.path.splitext(path)[0] + ".csv"
    with open(csv_path, "w", encoding="utf-8-sig") as fp:
        fp.write("序号,字符,Unicode,类别\n")
        for i, ch in enumerate(chars, 1):
            kind = ("汉字" if is_cjk(ch) else
                    "数字" if ch.isdigit() else
                    "拉丁" if ch.isascii() and ch.isalpha() else
                    "标点符号" if not ch.isalnum() else "其他")
            fp.write("%d,%s,U+%04X,%s\n" % (i, ch, ord(ch), kind))
    return path, csv_path


if __name__ == "__main__":
    for name, fn in (("一级", gb2312_level1), ("二级", gb2312_level2),
                     ("全集", gb2312_all), ("CJK", cjk_unified)):
        cs = fn()
        print("%s: %d 字  前 10：%s" % (name, len(cs), "".join(cs[:10])))
    print("生僻判断：", is_rare("峣"), is_rare("的"))
    print(describe("三相电路 R=10Ω，≈52.2kVA。"))

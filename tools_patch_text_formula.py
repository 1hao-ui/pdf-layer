# -*- coding: utf-8 -*-
"""让纯文本里的数字/字母/符号也走公式渲染，统一观感。

为什么
------
实测同一内容两条路径的笔画粗细与墨色差很多：

    纯文本 380   笔画中位 6.0px  墨色 208（又细又淡）
    公式 $380$   笔画中位 9.0px  墨色 173

原因是纯文本走 _char_tile：贴图闭环为了对齐字高会缩放字形，
缩放把笔画带细了；而公式走 handstroke 的笔迹渲染，笔画更粗更实。

用户建议「都用公式格式算了」——于是把纯文本里的非汉字字符也交给
formula.draw 渲染（单字符 LaTeX），两条路径完全同源，观感自然一致。
汉字仍走 _char_tile（公式引擎不擅长汉字手写体）。
"""
import io
import os

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf-layer")
P = os.path.join(REPO, "hwrender.py")
s = io.open(P, encoding="utf-8").read()

# ---------- 1) 判断哪些字符走公式渲染 ----------
anchor = "_RAISE_EM = 0.38"
add = anchor + '''

# 纯文本里哪些字符改用公式渲染（与 $...$ 完全同源，保证粗细墨色一致）。
# 汉字不走——公式引擎的汉字来自字体度量，和手写汉字库风格不搭。
_FORMULA_CHARS = set(
    "0123456789"
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "+-−×÷=≠≈≡<>≤≥±∓·/\\\\"
    "()[]{}|"
    "αβγδεζηθικλμνξοπρστυφχψωΓΔΘΛΞΠΣΦΨΩ"
    "√∞∂∇∑∏∫∬°℃%‰"
)
# 这些字符在 LaTeX 里要转义
_TEX_ESCAPE = {"%": r"\\%", "#": r"\\#", "&": r"\\&", "_": r"\\_",
               "{": r"\\{", "}": r"\\}", "$": r"\\$"}


def _formula_ready(ch):
    """该字符是否适合交给公式渲染。"""
    return ch in _FORMULA_CHARS


def _tex_char(ch):
    """把单个字符转成可安全解析的 LaTeX 片段。"""
    return _TEX_ESCAPE.get(ch, ch)'''
assert anchor in s
s = s.replace(anchor, add, 1)

# ---------- 2) _render_line：非汉字走公式 ----------
old = '''            ch = val
            p = self._char_params(ch, px)
            used = self._variant_for(ch)
            tile, ox, oy = self._char_tile(ch, px, p, used)'''
new = '''            ch = val
            if _formula_ready(ch):
                # 走公式渲染：与 $...$ 同源，粗细墨色一致
                tex = _tex_char(ch)
                fw, _fa, _fd = formula.measure(tex, px, self.font_path, self.ink,
                                               self.math_font_path)
                if x < right:
                    x = right
                formula.draw(img, tex, x, baseline, px, self.font_path, self.ink,
                             self.math_font_path)
                right = x + fw
                x += fw
                continue
            p = self._char_params(ch, px)
            used = self._variant_for(ch)
            tile, ox, oy = self._char_tile(ch, px, p, used)'''
assert old in s
s = s.replace(old, new, 1)

# ---------- 3) _line_box：同样按公式量宽 ----------
old2 = '''            elif kind in ("^", "_"):'''
new2 = '''            elif kind == "c" and _formula_ready(val):
                fw, fa, fd = formula.measure(_tex_char(val), px, self.font_path,
                                             self.ink, self.math_font_path)
                w += fw
                asc = max(asc, fa)
                desc = max(desc, fd)
            elif kind in ("^", "_"):'''
assert old2 in s
s = s.replace(old2, new2, 1)

# ---------- 4) _advance：与公式渲染一致 ----------
old3 = '''    def _advance_with(self, ch, px, font_path):'''
new3 = '''    def _advance_formula(self, ch, px):
        """字符走公式渲染时的前进宽度。"""
        try:
            return formula.measure(_tex_char(ch), px, self.font_path, self.ink,
                                   self.math_font_path)[0]
        except Exception:
            return self._advance(ch, px)

    def _advance_with(self, ch, px, font_path):'''
assert old3 in s
s = s.replace(old3, new3, 1)

# ---------- 5) _advance 入口分流 ----------
old4 = '''        path = self._font_for(ch)
        scale = fontcal.params_for(path).get("size_scale", 1.0)
        return _ttf(path, max(4, int(round(px * scale)))).getlength(ch)'''
new4 = '''        if _formula_ready(ch):
            try:
                return formula.measure(_tex_char(ch), px, self.font_path, self.ink,
                                       self.math_font_path)[0]
            except Exception:
                pass
        path = self._font_for(ch)
        scale = fontcal.params_for(path).get("size_scale", 1.0)
        return _ttf(path, max(4, int(round(px * scale)))).getlength(ch)'''
assert old4 in s
s = s.replace(old4, new4, 1)

io.open(P, "w", encoding="utf-8", newline="\n").write(s)
print("patched: 纯文本数字/字母/符号改走公式渲染")

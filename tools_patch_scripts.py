# -*- coding: utf-8 -*-
"""重写上下标定位：按「墨迹盒」而不是「基线」对齐。

原实现的问题：用「基线 + 固定偏移」定位上下标，但各字形的基线到墨迹顶/底
的距离差别很大（新字库是扫描墨迹，字形高低不一），结果：
  · x_1 的下标掉到基线下方很远
  · ∑ 的 i=1 掉出 ∑ 底部一整行
  · ∫ 的 0 同样掉得很低

改成按墨迹盒对齐（更符合手写直觉）：
  · 上标：墨迹底边 ≈ base 墨迹顶部下方 12% 处（贴着 base 顶部）
  · 下标：墨迹顶边 ≈ base 基线下方 8% 处（紧贴基线）
  · 上下标水平位置：紧贴 base 右缘，略微内收
"""
import io
import os

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf-layer")
P = os.path.join(REPO, "vendor", "mthw", "math_renderer.py")
s = io.open(P, encoding="utf-8").read()

# ---------- 1) 普通上下标 ----------
start = s.index("    def _script_box(self, node: ScriptNode")
end = s.index("    def _integral_side_scripts_box(")
new_script = '''    def _script_box(self, node: ScriptNode, size_px: int, seed_extra: str, display_style: bool = False) -> MathBox:
        symbol, limit_mode = self._operator_limit_mode(node.base)
        if symbol and self._should_stack_limits(symbol, limit_mode, display_style):
            return self._operator_limits_box(node, size_px, seed_extra)
        if symbol in INTEGRAL_SYMBOLS:
            return self._integral_side_scripts_box(node, size_px, seed_extra, display_style)
        base = self._layout(node.base, size_px, f"{seed_extra}:base", display_style)
        script_size = max(9, round(size_px * 0.58))
        superscript = self._layout(node.superscript, script_size, f"{seed_extra}:sup") if node.superscript else None
        subscript = self._layout(node.subscript, script_size, f"{seed_extra}:sub") if node.subscript else None

        # 本地改动：按「墨迹盒」定位，不用固定基线偏移。
        # 扫描字库的字形高低不一，按基线算会让下标掉出去（实测 x_1 的 1
        # 落到基线下方一整行）。这里直接对齐墨迹边缘：
        #   上标底边  ≈ base 顶部往下 10%
        #   下标顶边  ≈ base 基线往下 10%
        base_top = base.baseline - _ink_asc(base)
        base_bot = _ink_desc(base)
        gap = max(1, round(size_px * 0.06))
        script_x = max(1, base.width - round(size_px * 0.06))

        sup_y = 0
        base_y = 0
        if superscript:
            sup_h = superscript.height
            sup_top_target = base_top - sup_h + round(size_px * 0.10)
            sup_y = sup_top_target
            if sup_y < 0:                       # 上标比 base 高，整体下移
                base_y = -sup_y
                sup_y = 0
        sub_y = 0
        if subscript:
            # 下标顶边贴着基线下方一点
            sub_top_target = base_y + base.baseline + gap
            sub_y = sub_top_target

        height = base_y + base.height
        if subscript:
            height = max(height, sub_y + subscript.height)
        if superscript:
            height = max(height, sup_y + superscript.height)
        width = base.width + max(
            superscript.width if superscript else 0,
            subscript.width if subscript else 0,
        )
        image = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
        image.alpha_composite(base.image, (0, base_y))
        if superscript:
            image.alpha_composite(superscript.image, (script_x, sup_y))
        if subscript:
            image.alpha_composite(subscript.image, (script_x, sub_y))
        return MathBox(image, base_y + base.baseline)

'''
s = s[:start] + new_script + s[end:]

# ---------- 2) 大运算符的侧挂上下限 ----------
start2 = s.index("    def _integral_side_scripts_box(")
end2 = s.index("    def _operator_limit_mode(")
new_side = '''    def _integral_side_scripts_box(
        self,
        node: ScriptNode,
        size_px: int,
        seed_extra: str,
        display_style: bool = False,
    ) -> MathBox:
        """∑ ∏ ∫ 的上下限写在侧边（手写习惯）。

        定位规则同样按墨迹盒：
          · 上标底边贴着运算符顶部往下 8%
          · 下标顶边贴着运算符基线往下 6%
        这样 i=1 不会掉出运算符底部一整行。
        """
        base = self._layout(node.base, size_px, f"{seed_extra}:base", display_style)
        script_size = max(9, round(size_px * 0.54))
        superscript = self._layout(node.superscript, script_size, f"{seed_extra}:sup") if node.superscript else None
        subscript = self._layout(node.subscript, script_size, f"{seed_extra}:sub") if node.subscript else None

        base_top = base.baseline - _ink_asc(base)
        gap = max(1, round(size_px * 0.05))
        script_x = max(1, base.width - round(size_px * 0.06))

        sup_y = 0
        base_y = 0
        if superscript:
            sup_y = base_top - superscript.height + round(size_px * 0.08)
            if sup_y < 0:
                base_y = -sup_y
                sup_y = 0
        sub_y = 0
        if subscript:
            sub_y = base_y + base.baseline + gap

        width = max(base.width,
                    script_x + superscript.width if superscript else 0,
                    script_x + subscript.width if subscript else 0)
        height = base_y + base.height
        if superscript:
            height = max(height, sup_y + superscript.height)
        if subscript:
            height = max(height, sub_y + subscript.height)
        image = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
        image.alpha_composite(base.image, (0, base_y))
        if superscript:
            image.alpha_composite(superscript.image, (script_x, sup_y))
        if subscript:
            image.alpha_composite(subscript.image, (script_x, sub_y))
        return MathBox(image, base_y + base.baseline)

'''
s = s[:start2] + new_side + s[end2:]

# ---------- 3) 辅助函数：量墨迹的 asc/desc ----------
anchor = "class MathBox:"
helper = '''def _ink_asc(box) -> int:
    """墨迹顶到基线的距离（不是字体 ascent）。"""
    bb = box.image.getbbox()
    if bb is None:
        return box.baseline
    return max(0, box.baseline - bb[1])


def _ink_desc(box) -> int:
    """基线到墨迹底的距离。"""
    bb = box.image.getbbox()
    if bb is None:
        return max(0, box.height - box.baseline)
    return max(0, bb[3] - box.baseline)


class MathBox:'''
assert anchor in s
s = s.replace(anchor, helper, 1)

io.open(P, "w", encoding="utf-8", newline="\n").write(s)
print("patched: 上下标按墨迹盒定位")

# -*- coding: utf-8 -*-
"""让 ∑/∏/∫ 像根号一样「随内容变高」。

根号的做法是：先排内容，再按内容高度画伸长的根号笔画。
大运算符是字形，做法等价于「按内容高度选一个更大的字号」：

  1. RowNode 布局时先预扫一遍：把「非大运算符」的子项排出来，得到该行最大高度；
  2. 再排大运算符，把它放大到「不低于该高度」（有上限，避免病态放大）；
  3. 上下限仍按原字号排，不跟着放大（印刷体也是这样）。

这样 \\sum 后面跟一个分式时，∑ 会自动长到和分式一样高。
"""
import io
import os

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf-layer")
P = os.path.join(REPO, "vendor", "mthw", "math_renderer.py")
s = io.open(P, encoding="utf-8").read()

# ---- 1) 常量：拉伸上限与最小比例
anchor = "STACK_LIMITS_IN_DISPLAY = False"
add = anchor + '''

# 大运算符「随内容变高」的参数
BIGOP_STRETCH_MAX = 2.4      # 最多放大到原字号的 2.4 倍
BIGOP_STRETCH_SLACK = 1.06   # 略微超过内容高度，避免看起来比内容矮
BIGOP_STRETCH_MIN = 1.0      # 至少保持原样（不缩小）'''
assert anchor in s
s = s.replace(anchor, add, 1)

# ---- 2) 辅助：判断节点是不是「大运算符（可能带上下限）」
anchor2 = "    def _operator_limit_mode(self, node: MathNode) -> tuple[str | None, str | None]:"
helper = '''    def _bigop_base_symbol(self, node: MathNode) -> str | None:
        """若节点是（可能带上下限/上下标的）大运算符，返回其符号。"""
        cur = node
        for _ in range(3):
            if isinstance(cur, (ScriptNode, LimitsNode)):
                cur = cur.base
            else:
                break
        if isinstance(cur, TextNode):
            t = getattr(cur, "text", "")
            if t in LIMIT_OPERATOR_SYMBOLS:
                return t
        return None

    def _layout_bigop_stretched(self, node: MathNode, size_px: int, seed_extra: str,
                                display_style: bool, target_h: int) -> MathBox:
        """按目标高度放大大运算符（上下限不跟着放大）。"""
        sym = self._bigop_base_symbol(node)
        if sym is None or target_h <= 0:
            return self._layout(node, size_px, seed_extra, display_style)
        # 先按原字号量一次，得到它自己的高度
        probe = self._layout(node, size_px, seed_extra + ":probe", display_style)
        if probe.height <= 0:
            return probe
        want = target_h * BIGOP_STRETCH_SLACK
        if probe.height >= want:
            return probe
        k = min(BIGOP_STRETCH_MAX, max(BIGOP_STRETCH_MIN, want / probe.height))
        if k <= 1.02:
            return probe
        # 只放大运算符本体：把 base 单独放大后排上下限
        if isinstance(node, (ScriptNode, LimitsNode)) and node.superscript or \\
                isinstance(node, (ScriptNode, LimitsNode)) and node.subscript:
            return self._layout_scripts_with_bigop(node, int(round(size_px * k)),
                                                   size_px, seed_extra, display_style)
        return self._layout(node, int(round(size_px * k)), seed_extra, display_style)

    def _layout_scripts_with_bigop(self, node, big_size: int, base_size: int,
                                   seed_extra: str, display_style: bool) -> MathBox:
        """运算符本体用 big_size，上下限仍用 base_size 的比例。"""
        base = self._layout(node.base, big_size, f"{seed_extra}:bigbase", display_style)
        script_size = max(9, round(base_size * 0.54))
        sup = self._layout(node.superscript, script_size, f"{seed_extra}:sup") \\
            if node.superscript else None
        sub = self._layout(node.subscript, script_size, f"{seed_extra}:sub") \\
            if node.subscript else None
        raise_by = round(base_size * 0.57)
        lower_by = round(base_size * 0.25)
        initial_sup_y = base.baseline - raise_by - sup.baseline if sup else 0
        base_y = max(0, -initial_sup_y)
        baseline = base_y + base.baseline
        sup_x = max(1, base.width - round(base_size * 0.09))
        sub_x = max(1, base.width - round(base_size * 0.24))
        sup_y = base_y + initial_sup_y
        sub_y = baseline + lower_by - sub.baseline if sub else 0
        width = max(base.width,
                    sup_x + sup.width if sup else 0,
                    sub_x + sub.width if sub else 0)
        height = base_y + base.height
        if sup:
            height = max(height, sup_y + sup.height)
        if sub:
            height = max(height, sub_y + sub.height)
        image = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
        image.alpha_composite(base.image, (0, base_y))
        if sup:
            image.alpha_composite(sup.image, (sup_x, sup_y))
        if sub:
            image.alpha_composite(sub.image, (sub_x, sub_y))
        return MathBox(image, baseline)

''' + anchor2
assert anchor2 in s
s = s.replace(anchor2, helper, 1)

# ---- 3) RowNode 布局改为「预扫 + 拉伸」
old_row = '''        if isinstance(node, RowNode):
            boxes: list[MathBox] = []
            current_style = display_style
            for index, child in enumerate(node.items):
                if isinstance(child, StyleNode):
                    current_style = child.display_style
                    continue
                boxes.append(self._layout(child, size_px, f"{seed_extra}:row:{index}", current_style))
            return self._row_box(boxes, size_px)'''
new_row = '''        if isinstance(node, RowNode):
            # 两遍布局：先量出「非大运算符」子项的最大高度，
            # 再据此把 ∑/∏/∫ 拉伸到那个高度（和根号一样随内容变长）。
            items = []
            current_style = display_style
            for index, child in enumerate(node.items):
                if isinstance(child, StyleNode):
                    current_style = child.display_style
                    continue
                items.append((index, child, current_style))
            target_h = 0
            for index, child, st in items:
                if self._bigop_base_symbol(child) is not None:
                    continue
                b = self._layout(child, size_px, f"{seed_extra}:pre:{index}", st)
                target_h = max(target_h, b.height)
            boxes: list[MathBox] = []
            for index, child, st in items:
                if self._bigop_base_symbol(child) is not None and target_h > 0:
                    boxes.append(self._layout_bigop_stretched(
                        child, size_px, f"{seed_extra}:row:{index}", st, target_h))
                else:
                    boxes.append(self._layout(child, size_px, f"{seed_extra}:row:{index}", st))
            return self._row_box(boxes, size_px)'''
assert old_row in s
s = s.replace(old_row, new_row, 1)

io.open(P, "w", encoding="utf-8", newline="\n").write(s)
print("patched: 大运算符随内容拉伸")

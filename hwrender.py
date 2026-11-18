# -*- coding: utf-8 -*-
"""hwrender.py — 手写风格文本层渲染引擎（PDF-Layer 本地重建版）

设计目标：把一段文本渲染成「远看是手写」的透明 PNG，并可注回 PDF 原版面空白区。

相对交接稿 5 节的升级（对应 6.1 / 6.2）：
  6.1 同字异形：逐字独立采样 基线漂移 / 水平偏移 / 字号抖动 / 单字旋转 / 墨色深浅
  6.2 笔画层扰动：整行低频正弦弯曲 + 微剪切 + 行首宽行尾紧的字距渐变
  6.5 字形合并：使用 *-merged.ttf（已把 Ω φ √ ≈ ∠ ² ³ 等符号并入中文手写字体），
      不再需要逐字符回退 DejaVuSans，符号也是手写字形。

单位约定：外部坐标一律 pt；内部渲染 SCALE 倍超采样。
"""
import math
import os
import random

from PIL import Image, ImageDraw, ImageFont

import formula

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, "fonts")

SCALE = 4                      # 1pt = 4px
DEFAULT_SIZE_PT = 10.9
DEFAULT_LINE_PT = 20.2
INK = (30, 44, 70)             # 墨色 RGB（alpha 逐字抖动）
PAGE_LEFT_PT = 64.0
PAGE_RIGHT_PT = 468.0
BLOCK_GAP_PT = 15.0
PAGE_BOTTOM_PT = 676.0         # 触发降字号重排的底限（页码在 ~690pt）

NO_LINE_START = "，。；、：？！）》」』”’%,.;:?!)]}"
NO_LINE_END = "（《「『“‘([{"
_TILE_PAD = 6                   # 单字贴图四周的透明内边距（px），排版时不能算进字距
_JITTER_INFLATE = 0.02          # 逐字扰动（缩放/位移/防重叠右推）平均把行宽撑大的比例
_SYM_CP = set(ord(c) for c in "Ωφω√≈∠²³·×−°θΔΣπ±≤≥≠∞→∘⌀")


# ---------------------------------------------------------------- 字体管理
_font_cache = {}


def _ttf(path, px):
    key = (path, px)
    f = _font_cache.get(key)
    if f is None:
        f = ImageFont.truetype(path, px)
        _font_cache[key] = f
    return f


class Hand:
    """一套手写字体 + 渲染参数。"""

    def __init__(self, font_path, size_pt=DEFAULT_SIZE_PT, ink=INK,
                 jitter=1.0, seed=20261118):
        self.font_path = font_path
        self.size_pt = size_pt
        self.ink = ink
        self.jitter = jitter          # 全局扰动强度倍率，0 = 印刷体
        self.rng = random.Random(seed)

    # 6.1 每个字的独立形变参数
    def _char_params(self, ch, px):
        r = self.rng
        j = self.jitter
        return dict(
            dy=r.uniform(-0.8, 0.8) * j * SCALE / 4.0 * 1.0,   # px（以 4x 计）
            dx=r.uniform(-0.6, 0.6) * j * SCALE / 4.0,
            scale=1.0 + r.uniform(-0.035, 0.035) * j,
            rot=r.uniform(-1.2, 1.2) * j,
            alpha=int(r.uniform(220, 255) if j > 0 else 240),
            rgb=tuple(max(0, min(255, c + r.randint(-6, 6) * (1 if j > 0 else 0)))
                      for c in self.ink),
            adv_jit=r.uniform(-0.12, 0.12) * j,                # em
        )

    def _char_tile(self, ch, px, p):
        boost = 1.15 if ord(ch) in _SYM_CP else 1.0     # 符号字形偏小，放大一点才和汉字同高
        f = _ttf(self.font_path, max(4, int(round(px * p["scale"] * boost))))
        try:
            l, t, r, b = f.getbbox(ch)
        except Exception:
            return None, 0, 0
        pad = _TILE_PAD
        w = max(1, r - l) + pad * 2
        h = max(1, b - t) + pad * 2
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.text((pad - l, pad - t), ch, font=f, fill=p["rgb"] + (p["alpha"],))
        if abs(p["rot"]) > 0.05:
            img = img.rotate(p["rot"], resample=Image.BICUBIC, expand=True)
        return img, pad - l, pad - t

    # ------------------------------------------------------ 换行与标点悬挂
    def wrap(self, text, width_pt):
        px = self.size_pt * SCALE
        font = _ttf(self.font_path, int(round(px)))
        # 逐字扰动会把实际行宽撑大几个百分点，换行时按同一比例预留，
        # 否则渲染出来会越过列宽（旧版就是因此每行右侧被裁掉约三成）。
        max_px = width_pt * SCALE / (1.0 + _JITTER_INFLATE * self.jitter)
        lines = []
        for para in text.split("\n"):
            if not para.strip():
                lines.append("")
                continue
            tokens = _tokenize(para)
            cur, cur_w = "", 0.0
            for tk in tokens:
                w = self._token_width(tk, font, px)
                if cur and cur_w + w > max_px:
                    # 标点悬挂：行首禁则字符挤回上一行
                    if tk and tk[0] in NO_LINE_START:
                        cur += tk
                        cur_w += w
                        continue
                    # 行尾禁则：把上一行最后一个字符带下来
                    if cur and cur[-1] in NO_LINE_END:
                        carry = cur[-1]
                        cur = cur[:-1]
                        lines.append(cur)
                        cur = carry + tk
                        cur_w = self._token_width(cur, font, px)
                        continue
                    lines.append(cur)
                    cur, cur_w = tk, w
                else:
                    cur += tk
                    cur_w += w
            if cur:
                lines.append(cur)
        return lines

    # -------------------------------------------------------- 公式支持
    def _token_width(self, tk, font, px):
        """一个 token 的排版宽度：普通文本按字宽累加，$...$ 交给公式层量。"""
        if _is_formula(tk):
            return formula.measure(tk[1:-1], px, self.font_path, self.ink)[0]
        return font.getlength(tk)

    # -------------------------------------------------------- 渲染一个块
    def _line_box(self, line, font, px):
        """返回一行所需的 (宽, 基线以上, 基线以下)，公式会撑高行框。"""
        asc, desc = font.getmetrics()
        w = 0.0
        for kind, val in _atoms(line):
            if kind == "f":
                fw, fa, fd = formula.measure(val, px, self.font_path, self.ink)
                w += fw
                asc = max(asc, fa)
                desc = max(desc, fd)
            else:
                w += font.getlength(val)
        return w, asc, desc

    def render_block(self, text, width_pt=None, size_pt=None):
        """返回 (RGBA Image, 宽pt, 高pt)。"""
        if size_pt:
            self.size_pt = size_pt
        width_pt = width_pt or (PAGE_RIGHT_PT - PAGE_LEFT_PT)
        lines = self.wrap(text, width_pt)
        px = self.size_pt * SCALE
        base_lh = DEFAULT_LINE_PT * SCALE
        font = _ttf(self.font_path, int(round(px)))

        # 逐行算行框：含公式的行会比纯文本行高（分式/根号要占上下空间）
        plans = []
        for line in lines:
            w, asc, desc = self._line_box(line, font, px)
            h_i = max(base_lh, asc + desc + 4)
            baseline = int(max(h_i * 0.78, asc + 2))
            h_i = int(max(h_i, baseline + desc + 2))
            plans.append((line, w, h_i, baseline))

        # 画布先给足（标点悬挂会略超列宽，抖动还有随机外扩），渲染后按墨迹裁边
        need = max([p[1] for p in plans] + [width_pt * SCALE])
        W = int(math.ceil(need * 1.06 + 8))
        H = sum(p[2] for p in plans) or int(base_lh)
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))

        y = 0
        for line, _w, h_i, baseline in plans:
            if line:
                canvas.alpha_composite(
                    self._render_line(line, font, px, baseline, W / SCALE, h_i), (0, y))
            y += h_i
        out = canvas.crop((0, 0, W, y))
        bb = out.getbbox()
        if bb:
            out = out.crop((0, 0, min(out.width, bb[2] + 2), out.height))
        return out, out.size[0] / SCALE, out.size[1] / SCALE

    def _render_line(self, line, font, px, baseline, width_pt, line_h):
        W = int(width_pt * SCALE)
        lh = int(line_h)
        img = Image.new("RGBA", (W, lh), (0, 0, 0, 0))
        ascent = font.getmetrics()[0]
        x = self.rng.uniform(0, 3.0) * self.jitter      # 每行起笔位置随机
        atoms = _atoms(line)
        n = max(1, len(atoms))
        right = -1e9
        for k, (kind, val) in enumerate(atoms):
            if kind == "f":
                # 公式整体当一个原子：不可拆、不与前后字重叠
                fw, _fa, _fd = formula.measure(val, px, self.font_path, self.ink)
                if x < right:
                    x = right
                formula.draw(img, val, x, baseline, px, self.font_path, self.ink)
                right = x + fw
                x += fw
                continue
            ch = val
            p = self._char_params(ch, px)
            tile, ox, oy = self._char_tile(ch, px, p)
            if tile is None:
                continue
            bb = tile.getbbox()          # 贴图内的真实墨迹范围（旋转后也算得准）
            if bb is None:
                continue
            # 6.2 字距渐变：行首略宽，行末略紧
            drift = (0.06 - 0.12 * (k / n)) * px * self.jitter
            adv = font.getlength(ch) * p["scale"] + p["adv_jit"] * px + drift
            px_x = x + p["dx"]
            px_y = baseline - ascent + p["dy"]
            # 防重叠：按「墨迹」边缘判断。贴图四周有 _TILE_PAD 的透明边距，
            # 若把边距算进字距，每个字都会被推开一个 padding，行宽膨胀约 40%，
            # 长行会被画布右边界直接裁掉。这里用 bb 反推墨迹的绝对位置。
            ink_left = px_x - ox + bb[0]
            if ink_left < right:
                px_x += right - ink_left
            img.alpha_composite(tile, (int(round(px_x - ox)), int(round(px_y - oy))))
            right = px_x - ox + bb[2]
            x += adv
        # 6.2 整行低频弯曲 + 剪切
        img = _sine_warp(img, amp=1.5 * self.jitter, lam=300.0, phase=self.rng.uniform(0, 6.28))
        if abs(self.jitter) > 0:
            sh = self.rng.uniform(-0.15, 0.15) * self.jitter
            img = img.transform(img.size, Image.AFFINE,
                                (1, sh, -sh * lh / 2, 0, 1, 0), resample=Image.BICUBIC)
        return img


# ---------------------------------------------------------------- 工具函数
def _is_formula(tk):
    """$...$ 形式的 token 视为公式。"""
    return len(tk) >= 3 and tk[0] == "$" and tk[-1] == "$"


def _atoms(line):
    """把一行拆成原子：('c', 单字) 或 ('f', 公式源码)。

    公式用 $...$ 包住，作为一个整体参与排版（不可在中间断行）。
    """
    out, i, n = [], 0, len(line)
    while i < n:
        if line[i] == "$":
            j = line.find("$", i + 1)
            if j > i:
                out.append(("f", line[i + 1:j]))
                i = j + 1
                continue
        out.append(("c", line[i]))
        i += 1
    return out


def _tokenize(s):
    """中文逐字，连续 ASCII / 数字串不拆（避免把 220 拆开）；$...$ 整体保留。"""
    out, buf, i, n = [], "", 0, len(s)
    while i < n:
        ch = s[i]
        if ch == "$":
            j = s.find("$", i + 1)
            if j > i:
                if buf:
                    out.append(buf)
                    buf = ""
                out.append(s[i:j + 1])       # 含两端 $
                i = j + 1
                continue
        if ch.isascii() and (ch.isalnum() or ch in ".,+-=%/×√"):
            buf += ch
        else:
            if buf:
                out.append(buf)
                buf = ""
            out.append(ch)
        i += 1
    if buf:
        out.append(buf)
    return out


def _shift_rows(img, dy):
    if dy == 0:
        return img
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.alpha_composite(img, (0, -int(dy)))
    return out


def _sine_warp(img, amp=1.5, lam=300.0, phase=0.0):
    if abs(amp) < 0.05:
        return img
    W, H = img.size
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for x in range(W):
        d = int(round(amp * math.sin(2 * math.pi * x / lam + phase)))
        out.alpha_composite(img.crop((x, 0, x + 1, H)), (x, d))
    return out


def render_pages(texts, font_path, page_top_pt, width_pt=None,
                 size_pt=DEFAULT_SIZE_PT, seed=20261118):
    """把若干段文本作为独立块竖向排布，返回 [(Image, top_pt, w_pt, h_pt), ...]。"""
    hand = Hand(font_path, size_pt=size_pt, seed=seed)
    out, y = [], page_top_pt
    for t in texts:
        img, w, h = hand.render_block(t, width_pt=width_pt)
        out.append((img, y, w, h))
        y += h + BLOCK_GAP_PT
    return out


def paste_into_pdf(src_pdf, out_pdf, blocks_by_page, left_pt=PAGE_LEFT_PT):
    """blocks_by_page: {page_index: [(png_path_or_Image, top_pt, w_pt, h_pt), ...]}"""
    import fitz
    doc = fitz.open(src_pdf)
    for pno, blocks in blocks_by_page.items():
        page = doc[pno]
        for item, top, w, h in blocks:
            rect = fitz.Rect(left_pt, top, left_pt + w, top + h)
            if isinstance(item, str):
                page.insert_image(rect, filename=item, overlay=True, keep_proportion=False)
            else:
                import io
                buf = io.BytesIO()
                item.save(buf, format="PNG")
                page.insert_image(rect, stream=buf.getvalue(), overlay=True, keep_proportion=False)
    doc.save(out_pdf, deflate=True, garbage=3)
    doc.close()
    return out_pdf

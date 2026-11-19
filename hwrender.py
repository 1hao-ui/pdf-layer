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
import handstroke

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, "fonts")

SCALE = 4                      # 1pt = 4px
DEFAULT_SIZE_PT = 10.9
DEFAULT_LINE_PT = 20.2
INK = (16, 16, 18)             # 墨色 RGB：黑笔（alpha 逐字抖动）
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


def _pick_math_font(font_path):
    """给公式挑一款拉丁字形更清楚的手写字体。

    正文用的马善政楷书是连笔毛笔体，拉丁 `I` 会被读成 `9`；公式里 I（电流）极常见，
    所以默认换成同目录下的龙藏体（同样 OFL、同样是手写），字形清楚得多。
    找不到就退回正文字体。
    """
    d = os.path.dirname(os.path.abspath(font_path))
    for name in ("LongCang-Regular-merged.ttf", "LongCang-Regular.ttf"):
        p = os.path.join(d, name)
        if os.path.exists(p) and os.path.abspath(p) != os.path.abspath(font_path):
            return p
    return font_path


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
                 jitter=1.0, seed=20261118, math_font_path=None):
        self.font_path = font_path
        self.size_pt = size_pt
        self.ink = ink
        self.jitter = jitter          # 全局扰动强度倍率，0 = 印刷体
        self.rng = random.Random(seed)
        # 公式里的拉丁字母/数字换一款字形更清楚的同族手写字体（默认龙藏体）：
        # 马善政楷书的连笔 I 会被读成 9，公式里 I（电流）出现频率极高，必须可辨。
        self.math_font_path = math_font_path or _pick_math_font(font_path)

    # 6.1 每个字的独立形变参数
    def _char_params(self, ch, px):
        r = self.rng
        j = self.jitter
        return dict(
            # 上下起伏要克制：±0.8px 时每个字像各写各的，收到 ±0.3px 更像同一支笔
            dy=r.uniform(-0.3, 0.3) * j * SCALE / 4.0,
            dx=r.uniform(-0.6, 0.6) * j * SCALE / 4.0,
            scale=1.0 + r.uniform(-0.035, 0.035) * j,
            rot=r.uniform(-1.2, 1.2) * j,
            alpha=int(r.uniform(220, 255) if j > 0 else 240),
            rgb=tuple(max(0, min(255, c + r.randint(-6, 6) * (1 if j > 0 else 0)))
                      for c in self.ink),
            adv_jit=r.uniform(-0.12, 0.12) * j,                # em
            seed=r.randrange(1 << 30),                         # 供符号笔迹采样用
        )

    def _char_tile(self, ch, px, p):
        boost = 1.15 if ord(ch) in _SYM_CP else 1.0     # 符号字形偏小，放大一点才和汉字同高
        size = max(4, int(round(px * p["scale"] * boost)))
        # 合并字体里的数学符号是印刷体轮廓，走「骨架 → 变宽笔道」重绘，
        # 才能和周围手写字迹看起来是同一支笔写出来的。
        if handstroke.available(ch):
            tile, ox, oy = handstroke.tile(
                ch, size, self.ink + (p["alpha"],), self.font_path, seed=p.get("seed", 0))
            if tile is not None:
                return tile, ox, oy
        f = _ttf(self.font_path, size)
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
            return formula.measure(tk[1:-1], px, self.font_path, self.ink,
                                   self.math_font_path)[0]
        return font.getlength(tk)

    # -------------------------------------------------------- 渲染一个块
    def _line_box(self, line, font, px):
        """返回一行所需的 (宽, 基线以上, 基线以下)，公式会撑高行框。"""
        asc, desc = font.getmetrics()
        w = 0.0
        for kind, val in _atoms(line):
            if kind == "f":
                fw, fa, fd = formula.measure(val, px, self.font_path, self.ink,
                                             self.math_font_path)
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

        # 逐行按自身高度排布：纯文本行保持 20.2pt 行距，含分式/根号的行自动加高。
        # 若改成「按最高行统一行距」，一条嵌套分式会把整段撑成双倍行距，反而更难看。
        metrics = [self._line_box(line, font, px) for line in lines]
        heights, baselines = [], []
        for _w, asc, desc in metrics:
            h_i = max(base_lh, asc + desc + 4)
            b_i = int(max(h_i * 0.78, asc + 2))
            h_i = int(max(h_i, b_i + desc + 2))
            heights.append(h_i)
            baselines.append(b_i)

        # 行距微抖动：完全等距看起来像排版软件，真人写不出这么准的间距。
        # 幅度取 ±0.9px（≈±0.22pt），只破坏「机械等距」的观感，不影响阅读。
        gaps = [int(round(self.rng.uniform(-0.9, 0.9) * self.jitter))
                for _ in lines]

        # 画布先给足（标点悬挂会略超列宽，抖动还有随机外扩），渲染后按墨迹裁边
        need = max([m[0] for m in metrics] + [width_pt * SCALE])
        W = int(math.ceil(need * 1.06 + 8))
        H = max(1, sum(heights) + sum(gaps))
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))

        y = 0
        for i, line in enumerate(lines):
            if line:
                canvas.alpha_composite(
                    self._render_line(line, font, px, baselines[i], W / SCALE, heights[i]),
                    (0, y))
            y += heights[i] + gaps[i]
        out = canvas.crop((0, 0, W, min(H, y)))
        bb = out.getbbox()
        if bb:
            out = out.crop((0, 0, min(out.width, bb[2] + 2), out.height))
        return out, out.size[0] / SCALE, out.size[1] / SCALE

    def _render_line(self, line, font, px, baseline, width_pt, line_h):
        W = int(width_pt * SCALE)
        lh = int(line_h)
        img = Image.new("RGBA", (W, lh), (0, 0, 0, 0))
        ascent = font.getmetrics()[0]
        # 每行起笔位置：极其微小的左右偏移（±1.2px≈±0.3pt），只为打散「每行左端齐平」的机械感。
        # 加 2px 基底偏移，避免负值时贴图左边被画布裁掉。
        x = 2.0 + self.rng.uniform(-1.2, 1.2) * self.jitter
        atoms = _atoms(line)
        n = max(1, len(atoms))
        right = -1e9
        for k, (kind, val) in enumerate(atoms):
            if kind == "f":
                # 公式整体当一个原子：不可拆、不与前后字重叠
                fw, _fa, _fd = formula.measure(val, px, self.font_path, self.ink,
                                               self.math_font_path)
                if x < right:
                    x = right
                formula.draw(img, val, x, baseline, px, self.font_path, self.ink,
                             self.math_font_path)
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

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
# 手写字要比印刷正文大一号：真人很难把字写得跟印刷体一样小、一样匀。
# 13.5pt 偏大（整段占页太多、符号也跟着变大），12.4pt 是「明显大于印刷体」又不夸张的值。
DEFAULT_SIZE_PT = 12.4
DEFAULT_LINE_PT = 23.0         # 行距保持约 1.85 倍字号
INK = (16, 16, 18)             # 墨色 RGB：黑笔（alpha 逐字抖动）
PAGE_LEFT_PT = 64.0
PAGE_RIGHT_PT = 468.0
BLOCK_GAP_PT = 15.0
PAGE_BOTTOM_PT = 676.0         # 触发降字号重排的底限（页码在 ~690pt）

NO_LINE_START = "，。；、：？！）》」』”’%,.;:?!)]}"
NO_LINE_END = "（《「『“‘([{"
_TILE_PAD = 6                   # 单字贴图四周的透明内边距（px），排版时不能算进字距
_JITTER_INFLATE = 0.02          # 逐字扰动（缩放/位移/防重叠右推）平均把行宽撑大的比例
_LINE_HEIGHT_DAMP = 0.80        # 超高行（分式/根号）额外高度的打折系数：
                                # 1.0=完全按内容给（行距忽大忽小），0.55=压太紧会与下一行相撞
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
            # 单字旋转要小：±1.2° 叠在整行剪切上会让字看起来「东倒西歪」
            rot=r.uniform(-0.22, 0.22) * j,
            # 墨色深浅抖动收窄：220–255 时同段落里有字明显偏淡，看起来「粗细不统一」
            alpha=int(r.uniform(242, 255) if j > 0 else 250),
            rgb=tuple(max(0, min(255, c + r.randint(-3, 3) * (1 if j > 0 else 0)))
                      for c in self.ink),
            adv_jit=r.uniform(-0.12, 0.12) * j,                # em
            seed=r.randrange(1 << 30),                         # 供符号笔迹采样用
        )

    def _char_tile(self, ch, px, p):
        # 符号原本给 1.15 倍补偿「字形偏小」，但实测合并进来的 Ω/∑/∫ 墨迹本来就
        # 比汉字大 7–31%，再放大就是「符号比字还大」。改为不放大。
        boost = 1.0
        size = max(4, int(round(px * p["scale"] * boost)))
        # 合并字体里的数学符号是印刷体轮廓，走「骨架 → 变宽笔道」重绘，
        # 才能和周围手写字迹看起来是同一支笔写出来的。
        if handstroke.available(ch):
            tile, ox, oy = handstroke.tile(
                ch, size, self.ink + (p["alpha"],), self._font_for(ch),
                seed=p.get("seed", 0))
            if tile is not None:
                return tile, ox, oy
        f = _ttf(self._font_for(ch), size)
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

    def _font_for(self, ch):
        """拉丁字母/数字用「正常人笔写」的字体，汉字用毛笔手写体。

        马善政楷书的拉丁小写是连笔刷体（`a` 带大尾巴、`g` 是单层圈），
        正常人写不出来；龙藏体的拉丁是规整的硬笔写法，更像日常手写。
        所以正文里 ASCII 字母数字走 math_font_path，CJK 仍走 font_path。
        """
        if ch.isascii() and ch.isalnum():
            return self.math_font_path
        return self.font_path

    # ------------------------------------------------------ 换行与标点悬挂
    def wrap(self, text, width_pt):
        """按「行尾参差最小」做换行（动态规划），而不是贪心塞满。

        贪心换行的毛病：遇到放不下的长 token（尤其公式）就整段提前断，
        行尾留下大片空白，还容易挤出只有两三个字的孤行——看起来很不自然。
        这里改为最小化「每行右侧空白量的平方和」，行宽自然趋于均匀，
        同时保留中文行首/行尾禁则。
        """
        px = self.size_pt * SCALE
        font = _ttf(self.font_path, int(round(px)))
        # 逐字扰动会把实际行宽撑大几个百分点，换行时按同一比例预留，
        # 否则渲染出来会越过列宽（旧版就是因此每行右侧被裁掉约三成）。
        max_px = width_pt * SCALE / (1.0 + _JITTER_INFLATE * self.jitter)
        lines = []
        for para in _paragraphs(text):
            if not para.strip():
                lines.append("")
                continue
            lines.extend(self._wrap_para(_tokenize(para), font, px, max_px))
        return lines

    def _wrap_para(self, tokens, font, px, max_px):
        """单段落的最优换行：cost = Σ(行尾空白)²，末行空白按 0.25 权重。"""
        n = len(tokens)
        if not n:
            return []
        widths = [self._token_width(tk, font, px) for tk in tokens]

        # 允许在 i 前断行的条件：不是行首禁则字符，且 i-1 不是行尾禁则字符
        def can_break(i):
            if i <= 0 or i >= n:
                return True
            tk = tokens[i]
            prev = tokens[i - 1]
            if tk and tk[0] in NO_LINE_START:
                return False
            if prev and prev[-1] in NO_LINE_END:
                return False
            return True

        INF = float("inf")
        best = [INF] * (n + 1)
        back = [0] * (n + 1)
        best[0] = 0.0
        for i in range(1, n + 1):
            w = 0.0
            for j in range(i - 1, -1, -1):
                w += widths[j]
                if w > max_px * 1.35:      # 再往前只会更宽，剪枝
                    break
                if not can_break(j):
                    continue
                if best[j] == INF:
                    continue
                slack = max_px - w
                # 超宽行重罚（但允许标点悬挂造成的轻微超出）
                if slack < 0:
                    cost = (slack * 6.0) ** 2 + 1e6
                else:
                    weight = 0.25 if i == n else 1.0   # 末行不追求填满
                    cost = weight * slack * slack
                total = best[j] + cost
                if total < best[i]:
                    best[i] = total
                    back[i] = j
        if best[n] == INF:                 # 兜底：放不下就退回贪心
            return self._wrap_greedy(tokens, font, px, max_px)
        cuts, i = [], n
        while i > 0:
            cuts.append(i)
            i = back[i]
        cuts.append(0)
        cuts.reverse()
        return ["".join(tokens[cuts[k]:cuts[k + 1]]) for k in range(len(cuts) - 1)]

    def _wrap_greedy(self, tokens, font, px, max_px):
        lines, cur = [], ""
        for tk in tokens:
            if cur and self._token_width(cur + tk, font, px) > max_px:
                lines.append(cur)
                cur = tk
            else:
                cur += tk
        if cur:
            lines.append(cur)
        return lines

    # -------------------------------------------------------- 公式支持
    def _advance(self, ch, px):
        """单字前进宽度（px）。拉丁字母/数字走 math_font_path，汉字走 font_path。

        必须和 _font_for 保持一致：正文里 ASCII 用龙藏体渲染，若还按马善政量宽度，
        字距会错位（马善政的拉丁是连笔刷体，宽度和龙藏差很多）。
        """
        return _ttf(self._font_for(ch), int(round(px))).getlength(ch)

    def _text_width(self, s, px):
        return sum(self._advance(ch, px) for ch in s)

    def _token_width(self, tk, font, px):
        """一个 token 的排版宽度：普通文本按字宽累加，$...$ 交给公式层量。"""
        if _is_formula(tk):
            return formula.measure(tk[1:-1], px, self.font_path, self.ink,
                                   self.math_font_path)[0]
        return self._text_width(tk, px)

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
                w += self._advance(val, px)
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

        # 逐行按自身高度排布，但对「超出基准的部分」打折：分式/根号行确实需要更多
        # 垂直空间，但完全按内容给（最高到基准的 1.7 倍）会让整段行距忽大忽小。
        # 基线固定在行框的 0.78 处，超高的公式向上探入上一行的空白带——
        # 真人写带分式的笔记正是这样，行距只是略松，不会出现大段空白。
        metrics = [self._line_box(line, font, px) for line in lines]
        heights, baselines = [], []
        for _w, asc, desc in metrics:
            need = asc + desc
            h_i = base_lh + max(0.0, need - base_lh) * _LINE_HEIGHT_DAMP
            h_i = max(base_lh, h_i)
            # 基线取「行框 0.78」与「保证 descender 不越界」的较大值，
            # 但上限不超过基准行的基线，避免把整段撑开。
            b_i = int(min(max(h_i * 0.78, asc + 2), base_lh * 0.78 + 2))
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
            adv = self._advance(ch, px) * p["scale"] + p["adv_jit"] * px + drift
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
            # 剪切系数 sh 就是倾斜角的正切：0.15 约 8.5°，整行会明显歪；
            # 收到 0.021（约 1.2°）只留一点自然的行倾，不破坏版面观感。
            sh = self.rng.uniform(-0.021, 0.021) * self.jitter
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


def _paragraphs(text):
    """把文本切成段落：空行才是硬分段，单个换行按软换行处理（交给列宽决定断点）。

    手写排版里「换行」应当由行宽决定；源文本里的单个 \\n 往往只是编辑器折行，
    若当成硬换行会出现「一行只有两三个字」的短行，看起来很不自然。
    """
    out, buf = [], []
    for raw in text.split("\n"):
        if raw.strip():
            buf.append(raw.strip())
        else:
            if buf:
                out.append(_join_lines(buf))
                buf = []
            out.append("")
    if buf:
        out.append(_join_lines(buf))
    return out


def _join_lines(lines):
    """软换行拼接：中文之间直接相连，拉丁词之间补一个空格。"""
    s = lines[0]
    for nxt in lines[1:]:
        if not s or not nxt:
            s += nxt
            continue
        if s[-1].isascii() and s[-1].isalnum() and nxt[0].isascii() and nxt[0].isalnum():
            s += " " + nxt
        else:
            s += nxt
    return s


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

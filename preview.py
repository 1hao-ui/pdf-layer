# -*- coding: utf-8 -*-
"""preview.py — 不依赖 PyMuPDF 的可视化预览：合成 A4 → 手写层 → 输出 PNG。

用途：在没有装 PyMuPDF 的环境里也能直观看效果；demo.py 才是注回真实 PDF 的完整流程。
"""
import os

from PIL import Image, ImageDraw, ImageFont

import hwrender as H
import symbols

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
os.makedirs(OUT, exist_ok=True)

A4_W_PT, A4_H_PT = 595.0, 842.0
SCALE = H.SCALE                     # 4x 超采样

HEAD = "第四章 三相电路                4.3.5 三相负载的功率计算"
PRINT_LINES = [
    "在图 4.03 所示的三相电路中，已知线电压为 380V，负载 Z_a = 3 + j4 Ω，",
    "Z_b = 8 − j6 Ω，试求电路的总有功功率、无功功率、视在功率及线电流。",
]

TEXT = r"""4.3.5 解：图4.03中，三角形联结的$Z_b$与星形联结的$Z_a$并联接入线电压$380\,\mathrm{V}$的三相电源。
星形部分：相电压$U_{\mathrm{相}}=\frac{380}{\sqrt{3}}\approx 220\,\mathrm{V}$，$|Z_a|=\sqrt{3^2+4^2}=5\,\Omega$，相电流$I=\frac{220}{5}=44\,\mathrm{A}$。
有功$P_a=3\times 44^2\times 3\approx 17.4\,\mathrm{kW}$，无功$Q_a=3\times 44^2\times 4\approx 23.2\,\mathrm{kvar}$（感性）。
三角形部分：相电压等于线电压$380\,\mathrm{V}$，$|Z_b|=\sqrt{8^2+6^2}=10\,\Omega$，相电流$I_p=\frac{380}{10}=38\,\mathrm{A}$，
线电流$I_l=\sqrt{3}\times 38\approx 65.8\,\mathrm{A}$。有功$P_b=3\times 38^2\times 8\approx 34.7\,\mathrm{kW}$，无功$Q_b=-3\times 38^2\times 6\approx -26.0\,\mathrm{kvar}$（容性）。
总功率$P=\sum_{i=1}^{n}P_i\approx 52.1\,\mathrm{kW}$，$Q=23.2-26.0\approx -2.8\,\mathrm{kvar}$，
$S=\sqrt{P^2+Q^2}\approx 52.2\,\mathrm{kVA}$，$I=\frac{S}{\sqrt{3}\times 380}\approx 79.2\,\mathrm{A}$，$\cos\varphi\approx 0.999$。"""


def _sys_font(px):
    for p in (r"C:\Windows\Fonts\simsun.ttc", r"C:\Windows\Fonts\msyh.ttc",
              r"C:\Windows\Fonts\simhei.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, px)
            except Exception:
                continue
    return ImageFont.load_default()


def main():
    W, Hh = int(A4_W_PT * SCALE), int(A4_H_PT * SCALE)
    page = Image.new("RGB", (W, Hh), (255, 255, 255))
    d = ImageDraw.Draw(page)

    # 模拟「已印刷」的题面（不是本项目生成的部分）
    d.text((64 * SCALE, 100 * SCALE), HEAD, font=_sys_font(int(11 * SCALE)), fill=(20, 20, 20))
    y = 140
    for ln in PRINT_LINES:
        d.text((64 * SCALE, y * SCALE), ln, font=_sys_font(int(10.5 * SCALE)), fill=(20, 20, 20))
        y += 20

    # 手写层
    hand = symbols.make_han_hand(size_pt=H.DEFAULT_SIZE_PT, seed=20261118)
    img, w_pt, h_pt = hand.render_block(TEXT, width_pt=404)
    print("手写块: %.1f x %.1f pt | 顶部 y=226pt | 底部 %.1f pt (页底限 676pt)"
          % (w_pt, h_pt, 226 + h_pt))

    top = 226
    page_rgba = page.convert("RGBA")
    page_rgba.alpha_composite(img, (int(64 * SCALE), int(top * SCALE)))
    page = page_rgba.convert("RGB")

    png = os.path.join(OUT, "preview_page.png")
    page.save(png)
    print("预览图 ->", png)

    # 局部 2x（只取手写区，验证公式细节）
    k = SCALE
    box = (int(56 * k), int(216 * k), min(W, int(500 * k)), min(Hh, int((top + h_pt) * k)))
    crop = page.crop(box)
    crop.resize((crop.width // 2, crop.height // 2), Image.LANCZOS).save(
        os.path.join(OUT, "preview_detail.png"))
    print("局部图 ->", os.path.join(OUT, "preview_detail.png"))


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""intake.py — 把扫描件/数位板导出变成「已对齐、已切好的字块」。

自动做了这些事（用户只管上传）
------------------------------
1. **读数**：JPG/PNG/BMP/TIFF 直接读；PDF 按页渲染成图。
   透明底的数位板导出走 alpha 通道，白底扫描走灰度阈值。
2. **找四角黑块**：在模板标称位置附近搜黑色方块，找不到就用整页
   （要求整页导出、A4 比例），不会因为差几毫米就切错。
3. **透视校正**：四角做单应变换（手机斜拍也能拉正），只重采样一次。
4. **自动阈值**：Otsu 从图像自己算墨迹阈值，浅灰格线/参考字被滤掉；
   也能手填一个值。
5. **逐格切字**：按 layout.json 的网格切，每格缩放到统一的 240×240
   标准格，保留字在格内的相对大小与位置（'一' 天生比 '國' 小，不能
   被逐字拉满）。
6. **体检**：每格报告墨迹量、外框、是否贴边、是否空白，供界面标红。
"""
from __future__ import annotations

import math
import os

import numpy as np
from PIL import Image

CELL_NORM = 240            # 归一化格边长（px）
MIN_INK_RATIO = 0.0015     # 墨迹占比低于它 = 空白格


# ---------------------------------------------------------------- 读数
def load_images(paths):
    """把图片/PDF 展开成 [(名字, PIL.Image)]；PDF 每页一张。"""
    out = []
    for path in paths or []:
        ext = os.path.splitext(str(path))[1].lower()
        if ext == ".pdf":
            try:
                import fitz
                doc = fitz.open(path)
                for i in range(doc.page_count):
                    pix = doc[i].get_pixmap(dpi=300, alpha=True)
                    img = Image.frombytes("RGBA", (pix.width, pix.height),
                                          pix.samples)
                    out.append(("%s#%d" % (os.path.basename(path), i + 1), img))
                doc.close()
            except Exception as e:
                raise RuntimeError("PDF 读不了：%s（%r）" % (path, e))
            continue
        try:
            img = Image.open(path)
            img.load()
            if img.mode not in ("L", "RGBA", "RGB"):
                img = img.convert("RGBA" if "A" in img.mode else "RGB")
            out.append((os.path.basename(str(path)), img))
        except Exception as e:
            raise RuntimeError("图片读不了：%s（%r）" % (path, e))
    return out


def gray_and_alpha(img):
    """返回 (灰度图, alpha 图或 None)。透明底的导出以 alpha 为墨迹。"""
    if img.mode == "RGBA":
        alpha = img.getchannel("A")
        if _alpha_has_ink(alpha):
            bg = Image.new("RGBA", img.size, (255, 255, 255, 0))
            bg.alpha_composite(img)
            return bg.convert("L"), alpha
        return img.convert("L"), None
    return img.convert("L"), None


def _alpha_has_ink(alpha):
    """alpha 是否真的当过墨迹层（全不透明 = 只是通道占位）。"""
    a = np.asarray(alpha, dtype=np.uint8)
    dark = float((a < 200).mean())
    return 0.0005 < dark < 0.98


# ---------------------------------------------------------------- 阈值
def otsu(gray):
    """Otsu 阈值；返回 0–255 的整数。"""
    a = np.asarray(gray, dtype=np.uint8)
    hist = np.bincount(a.ravel(), minlength=256).astype(np.float64)
    total = hist.sum()
    if total <= 0:
        return 128
    omega = np.cumsum(hist) / total
    mu = np.cumsum(hist * np.arange(256)) / total
    mu_t = mu[-1]
    denom = omega * (1.0 - omega)
    denom[denom == 0] = 1e-12
    sigma = (mu_t * omega - mu) ** 2 / denom
    # +1：argmax 落在「最亮的前景像素」上，切字用的是 g < threshold，
    # 不 +1 会把等于该灰度的墨迹整片漏掉（实测纯色测试块会被判成空白）。
    return int(min(255, max(1, int(np.argmax(sigma)) + 1)))


# ---------------------------------------------------------------- 对齐
def _marker_centers(W, H, k):
    m = 22.0 + 14.0 / 2.0          # 22 页边 + 半块
    return [(m * k, m * k),
            ((595.0 - m) * k, m * k),
            (m * k, (842.0 - m) * k),
            ((595.0 - m) * k, (842.0 - m) * k)]


def find_markers(gray, max_shift_pt=45.0):
    """在四角附近找黑方块中心；找不全返回 None。

    做法：在每角的搜索区里用积分图找「最黑的一块」——14pt 实心方块在
    200–300dpi 下约有 1500–3000 个暗像素，手写笔画远没有这么密，所以
    不会把格里的字误当角标。找到后再在方框内取暗像素质心，精度到像素。
    """
    a = np.asarray(gray, dtype=np.uint8)
    H, W = a.shape
    k = W / 595.0
    if abs(H / float(W) - 842.0 / 595.0) > 0.14:
        return None
    dark = (a < 90).astype(np.int32)
    # 积分图：sum[y1,x1]-sum[y0,x1]-sum[y1,x0]+sum[y0,x0]
    ii = np.zeros((H + 1, W + 1), dtype=np.int64)
    ii[1:, 1:] = np.cumsum(np.cumsum(dark, axis=0), axis=1)

    def box_sum(x0, y0, x1, y1):
        return int(ii[y1, x1] - ii[y0, x1] - ii[y1, x0] + ii[y0, x0])

    win = max(6, int(round(14.0 * k * 1.9)))       # 方块外扩约 2 倍
    reach = int((11.0 + max_shift_pt) * k)
    corners = []
    for (tx, ty) in _marker_centers(W, H, k):
        rx0 = max(0, int(tx - reach))
        rx1 = min(W, int(tx + reach))
        ry0 = max(0, int(ty - reach))
        ry1 = min(H, int(ty + reach))
        best = (0, rx0, ry0)
        step = max(2, win // 12)
        for yy in range(ry0, max(ry0 + 1, ry1 - win), step):
            for xx in range(rx0, max(rx0 + 1, rx1 - win), step):
                v = box_sum(xx, yy, xx + win, yy + win)
                if v > best[0]:
                    best = (v, xx, yy)
        v, bx, by = best
        need = max(20, int((14.0 * k) ** 2 * 0.12))
        if v < need:
            return None
        sub = dark[by:by + win, bx:bx + win]
        ys, xs = np.where(sub > 0)
        if len(xs) < need:
            return None
        corners.append((float(np.median(xs + bx)), float(np.median(ys + by))))
    tl, tr, bl, br = corners
    if not (250 * k < tr[0] - tl[0] < 700 * k):
        return None
    if not (350 * k < bl[1] - tl[1] < 1000 * k):
        return None
    # 角标必须比理论位置偏移不大（防把正文大字块当角标）
    for (cx, cy), (tx, ty) in zip(corners, _marker_centers(W, H, k)):
        if abs(cx - tx) > (max_shift_pt + 15) * k \
                or abs(cy - ty) > (max_shift_pt + 15) * k:
            return None
    return corners


def _homography(src, dst):
    """求 H 使 H·src ≈ dst（src=理论坐标，dst=检出的像素坐标）。"""
    A, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        b.append(u)
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b.append(v)
    try:
        h = np.linalg.solve(np.asarray(A, dtype=np.float64),
                            np.asarray(b, dtype=np.float64))
    except np.linalg.LinAlgError:
        return None
    return tuple(float(x) for x in h)


def align(gray, centers, out_w=None):
    """按四角做单应变换，输出一张标准 A4 比例的整页灰度图。

    注意坐标系：``centers`` 是**输入图**的像素坐标；理论角标要按**输出图**
    的尺寸算（同为像素），否则会少乘一次 dpi 比例，整页被缩到 1/2.78。
    """
    H, W = gray.size[1], gray.size[0]
    W2 = out_w or W
    H2 = int(round(W2 * 842.0 / 595.0))
    k2 = W2 / 595.0
    theory = _marker_centers(W2, H2, k2)
    h = _homography(theory, centers)
    if h is None:
        return gray
    try:
        return gray.transform((W2, H2), Image.Transform.PERSPECTIVE, h,
                              resample=Image.Resampling.BICUBIC,
                              fillcolor=255)
    except Exception:
        return gray


def page_is_a4(gray, tol=0.12):
    H, W = gray.size[1], gray.size[0]
    return abs(H / float(W) - 842.0 / 595.0) <= tol


# ---------------------------------------------------------------- 切格
def cell_box(grid, col, row):
    x0 = grid["left"] + col * grid["cell_w"] + grid.get("pad", 1.5)
    y0 = grid["top"] + row * grid["cell_h"] + grid.get("pad", 1.5)
    x1 = grid["left"] + (col + 1) * grid["cell_w"] - grid.get("pad", 1.5)
    y1 = grid["top"] + (row + 1) * grid["cell_h"] - grid.get("pad", 1.5)
    return x0, y0, x1, y1


def _mask_from(crop_gray, crop_alpha, threshold):
    if crop_alpha is not None:
        a = np.asarray(crop_alpha, dtype=np.uint8)
        if (a < 200).mean() > 0.001:
            return a < 128
    g = np.asarray(crop_gray, dtype=np.uint8)
    return g < threshold


def denoise(mask):
    """去孤立噪点 + 去贴边黑线（角标/装订线误入格内）。"""
    if not mask.any():
        return mask
    p = np.pad(mask, 1, mode="constant")
    H, W = mask.shape
    n = np.zeros((H, W), dtype=np.int16)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            n += p[1 + dy:1 + dy + H, 1 + dx:1 + dx + W]
    out = mask & (n >= 1)
    if H > 6 and W > 6:
        out[:1, :] = False
        out[-1:, :] = False
        out[:, :1] = False
        out[:, -1:] = False
    return out


def normalize_cell(mask, size=CELL_NORM):
    """整格（不是墨迹外框）缩放到 size×size：保留字在格内的大小/位置。"""
    if mask.size == 0:
        return np.zeros((size, size), dtype=bool)
    im = Image.fromarray((mask.astype(np.uint8) * 255), mode="L")
    im = im.resize((size, size), Image.Resampling.BILINEAR)
    return np.asarray(im, dtype=np.uint8) > 96


def cell_metrics(mask):
    """一格字的体检数据（归一化格坐标，0–1）。"""
    H, W = mask.shape
    ys, xs = np.where(mask)
    n = int(len(xs))
    if n == 0:
        return {"ink": 0, "bbox": None, "h": 0.0, "w": 0.0,
                "bottom": None, "top": None, "cx": 0.5, "cy": 0.5,
                "edge": False, "blank": True}
    y0, y1 = int(ys.min()), int(ys.max())
    x0, x1 = int(xs.min()), int(xs.max())
    edge = (x0 <= 1 or y0 <= 1 or x1 >= W - 2 or y1 >= H - 2)
    return {
        "ink": n,
        "bbox": [x0, y0, x1, y1],
        "h": (y1 - y0 + 1) / float(H),
        "w": (x1 - x0 + 1) / float(W),
        "bottom": y1 / float(H - 1),
        "top": y0 / float(H - 1),
        "cx": (x0 + x1) / 2.0 / float(W - 1),
        "cy": (y0 + y1) / 2.0 / float(H - 1),
        "edge": bool(edge),
        "blank": n < MAX_BLANK_PX,
    }


MAX_BLANK_PX = int(CELL_NORM * CELL_NORM * MIN_INK_RATIO)


# ---------------------------------------------------------------- 整页
def prepare(gray, alpha, layout, auto_align=True, threshold=None):
    """对齐 + 定阈值，返回 (处理后灰度, alpha, 页信息)。"""
    info = {"aligned": False, "threshold": None, "scale": 1.0, "note": ""}
    k = gray.size[0] / 595.0
    if auto_align:
        centers = find_markers(gray)
        if centers:
            gray = align(gray, centers, out_w=gray.size[0])
            if alpha is not None:
                alpha = align(alpha, centers, out_w=alpha.size[0])
            info["aligned"] = True
            info["note"] = "四角对齐 OK"
        elif page_is_a4(gray):
            info["note"] = "未找到四角黑块，按整页 A4 处理"
        else:
            info["note"] = "未找到四角黑块且不是 A4 比例，按原图处理"
    else:
        info["note"] = "跳过对齐（按整页 A4 处理）"
    info["scale"] = gray.size[0] / 595.0
    if threshold in (None, "", "auto", "自动"):
        info["threshold"] = otsu(gray)
    else:
        try:
            info["threshold"] = int(threshold)
        except (TypeError, ValueError):
            info["threshold"] = otsu(gray)
    return gray, alpha, info


def slice_page(gray, alpha, layout, page_index, threshold,
               size=CELL_NORM):
    """切一页，返回 [(字符, 归一化掩膜, 体检数据)]。"""
    grid = layout["grid"]
    chars = layout.get("pages")[page_index].get("chars") or []
    k = gray.size[0] / 595.0
    W, H = gray.size
    out = []
    for i, ch in enumerate(chars):
        col, row = i % grid["cols"], i // grid["cols"]
        x0, y0, x1, y1 = cell_box(grid, col, row)
        box = (max(0, int(x0 * k)), max(0, int(y0 * k)),
               min(W, int(x1 * k)), min(H, int(y1 * k)))
        if box[2] <= box[0] or box[3] <= box[1]:
            out.append((ch, np.zeros((size, size), dtype=bool),
                        cell_metrics(np.zeros((size, size), dtype=bool))))
            continue
        cg = gray.crop(box)
        ca = alpha.crop(box) if alpha is not None else None
        m = _mask_from(cg, ca, threshold)
        m = denoise(m)
        nm = normalize_cell(m, size)
        out.append((ch, nm, cell_metrics(nm)))
    return out


def slice_all(images, layout, threshold=None, auto_align=True,
              size=CELL_NORM, progress=None):
    """整批切字。

    参数
    ----
    images  图片路径列表（顺序 = 第 1 遍第 1 页、第 1 遍第 2 页…）
    layout  template.make_layout() 的返回值（或 load_layout 读出来的）
    threshold  None/'auto' = 自动；数字 = 手动
    """
    pages = layout.get("pages") or []
    if not pages:
        raise ValueError("layout 里没有页信息")
    loaded = load_images(images)
    if len(loaded) != len(pages):
        # 允许只传一部分：按顺序对应前 N 页
        if len(loaded) > len(pages):
            loaded = loaded[:len(pages)]
    result = {"pages": [], "cells": [], "warnings": []}
    for idx, (name, img) in enumerate(loaded):
        gray, alpha = gray_and_alpha(img)
        if layout["page"].get("dpi") and abs(
                gray.size[0] - 595.0 * layout["page"]["dpi"] / 72.0) > 500:
            result["warnings"].append(
                "%s：分辨率 %dx%d，建议 %ddpi 导出" %
                (name, gray.size[0], gray.size[1], layout["page"]["dpi"]))
        gray, alpha, info = prepare(gray, alpha, layout,
                                    auto_align=auto_align,
                                    threshold=threshold)
        cells = slice_page(gray, alpha, layout, idx, info["threshold"], size)
        for ch, mask, meta in cells:
            meta.update({"char": ch, "page": idx, "pass": pages[idx]["pass"],
                         "page_in_pass": pages[idx]["page"]})
            result["cells"].append((ch, mask, meta))
        result["pages"].append({"index": idx, "name": name, **info,
                                "chars": len(cells)})
        if progress:
            progress(idx + 1, len(loaded))
    if len(loaded) < len(pages):
        result["warnings"].append(
            "只提供了 %d 张图，模板有 %d 页；缺的页会记为缺字" %
            (len(loaded), len(pages)))
    return result


def save_cells(cells, out_dir, prefix=""):
    """把切好的掩膜存成 PNG（白底黑字，便于人工看）。"""
    os.makedirs(out_dir, exist_ok=True)
    paths = {}
    for i, (ch, mask, meta) in enumerate(cells):
        name = "%s%03d_%s" % (prefix, i, "U%04X" % ord(ch))
        p = os.path.join(out_dir, name + ".png")
        Image.fromarray((mask * 255).astype(np.uint8), mode="L").save(p)
        paths[name] = p
    return paths


def contact_sheet(cells, out_png, cols=14, cell=64, label_font=None):
    """带字符标注的联系表：一眼看出哪格切歪/空白。"""
    from PIL import ImageDraw
    rows = max(1, int(math.ceil(len(cells) / float(cols))))
    sheet = Image.new("RGB", (cols * cell, rows * (cell + 14)), "white")
    d = ImageDraw.Draw(sheet)
    for i, (ch, mask, meta) in enumerate(cells):
        c, r = i % cols, i // cols
        x, y = c * cell, r * (cell + 14)
        tile = Image.fromarray((mask * 255).astype(np.uint8), mode="L")
        tile = tile.convert("RGB")
        sheet.paste(tile, (x + 2, y + 14))
        color = (200, 40, 40) if meta.get("blank") or meta.get("edge") \
            else (40, 40, 40)
        d.text((x + 2, y + 1), ch, fill=color,
               font=label_font or _default_font(11))
    sheet.save(out_png)
    return out_png


def _default_font(px):
    from PIL import ImageFont
    for p in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, px)
    return ImageFont.load_default()

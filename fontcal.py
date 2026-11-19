# -*- coding: utf-8 -*-
"""fontcal.py — 多套字库的观感归一化（墨色 / 粗细 / 字号 / 清晰度 / 中心位置）。

为什么需要
----------
多套真人笔迹字库（ZTi 系列）来自字嗨导出与手稿扫描，与主字体（马善政）在
五个维度上天然不一致。实测同一参考字号下：

    字体        墨色   笔画   字高   垂直偏移  水平偏移
    马善政      202    6.0    46     -23.0     0.0
    ZTi-01      144    2.0    29     -23.5     +0.5
    ZTi-Scan-01 148    3.0    33     -21.5    -10.5

也就是说 ZTi 的字小了 35–60%、笔画细 2–3 倍、墨色淡 30%，直接混排会一眼看出
「这不是同一个人的字」。本模块在渲染前对每套字库做一次性校准：

  · 字号：按实测墨迹高把字形缩放到与主字体一致
  · 粗细：笔画细的用 alpha 伽马 + 轻微膨胀补足（比描边自然）
  · 墨色：按平均 alpha 比值做 alpha 增益，补到同一浓度
  · 中心位置：按墨迹中心相对基线的偏移，逐套做像素级位移
  · 清晰度：只测不校（实测差异 <6%，强行锐化反而会出现振铃）

校准值离线测量一次后缓存成 JSON，渲染时直接查表，不影响运行时性能。
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CAL_PATH = os.path.join(HERE, "fonts", "fontcal.json")

# 参考字号（px）与采样字：这些字在主字体与 ZTi 字库里都存在
REF_PX = 54
SAMPLE = "相电压功率流生电的"

# 目标值：以主字体（马善政）为基准
DEFAULTS = {
    "size_scale": 1.0,      # 字号缩放（乘到渲染字号上）
    "alpha_gain": 1.0,      # 墨色增益（乘到 alpha 上，>1 更深）
    "alpha_gamma": 1.0,     # alpha 伽马（<1 提亮中间调）
    "thicken": 0.0,         # 笔画加粗（px，形态学膨胀半径）
    "dy": 0.0,              # 垂直位移（px，正=下移）
    "dx": 0.0,              # 水平位移（px）
}


def _metrics(path, ref_px=REF_PX, sample=SAMPLE):
    """测量一套字体的五项指标（与 _refs/measure_fonts.py 同算法）。"""
    import statistics

    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    alphas, runs, heights, sharps, dys, dxs = [], [], [], [], [], []
    for ch in sample:
        try:
            f = ImageFont.truetype(path, ref_px)
            bbox = f.getbbox(ch)
        except Exception:
            continue
        if bbox is None:
            continue
        pad = 8
        w = max(1, bbox[2] - bbox[0]) + 2 * pad
        h = max(1, bbox[3] - bbox[1]) + 2 * pad
        img = Image.new("L", (w, h), 0)
        ImageDraw.Draw(img).text((pad - bbox[0], pad - bbox[1]), ch, fill=255, font=f)

        vals = [v for v in img.getdata() if v > 0]
        if not vals:
            continue
        alphas.append(statistics.mean(vals))

        rr = []
        for y in range(h):
            run = 0
            for x in range(w):
                if img.getpixel((x, y)) > 40:
                    run += 1
                elif run:
                    rr.append(run)
                    run = 0
            if run:
                rr.append(run)
        if rr:
            runs.append(statistics.median(rr))

        bb = img.getbbox()
        if bb:
            heights.append(bb[3] - bb[1])
            baseline = pad - bbox[1] + bbox[3]
            dys.append(((bb[1] + bb[3]) / 2.0) - baseline)
            # 水平：墨迹中心相对「字面中心」的偏移（Scan 系列手稿切字常偏左）
            face_cx = pad + (bbox[2] - bbox[0]) / 2.0
            dxs.append(((bb[0] + bb[2]) / 2.0) - face_cx)

        lap = img.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0],
                                            scale=1, offset=0))
        lv = [v for v in lap.getdata() if v > 0]
        if lv:
            sharps.append(statistics.pvariance(lv) ** 0.5)

    def med(x):
        return statistics.median(x) if x else 0.0

    return {
        "alpha": med(alphas),
        "stroke": med(runs),
        "height": med(heights),
        "sharp": med(sharps),
        "dy": med(dys),
        "dx": med(dxs),
    }


def calibrate(fonts, base_font, save=True, rounds=3):
    """以 base_font 为基准，闭环迭代算出每套字体的校准系数。

    单次测量不够：放大字号会同时改变墨色与笔画（抗锯齿像素变多），
    所以先粗算、再按「校准后实测」反复修正，2–3 轮即可收敛。
    """
    base = _metrics(base_font)
    table = {}
    for path in fonts:
        if not os.path.exists(path):
            continue
        key = os.path.basename(path)
        if os.path.abspath(path) == os.path.abspath(base_font):
            table[key] = dict(DEFAULTS)
            continue
        c = dict(DEFAULTS)
        try:
            m = _metrics(path)
        except Exception:
            table[key] = c
            continue
        # 初值
        if m["height"] > 0:
            c["size_scale"] = base["height"] / m["height"]
        if m["alpha"] > 0:
            c["alpha_gain"] = min(2.2, base["alpha"] / m["alpha"])
        # 笔画：先用膨胀补足差距（1px 膨胀约增粗 2px 游程），剩下的用伽马微调
        if m["stroke"] > 0:
            gap = base["stroke"] - m["stroke"]
            c["thicken"] = max(0.0, min(3.0, gap / 2.0))
            ratio = base["stroke"] / max(0.1, m["stroke"] + 2 * c["thicken"])
            c["alpha_gamma"] = max(0.6, min(1.0, 1.0 / (ratio ** 0.3)))
        c["dy"] = base["dy"] - m["dy"]
        c["dx"] = base["dx"] - m["dx"]

        # 闭环修正：只迭代「字号 / 墨色 / 笔画」——这三项互相影响
        # （放大后抗锯齿像素变多、膨胀后又变深）。位置（dy/dx）必须在固定字号下测，
        # 否则字号一变基线也跟着变，误差会累积放大（实测会让 dy 涨到 40+ px）。
        for _ in range(rounds + 2):
            mm = _metrics_scaled(path, c)
            if not mm or mm["height"] <= 0:
                break
            err_h = base["height"] / mm["height"] if mm["height"] else 1.0
            c["size_scale"] *= err_h
            if mm["alpha"] > 0:
                err_a = base["alpha"] / mm["alpha"]
                # 收敛步长取 0.5：膨胀与增益会互相影响，步长太大会在目标值附近震荡
                c["alpha_gain"] = min(2.6, c["alpha_gain"] * (1.0 + (err_a - 1.0) * 0.5))
            # 笔画：按实测游程差继续加/减膨胀量（1px 膨胀 ≈ +2px 游程）
            if mm["stroke"] > 0 and base["stroke"] > 0:
                gap = base["stroke"] - mm["stroke"]
                c["thicken"] = max(0.0, min(3.0, c["thicken"] + gap / 2.0 * 0.6))

        # 收尾：用最后一轮实测值做一次精确的墨色修正（前面是阻尼迭代，会留残差）
        mm = _metrics_scaled(path, c)
        if mm and mm["alpha"] > 0 and base["alpha"] > 0:
            c["alpha_gain"] = min(2.6, max(0.3, c["alpha_gain"] * base["alpha"] / mm["alpha"]))
            # 再验一次，若仍有残差就再修一次（实测两次足够收敛到 ±3%）
            mm2 = _metrics_scaled(path, c)
            if mm2 and mm2["alpha"] > 0:
                c["alpha_gain"] = min(2.6, max(0.3,
                                            c["alpha_gain"] * base["alpha"] / mm2["alpha"]))

        for k in ("size_scale", "alpha_gain", "alpha_gamma", "thicken"):
            c[k] = round(c[k], 4)
        for k in ("dy", "dx"):
            c[k] = round(c[k], 3)
        table[key] = c
    if save:
        try:
            os.makedirs(os.path.dirname(CAL_PATH), exist_ok=True)
            with open(CAL_PATH, "w", encoding="utf-8") as fp:
                json.dump({"ref_px": REF_PX, "sample": SAMPLE,
                           "base": os.path.basename(base_font),
                           "fonts": table}, fp, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return table


def _metrics_scaled(path, cal):
    """按给定校准系数渲染后再测——用于闭环修正。"""
    import statistics

    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    alphas, runs, heights, dys, dxs = [], [], [], [], []
    for ch in SAMPLE:
        try:
            px = max(4, int(round(REF_PX * cal["size_scale"])))
            f = ImageFont.truetype(path, px)
            bbox = f.getbbox(ch)
        except Exception:
            continue
        if bbox is None:
            continue
        pad = 8
        w = max(1, bbox[2] - bbox[0]) + 2 * pad
        h = max(1, bbox[3] - bbox[1]) + 2 * pad
        img = Image.new("L", (w, h), 0)
        ImageDraw.Draw(img).text((pad - bbox[0], pad - bbox[1]), ch, fill=255, font=f)
        # 应用笔画加粗（与渲染路径一致）
        th = cal.get("thicken", 0.0)
        if th >= 0.5:
            k = min(9, max(3, int(round(th)) * 2 + 1))
            img = img.filter(ImageFilter.MaxFilter(k))
        # 应用墨色/伽马
        lut = [max(0, min(255, int(round(255.0 * ((i / 255.0) ** cal["alpha_gamma"])
                                        * cal["alpha_gain"])))) for i in range(256)]
        img = img.point(lut)

        vals = [v for v in img.getdata() if v > 0]
        if not vals:
            continue
        alphas.append(statistics.mean(vals))

        rr = []
        for y in range(h):
            run = 0
            for x in range(w):
                if img.getpixel((x, y)) > 40:
                    run += 1
                elif run:
                    rr.append(run)
                    run = 0
            if run:
                rr.append(run)
        if rr:
            runs.append(statistics.median(rr))

        bb = img.getbbox()
        if bb:
            heights.append(bb[3] - bb[1])
            baseline = pad - bbox[1] + bbox[3]
            dys.append(((bb[1] + bb[3]) / 2.0) - baseline)
            face_cx = pad + (bbox[2] - bbox[0]) / 2.0
            dxs.append(((bb[0] + bb[2]) / 2.0) - face_cx)

    def med(x):
        return statistics.median(x) if x else 0.0

    return {"alpha": med(alphas), "stroke": med(runs), "height": med(heights),
            "dy": med(dys), "dx": med(dxs)}


_cache = None


def load():
    """读缓存的校准表；没有就返回空表（等价于不校准）。"""
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(CAL_PATH, encoding="utf-8") as fp:
            data = json.load(fp)
        _cache = data.get("fonts", {})
    except Exception:
        _cache = {}
    return _cache


def params_for(path):
    """取某套字体的校准参数（缺失则用默认值 = 不校准）。"""
    if not path:
        return dict(DEFAULTS)
    got = load().get(os.path.basename(path))
    if not got:
        return dict(DEFAULTS)
    p = dict(DEFAULTS)
    p.update(got)
    return p


def apply_alpha(img, gain, gamma):
    """对 RGBA 贴图做墨色增益 + 伽马（保形，不改形状与抗锯齿）。"""
    if abs(gain - 1.0) < 1e-6 and abs(gamma - 1.0) < 1e-6:
        return img
    lut = []
    for i in range(256):
        v = 255.0 * ((i / 255.0) ** gamma) * gain
        lut.append(max(0, min(255, int(round(v)))))
    out = img.copy()
    out.putalpha(img.getchannel("A").point(lut))
    return out


def apply_thicken(img, px):
    """把笔画加粗 px 个像素（形态学膨胀，用 MaxFilter）。

    为什么需要：ZTi 系列笔画只有 2–3px，而主字体（马善政毛笔体）是 6px。
    放大字号不会让笔画变粗（只是整体变大），伽马也只改墨色深浅——
    真正让笔画变粗必须做形态学膨胀。用 MaxFilter 而非描边，
    边缘过渡更自然，不会出现硬边。
    """
    if px < 0.5:
        return img
    k = int(round(px)) * 2 + 1
    k = min(9, max(3, k))
    a = img.getchannel("A")
    from PIL import ImageFilter
    a2 = a.filter(ImageFilter.MaxFilter(k))
    out = img.copy()
    out.putalpha(a2)
    return out


def apply_shift(img, dx, dy):
    """整体平移贴图（用于对齐墨迹中心），空出的区域保持透明。"""
    if abs(dx) < 0.5 and abs(dy) < 0.5:
        return img
    from PIL import Image
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.alpha_composite(img, (int(round(dx)), int(round(dy))))
    return out


def finish_tile(img, ox, oy, cal):
    """贴图收尾：笔画加粗 + 墨色增益 + 伽马 + 中心位置补偿。

    返回 (贴图, ox, oy)。位移通过修改 ox/oy 实现——这样不会改变贴图尺寸，
    也不会把画布外的墨迹裁掉。

    注意 dx 的语义：它补偿的是「字形墨迹中心相对字面中心的偏差」，
    典型场景是手稿切字偏左（Scan 系列实测偏左 10px）。改 ox 会让字形在
    字框内移动，但 advance 不变——这正是我们想要的（字形居中，字距不变）。
    dx 上限压到 ±0.35em，避免个别字体把字形推得离字框太远。
    """
    if img is None:
        return None, ox, oy
    out = apply_thicken(img, cal.get("thicken", 0.0))
    out = apply_alpha(out, cal["alpha_gain"], cal["alpha_gamma"])
    dx = cal["dx"]
    dy = cal["dy"]
    # 用贴图宽度做上限，防止极端值把字推飞
    lim = max(2.0, img.width * 0.35)
    dx = max(-lim, min(lim, dx))
    return out, ox + dx, oy + dy


if __name__ == "__main__":
    import sys
    repo = HERE
    base = os.path.join(repo, "fonts", "MaShanZheng-Regular-merged.ttf")
    fonts = []
    zti = os.path.join(repo, "fonts", "ZTi")
    if os.path.isdir(zti):
        fonts += [os.path.join(zti, n) for n in sorted(os.listdir(zti))
                  if n.endswith((".otf", ".ttf"))]
    if os.path.exists(base):
        fonts.insert(0, base)
    if len(sys.argv) > 1 and sys.argv[1] == "measure":
        for p in fonts:
            print(os.path.basename(p), _metrics(p))
        sys.exit(0)
    table = calibrate(fonts, base)
    print("%-22s %10s %10s %10s %8s %8s" % ("字体", "字号倍", "墨色倍", "伽马", "dy", "dx"))
    for k, v in table.items():
        print("%-22s %10.3f %10.3f %10.3f %8.2f %8.2f"
              % (k, v["size_scale"], v["alpha_gain"], v["alpha_gamma"], v["dy"], v["dx"]))
    print("\n已写入", CAL_PATH)

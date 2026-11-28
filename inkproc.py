# -*- coding: utf-8 -*-
"""inkproc.py —— 墨迹后处理（渲染层与公式层共用）。

`hwrender._thin_alpha_tile` 与 `formula._thin_formula_image` 原本是**两份逐行
相同**的实现，只有「strength 怎么从档位算出来」不同。两份代码一旦漂移，
正文数字和公式数字的粗细就会不一致（用户反馈过「公式里的数字偏粗」）。
这里把像素运算收敛到一处，两边的档位换算留在各自模块里。

算法：距离变换求每个墨迹像素到背景的距离 → 9×9 局部最大值近似「局部笔画
半径」→ 只把「粗笔画的外沿 1px」乘一个 <1 的系数，再用 0.5px 高斯回糊，
避免收边后出现硬边。细笔画（角标、分式上下）完全不碰，保持原墨色。
"""
from PIL import Image, ImageFilter


def thin_rgba(img, strength):
    """按笔画粗细自适应收边，返回新的 RGBA 图（不修改入参）。

    strength ≥ 0.5，越大收得越多：0.5 约等于「只收一点点」，2.0 是默认档。
    只收粗笔画外沿；mask 为空的图原样返回。
    """
    import numpy as np
    from scipy import ndimage

    a = np.asarray(img.getchannel("A")).astype(np.float32)
    mask = a > 30
    if not mask.any():
        return img
    dist = ndimage.distance_transform_edt(mask)
    rad = ndimage.maximum_filter(dist, size=9)          # 局部笔画半径（px）
    thick = rad >= max(1.6, 2.4 - 0.4 * strength)       # 只挑粗笔画
    edge = (dist <= 1.4) & thick                        # 粗笔画的外沿
    factor = np.where(edge, max(0.35, 1.0 - 0.25 * strength), 1.0)
    a2 = np.clip(a * factor, 0, 255)
    blurred = np.asarray(Image.fromarray(a2.astype(np.uint8), "L")
                         .filter(ImageFilter.GaussianBlur(0.5))).astype(np.float32)
    a2 = np.minimum(a, blurred)          # 不扩散到原墨迹之外（避免字高变大）
    a2 = np.clip(a2 * 1.25, 0, 255)      # 小字号抗锯齿会显灰，补一点墨色
    out = img.copy()
    out.putalpha(Image.fromarray(a2.astype(np.uint8), "L"))
    return out

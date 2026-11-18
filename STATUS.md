# PDF-Layer · 本地重建状态（2026-11-18 夜）

> 依据 `D:\qq下载\交接说明(1).md`。交接文档描述的运行环境是 Linux 沙箱（`/workspace`、`/root/uploads`），
> 本机是 Windows + Python 3.9（fitz/PIL/fontTools 均已就绪），因此按文档第 6 节方案**从零重建**，
> 没有沿用那边的临时脚本。

## 1. 已经落地的部分

| 项 | 位置 | 状态 |
|---|---|---|
| 四款开源手写字体（CDN 下载，字节数已核对） | `fonts/MaShanZheng-Regular.ttf` 5.86MB、`LongCang-Regular.ttf` 5.16MB、`LiuJianMaoCao-Regular.ttf` 4.95MB、`ZhiMangXing-Regular.ttf` 4.06MB | ✅ |
| **6.5 字形合并**（解决符号缺字） | `merge_symbols.py` → `fonts/*-merged.ttf` | ✅ 每款注入 20 个符号字形 |
| **6.1 同字异形** | `hwrender.py` `Hand._char_params` | ✅ 基线±0.8px / dx±0.6px / 字号±3.5% / 单字旋转±1.2° / alpha 220–255 / RGB±6 |
| **6.2 笔画层扰动** | `hwrender.py` `Hand._render_line` + `_sine_warp` | ✅ 整行正弦弯 A=1.5px λ=300px、±0.15° 剪切、行首宽行尾紧的字距渐变 |
| 排版（贪心换行 + ASCII 不拆 + 标点悬挂） | `hwrender.py` `Hand.wrap` | ✅ 行首禁则、行尾禁则都已实现；另加防重叠护栏 |
| 注回 PDF | `hwrender.py` `paste_into_pdf` | ✅ `insert_image(overlay=True)` |
| 端到端验证 | `demo.py` → `out/demo_page.pdf`、`out/detail2x.png` | ✅ 4.3.5 全段（含 Ω √ ≈ ² ³ φ）已写入 A4 |

## 2. 符号缺字是怎么解决的（原交接稿的 P2）

三款中文字体都是 **7015 字符的 CJK 子集**，缺 `Ω φ ω √ ≈ ∠ ² ³ ·`。
`merge_symbols.py` 的做法：从 `C:\Windows\Fonts\seguisym.ttf`（Segoe UI Symbol，7536 字符，符号全覆盖）
取出目标字号的字形，用 `DecomposingRecordingPen` 递归展开复合字形，按 unitsPerEm 比例缩放后
写进 glyf 表，并补 hmtx 与 cmap(format 4/12)。输出 `*-merged.ttf` 自带中文 + 符号，
渲染时不再需要逐字符回退 DejaVuSans —— **「中文手写 + 符号印刷体」的混排感从根上消掉了**。
符号另加 1.15 倍字号补偿（`_SYM_CP`），因为 Segoe 符号字面比汉字小一号。

## 3. 本机缺的东西（真正的阻塞点）

1. **原始扫描 PDF**：`1794984643773700345-*.pdf`（4 页《电工学》第四章三相电路，无文字层）。本机 C:/D: 均未找到，`D:\qq下载` 里没有同名文件。
2. 交接稿里的 `render_hw3.py` 在上一环境的临时目录，本机不存在；其真实价值是**每页 7 个块的起始 y 坐标**（p1=138、p2=172、p3=226、p4=256），这些数字已抄进第 4 节，脚本本身不重要。
3. `pdftoppm`（poppler）未安装 —— 已用 `fitz.Page.get_pixmap(dpi=120)` 等价替代，不影响。

## 4. 拿到源 PDF 后的接续步骤

```powershell
cd C:\Users\admin\Documents\deepseek-harness\default-workspace\pdf-layer
python -c "import fitz;d=fitz.open(r'<SRC>');[d[i].get_pixmap(dpi=120).save(f'out/p{i+1}.png') for i in range(len(d))]"
```

然后按页给 `hwrender.Hand.render_block` 传 7 段文本，`top_pt` 用交接稿的表（138/172/226/256），
`bottom > 676pt` 时降字号重排（`render_pages` 已支持逐块累加 `BLOCK_GAP_PT=15`）。

## 5. 还没做的（交接稿 P3/P4）

- **P3 版面自动分析**：`page.get_drawings()` + 视觉模型出 `{bbox, 类型, 可写入}`，再与既有块 bbox 求交做碰撞检测。本机没有视觉模型通道，需要用 `ego_*` 浏览器读 PDF 渲染图，或让模型直接看 `out/p*.png`。
- **P4 笔迹轨迹合成**：RNN（Graves 2013）/ fontTools 笔画中心线变宽描边 / pix2pix。二期。
- 遗留口径问题（交接稿第 7 节）：4.3.1 的 1.5kW 是每相还是三相合计；4.2.2 用 381V 还是取整 380V。**未定稿前不写死。**

## 6. 关键参数（当前生效值）

`SCALE=4`、`size_pt=10.9`、行高 `20.2pt`、墨色 `(30,44,70)` alpha 220–255、
左边距 `64pt`、正文宽 `404pt`、右边界 `468pt`、块间距 `15pt`、页面底限 `676pt`、
`random.seed=20261118`（可复现）。

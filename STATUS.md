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
| 端到端验证 | `preview.py` → `out/preview_page.png`；`demo.py` → `out/demo_page.pdf` | ✅ 4.3.5 全段（含公式）已写入 A4 |
| **公式支持（2026-11-18 二次修复）** | `formula.py` + `vendor/mthw/` | ✅ 分式/根式/上下标/∑∫上下限/矩阵/cases，行内 `$...$` 混排 |

## 1.1 二次修复记录（2026-11-18 晚）

接手后修掉的问题，按严重度：

| 问题 | 根因 | 修复 |
|---|---|---|
| **每行右侧约 1/3 文字被裁掉** | `_render_line` 的防重叠护栏把贴图的 6px 透明内边距算进了字距，每个字被推开一个 padding（56px 而非 44px），字距从 11pt 撑到 15.4pt，行宽膨胀约 40% | 改用贴图实际墨迹 bbox（`tile.getbbox()`）反推墨迹绝对位置，只在墨迹真会重叠时右推 |
| 行宽仍比 wrap 估算宽 6.7% | 抖动（缩放/位移/字距）会让实际行宽略超估算 | `wrap` 按 `_JITTER_INFLATE=0.02` 预留；画布先给足再按墨迹裁边，任何字号/种子都不裁字 |
| 长行右侧被画布切掉 | 画布固定按列宽开，标点悬挂/抖动一超出就被裁 | 画布按 `max(行宽)*1.06+8` 开，渲染后裁到墨迹右缘 |
| `demo.py` 预览图坐标错 | pt/px 混用（`226` 当 px 用），且 `crop.width*2//2` 等于没放大 | 统一 `k = dpi/72` 换算；真正 2x；产物改名 `detail2x.png` 与文档一致 |
| 公式打不出来 | 手写字体没有 OpenType MATH 表，画不出分式/根号结构 | 引入 `formula.py`，复用 MarkdownToHandwrite（MIT）的 2D 布局内核，符号表改回真希腊字符 |
| 合并字体缺大运算符 | `SYMS` 只并了 25 个符号，缺 `∑ ∫ ∏ ∈ ⊂ ⇒ ⟨⟩` 等 | `SYMS` 扩到 45 个，实测零缺失 |

**验收数据**：字距 11.00pt（设计值一致）；纯文本块 399.0×160.0pt，右缘 1594/1596 不裁切；公式块（10 条公式）0 空渲染；17 个数学符号全部有墨迹；9.5/10.9/12.0 三种字号 × 3 个种子均无裁切；含公式块渲染 0.02s/次（带缓存）。

## 1.2 三次修改记录（2026-11-18 深夜）

| 问题 | 根因 | 修复 |
|---|---|---|
| 符号不是手写体 | 合并进字体的 Ω √ ≈ × ° ∑ π φ 是 Segoe UI Symbol 的**印刷体轮廓** | 新增 `handstroke.py`：栅格化 → Zhang-Suen 取骨架 → 变宽笔道重绘（起收笔渐细/笔压抖动/轻微旋转）；`∠ ≤ ≥ ° ∘` 骨架拓扑会坏，改人工描路径 |
| 上标 ² ³ 被撑成正常大小 | 用 PIL `font.getbbox()` 当墨迹框，而它返回的是**布局框**（≈整个 em 框） | 改为栅格化后取 alpha bbox 作为真实墨迹框 |
| 墨色偏蓝黑 | 原墨色 `(30,44,70)` | 改成黑笔 `(16,16,18)` |
| 公式里 `I` 读成 `9` | 马善政楷书的连笔拉丁 `I` 形似 9，而电流 I 出现频率极高 | 公式字体改用龙藏体（`Hand.math_font_path`，同样 OFL 手写体），实测 `I=220/5=44 A`、`UI cos φ`、`kW`、`Z_b` 均可辨 |
| 公式符号仍是印刷体 | 公式内核 `_render_with_pillow` 逐字 `draw.text` | `handstroke.inline_patch()` 替换该方法，符号分支走笔迹 |
| 行距忽大忽小 | 含分式/根号的行把行框撑高 | 逐行按自身高度排布（纯文本 20.2pt，公式行自动加高）；试过「统一行距」但一条嵌套分式会把整段撑成双倍行距，已回退 |

**踩过的坑**：给公式字体加 `stroke_width` 加粗以对齐正文毛笔体的笔画重量 → 加粗后笔画变等宽、像无衬线印刷体，反而丢掉手写感，已放弃。

## 1.3 四次微调（2026-11-19）

| 项 | 原值 | 现值 | 说明 |
|---|---|---|---|
| 字间上下偏移 `dy` | ±0.8px（±0.2pt） | **±0.3px（±0.075pt）** | 原来看起来每个字各写各的，收敛后像同一支笔 |
| 行距抖动 | 无（完全等距） | **±1px（±0.25pt）** | 真人写不出机械等距，幅度只够破坏规整感 |
| 行首偏移 | `uniform(0, 3.0)`px（单向 0–0.75pt） | **2.0 ± 1.2px（0.2–0.8pt，双向）** | 原来每行左端齐平，加极小参差 |

位置：`dy` 在 `_char_params`；行距抖动在 `render_block` 的 `gaps`；行首偏移在 `_render_line` 的起笔 `x`（+2px 基底，避免负值把贴图左边裁掉）。

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
- **公式层的风格统一**：公式笔画来自上游内核（`vendor/mthw`），与正文 `Hand` 是两套扰动参数，放大看能看出差异；要统一得把 `Hand` 的扰动接进公式内核的渲染路径。
- **跨平台 / 授权**：`merge_symbols.py` 依赖 `C:\Windows\Fonts\seguisym.ttf`（微软专有），合并产物不可再分发，且 Linux/macOS 直接失效；应换 DejaVu Sans 或 Noto Sans Symbols（OFL）。

## 6. 关键参数（当前生效值）

`SCALE=4`、`size_pt=10.9`、行高 `20.2pt`、墨色 `(30,44,70)` alpha 220–255、
左边距 `64pt`、正文宽 `404pt`、右边界 `468pt`、块间距 `15pt`、页面底限 `676pt`、
`random.seed=20261118`（可复现）。

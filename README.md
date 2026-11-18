# PDF-Layer

把一段文字以**手写**的样子写进扫描版 PDF 的原版面空白处，并支持**行内数学公式**。

- 输入：一份无文字层的扫描 PDF + 待写内容（支持 `$...$` LaTeX 公式）
- 输出：保留原版式、在空白区叠加手写风格文本层的新 PDF

## 它做什么

| 能力 | 说明 | 状态 |
|---|---|---|
| 字迹合成 | 中文手写字体 + 逐字扰动（基线/字号/旋转/墨色）+ 整行正弦弯曲 | ✅ |
| 字形合并 | 把数学符号并入中文手写字体，消除「中文手写 + 符号印刷体」混排 | ✅ |
| 公式排版 | 分式、根式、上下标、∑/∫ 上下限、矩阵、cases、自适应括号 | ✅ |
| 版面分析 | 自动定位空白区、碰撞检测 | ❌ 未做，坐标目前手工给 |
| 笔迹轨迹合成 | RNN/笔画中心线变宽描边 | ❌ 二期 |

## 快速开始

```powershell
pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\fetch_fonts.ps1   # 拉字体 + 合并符号
python demo.py                                               # 产出 out/demo_page.pdf
```

`fetch_fonts.ps1` 需要 PATH 里有 `python`；若你的机器只有 `py` 启动器，把脚本里的
`python` 改成 `py -3`，或直接用 `python merge_symbols.py` 手动合并。

## 用法

```python
import hwrender as H

FONT = "fonts/MaShanZheng-Regular-merged.ttf"
hand = H.Hand(FONT, size_pt=10.9, seed=20261118)

# 纯文本
img, w_pt, h_pt = hand.render_block("正文……", width_pt=404)

# 行内公式：$...$ 包住，作为整体参与排版（不会在中间断行）
img, w_pt, h_pt = hand.render_block(
    r"相电流 $I=\frac{220}{5}=44\,\mathrm{A}$，视在功率 $S=\sqrt{P^2+Q^2}$。",
    width_pt=404)

# 注回 PDF
H.paste_into_pdf("src.pdf", "out.pdf", {0: [(img, 226, w_pt, h_pt)]})
```

### 公式支持的子集

分式 `\frac{}{}`、根式 `\sqrt{}` / `\sqrt[n]{}`、上下标 `^{}` `_{}`、
大运算符 `\sum \int \prod`（带上下限）、`\lim`、矩阵 `pmatrix/bmatrix/vmatrix`、
`cases`、`\left( \right)` 自适应括号、希腊字母、`\mathrm{}`、`\text{}`（可夹中文）。
不支持：自定义宏包、`\newcommand`、TikZ。

## 实现要点

- **坐标单位**：外部一律 pt，内部 `SCALE=4` 超采样（1pt=4px）。
- **墨色**：黑笔 `(16,16,18)`，逐字 alpha 抖动 220–255。
- **排版护栏**：防重叠按「墨迹」边缘判断，不能把贴图的透明内边距算进字距
  （否则字距从 11pt 被撑到 15.4pt，行宽膨胀 40%，长行右侧被画布裁掉）。
- **画布宽度**：先按行宽给足再按实际墨迹裁边，保证任何字号/种子下都不裁字。
- **符号笔迹化**（`handstroke.py`）：合并字体里的 Ω √ ≈ × ° ∑ π φ 是 Segoe UI Symbol 的
  **印刷体轮廓**，直接贴会显得不是同一支笔。做法是栅格化 → Zhang-Suen 细化取骨架 →
  变宽笔道重绘（起收笔渐细、笔压抖动、轻微旋转）。少数骨架拓扑会坏的符号
  （`∠ ≤ ≥ ° ∘`）改用人工描的笔道路径。
  注意 PIL 的 `font.getbbox()` 是**布局框**不是墨迹框，用它缩放骨架会把上标 ² ³ 撑成正常大小。
- **公式**：手写字体没有 OpenType MATH 表，画不出分式/根号结构。因此复用
  [MarkdownToHandwrite](https://github.com/lyc07/MarkdownToHandwrite)（MIT）的
  2D 公式排版内核（`vendor/mthw/`，见 `vendor/LICENSE-MarkdownToHandwrite`），
  只保留「解析 → 盒式布局 → 笔画化渲染」，输出透明 RGBA + 基线，贴到同一张 4x 画布。
  上游默认把希腊字母映射成形近汉字，本项目的 `formula.py` 改回真希腊字符；
  公式里的符号同样走 `handstroke` 笔迹化。
- **公式字体**：公式里的拉丁字母/数字默认用龙藏体（`math_font_path`），
  因为马善政楷书的连笔 `I` 会被读成 `9`，而公式里 I（电流）出现频率极高。
- **行距**：逐行按自身高度排布——纯文本行保持 20.2pt，含分式/根号的行自动加高；
  不采用「按最高行统一行距」，否则一条嵌套分式会把整段撑成双倍行距。

## 目录

```
hwrender.py        手写层渲染引擎（换行 / 逐字扰动 / 行内公式 / 注回 PDF）
handstroke.py      符号笔迹化（骨架提取 + 变宽笔道重绘）
formula.py         公式层适配器（复用 vendor/mthw，改符号映射，带缓存）
merge_symbols.py   把数学符号字形并入中文手写字体
fetch_fonts.ps1    下载字体并触发合并
demo.py            端到端演示（合成页面 → 手写层 → 注回 PDF）
preview.py         不依赖 PyMuPDF 的预览（输出 PNG）
vendor/mthw/       第三方公式排版内核（MIT，见 vendor/LICENSE-MarkdownToHandwrite）
docs/交接说明.md   项目背景、调研结论、计算口径与 TODO
STATUS.md          当前进度与已知阻塞
```

## 已知问题

- **版面坐标是手工估算的**，不通用；通用化需要版面分析（P3）。
- 原始扫描件（4 页《电工学》第四章）不在仓库内，真实版面无法复现验证。
- 合并字体依赖 `C:\Windows\Fonts\seguisym.ttf`，**仅 Windows 可用**，且
  Segoe UI Symbol 为微软专有字体，**合并产物不可再分发**；若要跨平台/分发，
  应改用 DejaVu Sans 或 Noto Sans Symbols（均 OFL）。
- 公式层的笔画风格来自上游内核，与正文 `Hand` 的扰动是两套参数，观感可能略有差异。

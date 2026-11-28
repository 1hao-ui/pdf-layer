# -*- coding: utf-8 -*-
"""packaging/slim.py —— 打包瘦身规则（**唯一来源**）。

正式包 `ZTi.spec` 与自检包 `ZTi-smoke.spec` 都从这里取 excludes / TOC 过滤，
保证「验证的就是发布的」——两份 spec 各写一套必然漂移。

规则依据见各常量注释，实测数据见 工作区/核心重写/README.md。
"""

# 第三方库的**可选导入**带进来的东西，本项目从不调用：
#   pandas      ← pymupdf/table.py to_dataframe()
#   matplotlib  ← fontTools.varLib.plot / pens.freetypePen
#   lxml        ← fontTools.misc.etree / subset.svg（try/except 兜底）
#   contourpy/kiwisolver/cycler/dateutil/tzdata/pytz ← 上三者的依赖
EXCLUDES_OPTIONAL = [
    "pandas", "matplotlib", "lxml",
    "contourpy", "kiwisolver", "cycler", "dateutil", "pytz", "tzdata",
]

# scipy 只用到 ndimage；它的导入闭包 = scipy + _lib + ndimage + special
# （special 的 _ufuncs.pyd 链接 scipy.libs 里的 OpenBLAS，所以 scipy.libs 必须留）。
# 下面这些是 fontTools.varLib.interpolatableHelpers 的可选导入带进来的，
# 合计 ~37 MB，本项目不做曲线插值。
EXCLUDES_SCIPY = [
    "scipy.optimize", "scipy.linalg", "scipy.sparse", "scipy.spatial",
    "scipy.interpolate", "scipy.stats", "scipy.fft", "scipy.integrate",
    "scipy.odr", "scipy.signal", "scipy.cluster", "scipy.datasets",
    "scipy.constants", "scipy.io", "scipy.misc", "scipy.differentiate",
]

EXCLUDES = EXCLUDES_OPTIONAL + EXCLUDES_SCIPY

# pymupdf 里运行时用不到的数据/开发文件（合计 ~55 MB）：
#   layout/         版面分析 ONNX 模型，只有 pymupdf.layout 会读
#   mupdf-devel/    头文件 + 静态库（mupdfcpp64.lib）
DROP_DATA_PREFIXES = (
    "pymupdf/layout/",
    "pymupdf/mupdf-devel/",
)
DROP_SUFFIXES = (".lib", ".pdb", ".h", ".hpp", ".exp", ".iobj")


def keep_entry(entry):
    """TOC 条目要不要保留（entry[0] 是包内目标名）。"""
    name = str(entry[0]).replace("\\", "/").lower()
    for prefix in DROP_DATA_PREFIXES:
        if name.startswith(prefix) or ("/" + prefix) in ("/" + name):
            return False
    return not name.endswith(DROP_SUFFIXES)


def slim_toc(entries):
    kept = [e for e in entries if keep_entry(e)]
    dropped = len(entries) - len(kept)
    if dropped:
        print("[ZTi] 瘦身：TOC 去掉 %d 个文件" % dropped)
    return kept


def apply(analysis):
    """对 Analysis 结果就地裁剪，返回 analysis 方便链式调用。"""
    analysis.datas = slim_toc(analysis.datas)
    analysis.binaries = slim_toc(analysis.binaries)
    return analysis

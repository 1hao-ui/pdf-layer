# -*- coding: utf-8 -*-
"""packaging/build_common.py —— 两个 spec 共用的构建常量。

`ZTi.spec`（正式包）与 `ZTi-smoke.spec`（打包自检包）都从这里取路径约定、
隐藏导入与瘦身规则，避免「验证的配置」和「发布的配置」各写一套而漂移。
"""
import os
import sys

# spec 在 <项目>/源码/pdf-layer/packaging/：上一级是仓库根，再上一级是项目根。
# 用本文件位置推导（而不是 spec 里的 SPECPATH——被 import 的模块看不到那个全局量），
# 一律不写死绝对路径：项目一挪就失效。
_PKG = os.path.dirname(os.path.abspath(__file__))
APP = os.path.normpath(os.path.join(_PKG, os.pardir))
ROOT = os.path.normpath(os.path.join(APP, os.pardir, os.pardir))
FONTS = os.path.join(ROOT, "字体输出")
ICON_PATH = os.path.normpath(os.path.join(APP, "assets", "icon", "app.ico"))

# 动态导入（延迟 import / 字符串 import）必须显式列出，否则 PyInstaller 静态分析抓不到。
HIDDEN = [
    "mthw", "mthw.math_renderer", "mthw.handwriting", "mthw.config",
    "mthw.latex_parser",
    # 笔画收边是函数内延迟导入（hwrender/formula 共用），显式列出
    "inkproc",
    # 字库注册表是延迟导入（symbols/hwrender 里才 import），显式列出
    "fontlib", "fontlib.registry", "fontlib.charsets", "fontlib.template",
    "fontlib.intake", "fontlib.autotune", "fontlib.builder", "fontlib.preview",
    "fontlib.importer",
    # 激活系统：guard 和 ui 之间是延迟导入，显式列出避免 PyInstaller 漏掉
    "zti_lic", "zti_lic._ed25519", "zti_lic._pubkey", "zti_lic._b32",
    "zti_lic._fontkey", "zti_lic.fonts", "zti_lic._console",
    "zti_lic._endpoint", "zti_lic.license", "zti_lic.payload",
    "zti_lic.machine", "zti_lic.store", "zti_lic.trial", "zti_lic.net",
    "zti_lic.activation", "zti_lic.guard", "zti_lic.ui",
]

_PKG = os.path.dirname(os.path.abspath(__file__))
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)
import slim  # noqa: E402
EXCLUDES = slim.EXCLUDES


def slim_analysis(analysis):
    """对 Analysis 结果做瘦身裁剪。"""
    return slim.apply(analysis)

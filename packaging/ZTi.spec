# -*- mode: python ; coding: utf-8 -*-
"""ZTi 智能做题助手 v1.2 打包 spec：**单个 exe**。

1.2 起整个产品就是一个可执行文件：

    ZTi智能做题助手.exe     做题窗口 + 内置手写渲染 + 授权校验

不再打包的两个入口（源码仍保留，但不随发布包分发）：

    gui.py       手写工具窗口。作者自用测试 / 另做单独产品用，本产品不需要。
    ai_api.py    本地 AI 接口（HTTP 服务）。需要外部程序调用时用源码跑
                 `py -3 ai_api.py`，普通用户用不到，软件内的 AI 自动作答不依赖它。

历史：1.1 及以前打三个 exe、共享一份 _internal、各自独立授权。
"""
import os
import sys as _sys
import tempfile as _tempfile

# 路径约定 / 隐藏导入 / 瘦身规则全部来自 packaging/build_common.py，
# 与自检 spec 共用同一份配置。
if SPECPATH not in _sys.path:
    _sys.path.insert(0, SPECPATH)
import build_common as _bc  # noqa: E402

APP = _bc.APP
FONTS = _bc.FONTS
ICON_PATH = _bc.ICON_PATH

# ---------------------------------------------------------------------------
# 手写字体：构建时加密，包里不放明文 TTF
#
# 字体是这个产品最核心的东西。以前直接把 `字体输出\ZTi-Han|ZTi-Sym\*.ttf`
# 打进包，任何人拷走文件夹就能拿去 Word/Photoshop 用。
# 现在构建时封存成一个加密块，运行时由 zti_lic.fonts 在**授权校验通过之后**
# 解密还原到原位置——渲染链路一行不用改。
# ---------------------------------------------------------------------------
if APP not in _sys.path:
    _sys.path.insert(0, APP)
from zti_lic import fonts as _zfonts  # noqa: E402

_SEAL_DIR = _tempfile.mkdtemp(prefix="zti_fontseal_")
_SEALED = _zfonts.seal(
    os.path.join(FONTS),
    out_path=os.path.join(_SEAL_DIR, _zfonts.BLOB_NAME),
)
print("[ZTi] 字体已加密：%d 个文件 / %.2f MB -> %s"
      % (_SEALED["files"], _SEALED["bytes"] / 1048576, _SEALED["path"]))

DATAS = [
    (_SEALED["path"], "字体输出"),
    (os.path.join(FONTS, "字库索引.json"), "字体输出"),
    (os.path.join(APP, "fonts", "fontcal.json"), "fonts"),
    (os.path.join(APP, "fonts", "fontcal_glyph.json"), "fonts"),
    (os.path.join(APP, "vendor"), "vendor"),
    (os.path.join(APP, "assets"), "assets"),
]

# 唯一的主程序：做题窗口
a_main = Analysis([os.path.join(APP, "做题窗口.py")], pathex=[APP],
                  datas=DATAS, hiddenimports=_bc.HIDDEN,
                  excludes=_bc.EXCLUDES, noarchive=False)
_bc.slim_analysis(a_main)

pyz_main = PYZ(a_main.pure)
exe_main = EXE(pyz_main, a_main.scripts, [],
               exclude_binaries=True, name="ZTi智能做题助手", icon=ICON_PATH,
               debug=False, strip=False, upx=False, console=False)

coll = COLLECT(exe_main, a_main.binaries, a_main.datas,
               strip=False, upx=False, name="ZTi智能做题助手")

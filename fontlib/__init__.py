# -*- coding: utf-8 -*-
"""fontlib — 自建字库工作台（选字 → 模板 → 识别 → 微调 → 建库）。

这个包只做「怎么把一批手写扫描变成一套能用的字库」，不参与排版。
渲染侧通过 ``字体输出/字库索引.json`` 找到启用的字库，见 ``symbols.py``。

模块划分
--------
charsets.py  字符集来源：GB2312 分级、自定义文本、去掉已有字、剔除生僻字
template.py  分页 A4 书写模板（任意字表、任意格大小、角标定位）
intake.py    扫描件/数位板导出 → 网格对齐 → 自动阈值 → 逐格切字
autotune.py  墨迹度量、自动参数建议、逐字微调、渲染校准（size_scale/adv_scale）
builder.py   归一化后的字块 → 多套 glyf TTF + 报告 + 预览
preview.py   1:1 效果示例（同段样文并排）
registry.py  字体输出/字库索引.json 的读写、启用、回滚
session.py   一次建库过程的可续传工程（session.json）

命令行：``py -3 fontlib_cli.py --help``
界面：  ``gui.py`` 顶栏「字库工作台」，或 ``py -3 fontlab_gui.py``
"""

__version__ = "1.0.0"

__all__ = [
    "charsets", "template", "intake", "autotune", "builder",
    "preview", "registry", "session",
]

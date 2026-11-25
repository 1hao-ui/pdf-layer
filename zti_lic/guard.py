# -*- coding: utf-8 -*-
"""授权门禁：入口调用的就是这里的两个函数。

    check(product)    只查询，不弹窗、不退出 —— 业务代码和测试用
    require(product)  门禁 —— 三个入口 main() 开头各加一行

设计约束（对应 README 与 01 文档的红线）
----------------------------------------
1. 跳过开关由 require 自己判断，入口不用各写一遍；
2. 不依赖 tkinter（ai_api 是 headless 的），UI 由 ui 模块按需降级；
3. 任何异常都不能把堆栈甩给用户，失败一律给中文人话；
4. 只读不改用户数据 —— 授权失败绝不删除 out/ 和项目 JSON。
"""

from __future__ import annotations

import os
import sys
import time as _time

from . import license as lic, machine, store, trial
from ._pubkey import PUBLIC_KEY
from .errors import LicenseError, Reason, message_for

__all__ = [
    "Status",
    "check",
    "require",
    "is_bypassed",
    "EXIT_CODE",
    "BYPASS_FLAGS",
]

# 授权失败时的进程退出码（与正常错误 1 区分，便于批处理判断）
EXIT_CODE = 3

# 默认放行的自检开关。
#
# --smoke 只构建一次窗口就退出，不产出任何东西，放行是安全的。
#
# 不放行（需 ZTI_QA=1）的是 --selftest / --ai-smoke：这两个会真的加载 PDF、
# 生成手写作答、跑 AI 作答，等于"免授权出成品"。它们不在任何硬性要求里，
# 所以默认关掉。
#
# --self-test 虽然也属于自检，但它是 README 硬性要求 #2 明确要求"不能被授权挡住"
# 的条目（M6 的 28 项回归靠它），所以在 ai_api 的入口上单独放行，
# 见下面 ai_api 的接入说明。这个取舍记录在 设计.md 的"已知取舍"一节。
BYPASS_FLAGS = ("--smoke",)
QA_ENV = "ZTI_QA"
QA_FLAGS = ("--selftest", "--ai-smoke")


class Status:
    """一次授权检查的结果。

    故意不用 dataclass：`import dataclasses` 在冷启动下要 20ms 左右，
    而本包在三个 exe 的启动路径上，不值得为一个数据结构付这个钱。
    """

    __slots__ = ("product", "licensed", "trial", "days_left", "machine_code",
                 "payload", "reason", "message", "license_error", "trial_info")

    def __init__(self, product: str, licensed: bool = False,
                 trial: bool = False, days_left: int = 0,
                 machine_code: str = "", payload: dict | None = None,
                 reason: Reason = Reason.OK, message: str = "",
                 license_error: LicenseError | None = None,
                 trial_info: dict | None = None):
        self.product = product
        self.licensed = licensed
        self.trial = trial
        self.days_left = days_left
        self.machine_code = machine_code
        self.payload = payload
        self.reason = reason
        self.message = message
        self.license_error = license_error
        self.trial_info = trial_info if trial_info is not None else {}

    @property
    def usable(self) -> bool:
        """当前能不能继续用（授权有效或试用未过期）。"""
        return bool(self.licensed or self.trial)

    def describe(self) -> str:
        if self.licensed:
            serial = (self.payload or {}).get("serial") or ""
            expires = (self.payload or {}).get("expires_at")
            tail = "，有效期至 %s" % expires if expires else "，永久有效"
            if serial:
                return "已授权（%s）%s" % (serial, tail)
            return "已授权%s" % tail
        if self.trial:
            return "试用中，还剩 %d 天" % self.days_left
        return self.message or message_for(self.reason)

    def __repr__(self) -> str:
        return "Status(%r, licensed=%r, trial=%r, reason=%r)" % (
            self.product, self.licensed, self.trial,
            getattr(self.reason, "value", self.reason))


def check(product: str, app_version: str | None = None) -> Status:
    """查询某产品的授权状态。**不会弹窗，也不会退出进程。**

    可能抛出的异常都在内部收敛掉了；返回值里 reason 说明具体状态。
    """
    status = Status(product=product)
    try:
        status.machine_code = machine.machine_code()
    except Exception as exc:                     # 指纹采集异常不该让程序起不来
        status.reason = Reason.INTERNAL
        status.message = "无法读取本机硬件信息：%s" % exc
        return status

    # 1) 先看有没有有效授权
    try:
        doc = store.load_license()
        payload = lic.validate(doc, product=product,
                               machine_code=status.machine_code,
                               public_key=PUBLIC_KEY,
                               app_version=app_version)
        status.licensed = True
        status.payload = payload
        status.reason = Reason.OK
        return status
    except LicenseError as exc:
        status.license_error = exc
        status.reason = exc.reason
        status.message = exc.message
    except Exception as exc:                     # 兜底：绝不让堆栈冒到界面
        status.reason = Reason.INTERNAL
        status.message = "授权校验出现内部错误：%s" % exc
        status.license_error = LicenseError(Reason.INTERNAL, status.message)

    # 2) 没有有效授权，看试用
    try:
        info = trial.trial_status()
        status.trial_info = info
        status.days_left = int(info.get("days_left") or 0)
        status.trial = bool(info.get("active"))
    except Exception:
        status.trial = False
        status.days_left = 0

    if status.trial:
        # 试用中：保留 license_error 供界面显示"你有一份授权但不对"
        status.reason = Reason.TRIAL
    elif status.reason in (Reason.OK, Reason.NO_LICENSE):
        # 根本没有授权文件、试用也到期了 —— 这时"试用已结束"比
        # "未找到授权文件"更贴近用户实际感受，也更能指引他去激活。
        status.reason = Reason.TRIAL_EXPIRED
        status.message = message_for(Reason.TRIAL_EXPIRED)
    # 其它情况（签名不对 / 绑了别的机器 / 已过期）保留具体原因，更利于排查

    return status


def should_bypass(argv, bypass_flags) -> bool:
    """判断本次启动是否应当跳过授权。

    规则：
      - bypass_flags 里的开关（默认只有 --smoke）→ 放行；
      - QA_FLAGS 里的开关只有在环境变量 ZTI_QA=1 时才放行，
        否则照常要授权 —— 避免把"能免费出成品"的命令行开关变成后门。

    argv 为 None 时读真实的 sys.argv[1:]，因为入口都是
    `def main(argv=None)` + `sys.exit(main())` 这种写法。
    """
    if argv is None:
        argv = sys.argv[1:]
    args = list(argv)
    if any(flag in args for flag in (bypass_flags or ())):
        return True
    if any(flag in args for flag in QA_FLAGS):
        import os
        return os.environ.get(QA_ENV) == "1"
    return False


def _unseal_fonts() -> None:
    """解锁手写字体（授权/试用通过之后才调用）。

    失败不阻断启动：字体缺失时渲染器会自动回退到开源字体，
    程序还能用（只是字迹不对），比直接崩掉好。原因写进调试日志供排查。
    """
    try:
        from . import _console, fonts
        if not fonts.is_sealed_build():
            return
        started = _time.perf_counter()
        fonts.ensure_unsealed()
        _console.debug_log("fonts: 已解锁，耗时 %.1f ms"
                           % ((_time.perf_counter() - started) * 1000))
    except Exception as exc:
        try:
            from . import _console
            _console.debug_log("fonts: 解锁失败 %r" % (exc,))
        except Exception:
            pass


def require(product: str,
            *,
            ui: str = "auto",
            argv=None,
            bypass=BYPASS_FLAGS,
            app_version: str | None = None,
            notify_trial: bool = True) -> Status:
    """授权门禁。通过则返回 Status；不通过则走激活流程，仍失败就结束进程。

    参数
    ----
    product      授权单元。1.2 起整个产品只有一个："ziti"
    ui           "auto" | "tk" | "console"；auto 有 tkinter 就用图形界面
    argv         入口收到的原始参数（要在 argparse 之前传入，才能看到 --smoke）
    bypass       跳过授权的开关清单，默认 ("--smoke",)
    notify_trial 试用期内是否提示剩余天数
    """
    if should_bypass(argv, bypass):
        # 自检/诊断也要能跑真实字体路径，否则打包自检等于没测
        _unseal_fonts()
        return Status(product=product, licensed=False, trial=True,
                      reason=Reason.OK, message="自检模式，已跳过授权")

    # 启动时记一笔：数据目录在哪、能不能写。窗口程序没有控制台，
    # 出问题时这就是唯一的现场。
    try:
        from . import _console, paths
        base = paths.appdata_dir()
        probe = os.path.join(base, ".write_probe")
        with open(probe, "w", encoding="ascii") as f:
            f.write("x")
        os.unlink(probe)
        _console.debug_log("require: 数据目录可写 %s" % base)
    except Exception as exc:
        try:
            from . import _console, paths
            _console.debug_log("require: 数据目录不可写 %r -> %s"
                               % (paths.appdata_dir(), exc))
        except Exception:
            pass

    status = check(product, app_version=app_version)
    if status.licensed:
        _unseal_fonts()
        return status
    if status.trial:
        _unseal_fonts()
        if notify_trial:
            try:
                from . import ui as ui_mod
                ui_mod.maybe_notify_trial(status, mode=ui)
            except Exception:
                pass
        return status

    # 走到这里：没有授权，试用也过期了 → 必须激活
    try:
        from . import ui as ui_mod
        activated = ui_mod.run_activation(status, mode=ui, app_version=app_version)
    except SystemExit:
        raise
    except Exception as exc:                     # 界面本身崩了也要给个说法
        _print_fallback(status, "激活界面无法启动：%s" % exc)
        raise SystemExit(EXIT_CODE)

    if activated:
        refreshed = check(product, app_version=app_version)
        if refreshed.licensed:
            return refreshed

    raise SystemExit(EXIT_CODE)


def _print_fallback(status: Status, extra: str = "") -> None:
    """没有可用界面时的最后一道人话输出。"""
    from . import _console
    _console.ensure_utf8_console()
    lines = [
        "",
        "=" * 56,
        "  %s 尚未激活" % lic.product_display_name(status.product),
        "=" * 56,
        "  本机机器码：%s" % (status.machine_code or "（读取失败）"),
        "",
    ]
    if status.reason == Reason.WRONG_MACHINE:
        lines.append("  这份授权绑定的是另一台电脑。")
        lines.append("  请把上面的机器码发给作者，换取本机的授权。")
    elif status.message:
        lines.append("  %s" % status.message)
    if extra:
        lines.append("  %s" % extra)
    lines.extend([
        "",
        "  激活方式：运行程序后输入卡密，或导入授权文件。",
        "=" * 56,
        "",
    ])
    _console.eprint("\n".join(lines))

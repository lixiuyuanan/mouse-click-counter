# -*- coding: utf-8 -*-
"""测试隔离层：把 mouse_counter 的所有落盘入口一次性锁进临时目录。

为什么需要专门一个模块（血泪教训 v2.4.1 接手当天）：
`文档/项目说明.md` 的踩坑 14 只写了「测试脚本必须打桩 `_data_search_dirs`」，
但产品里还有一类路径读的是**模块级全局** `APP_DIR` —— `Stats.backup_dir()` /
`auto_backup()`。任何构造 `App` 再触发 `save(force=True)` 的探针（例如
`switch_theme`）都会把假数据写进用户真实的「文档\\鼠标点击次数记录\\backups\\」，
并可能按「保留 7 份」规则删掉真实自动备份。`_data_search_dirs` 打桩拦不住它。

本模块的 `isolate()` 是唯一的入口：任何测试在 import mouse_counter 之后
必须第一件事调用它。
"""
import os
import shutil
import tempfile
import time

_ACTIVE = []          # [(tmpdir, module_backup)]


class _Isolated(object):
    """临时目录 + 原值恢复。"""

    def __init__(self, mc, tmp):
        self.mc = mc
        self.tmp = tmp
        self.data_file = mc.DATA_FILE
        self.saved = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        for name, value in self.saved.items():
            setattr(self.mc, name, value)
        shutil.rmtree(self.tmp, ignore_errors=True)


def isolate(mc):
    """Redirect every write target of the module into a fresh temp dir."""
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="mct_"))
    saved = {}
    targets = {
        "EXE_DIR": tmp,
        "APP_DIR": tmp,
        "DATA_FILE": os.path.join(tmp, mc.DATA_NAME),
        "LOG_FILE": os.path.join(tmp, "error.log"),
        "TRAY_LOG": os.path.join(tmp, "tray.log"),
    }
    for name, value in targets.items():
        saved[name] = getattr(mc, name)
        setattr(mc, name, value)
    saved["_data_search_dirs"] = mc._data_search_dirs
    mc._data_search_dirs = lambda: []
    holder = _Isolated(mc, tmp)
    holder.saved = saved
    _ACTIVE.append(holder)
    return holder


class _FakeThread(object):
    """骗过 health_check：让它以为后台线程健在，于是不去装真钩子。"""

    def is_alive(self):
        return True

    def join(self, *a):
        return None


def silence_backend(mc):
    """让任何界面测试都不可能碰到真实 shell：不装钩子、不建托盘窗口、不发通知。

    下半段是硬保险：提权重启、注册表自启动、计划任务这三类调用会改用户机器状态
    （还会弹 UAC），任何测试都不该走到它们；一律替换成会大声失败的桩。
    """
    def _forbidden(*a, **kw):
        raise AssertionError("测试触发了禁止的副作用调用：%s" % (a[0] if a else "set_startup"))

    mc.start_worker = lambda sink: None
    mc.tray_modify = lambda *a, **kw: None
    mc.tray_remove = lambda: None
    mc.request_hook_reinstall = lambda: False
    mc.is_tray_window_alive = lambda: True
    mc.system_idle_seconds = lambda: 0.0
    mc.relaunch_elevated = lambda *a, **kw: _forbidden("relaunch_elevated")
    mc.set_startup = lambda *a, **kw: _forbidden("set_startup")
    mc.write_run_value = lambda *a, **kw: _forbidden("write_run_value")
    mc.create_logon_task = lambda *a, **kw: _forbidden("create_logon_task")
    mc.delete_logon_task = lambda *a, **kw: _forbidden("delete_logon_task")
    mc.WORKER["thread"] = _FakeThread()
    mc.HOOK_STATE["installed"] = True
    mc.HOOK_STATE["last_event"] = time.monotonic()
    mc.TRAY["hwnd"] = None
    mc.TRAY["ready"] = False
    mc.TRAY["sink"] = None


def seed_days(count, base=None, spread=True):
    """造 count 天的假计数，键齐全。"""
    import datetime
    base = base or datetime.date.today()
    out = {}
    for i in range(count):
        d = (base - datetime.timedelta(days=i)).isoformat()
        if spread:
            n = (i * 517) % 9000
        else:
            n = 0
        out[d] = {"left": n, "right": (i * 91) % 2200, "middle": (i * 13) % 400,
                  "x1": (i * 7) % 90, "x2": (i * 3) % 30, "wheel": (i * 11) % 300}
    return out


def new_stats(mc, raw=None):
    """在隔离目录里造一个 Stats；raw 不为空时先写文件再加载。"""
    import json
    if raw is not None:
        with open(mc.DATA_FILE, "w", encoding="utf-8") as f:
            if isinstance(raw, str):
                f.write(raw)
            else:
                json.dump(raw, f, ensure_ascii=False)
    return mc.Stats(mc.DATA_FILE)

# -*- coding: utf-8 -*-
"""渲染界面预览图（四页 × 深浅两套），输出到 预览图\\。

为什么不用 _pkg_tmp\\render_shot.py：它只改了 DATA_FILE/EXE_DIR，没改 APP_DIR，
而 Stats.backup_dir()/auto_backup() 读的正是模块级 APP_DIR —— 一跑就会把假数据
写进用户真实的「文档\\鼠标点击次数记录\\backups\\」，还可能按保留 7 份的规则
删掉真实自动备份（2026-10-14 踩过，见 文档/项目说明.md 踩坑 14）。
本脚本走 env.isolate()，所有落盘入口一次性锁进临时目录。

跑法：python -X utf8 测试/render_preview.py [版本号后缀]
"""
import ctypes
import ctypes.wintypes as wt
import datetime
import os
import queue
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import env                                               # noqa: E402
import mouse_counter as mc                               # noqa: E402

PW_RENDERFULLCONTENT = 0x00000002
OUT_DIR = os.path.join(ROOT, "预览图")
PAGES = ("home", "settings", "profiles", "about")
PAGE_CN = {"home": "首页", "settings": "设置", "profiles": "鼠标档案", "about": "关于"}


def _api():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
    user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    user32.FindWindowW.restype = ctypes.c_void_p
    user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
                                ctypes.c_uint]
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateDCW.restype = ctypes.c_void_p
    gdi32.CreateDCW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p,
                               ctypes.c_wchar_p, ctypes.c_void_p]
    return user32, gdi32


USER32, GDI32 = _api()


class BIH(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BIH), ("bmiColors", ctypes.c_uint32 * 3)]


def capture(hwnd, path):
    rect = wt.RECT()
    if not USER32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect)):
        raise AssertionError("GetWindowRect 失败")
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w < 50 or h < 50:
        raise AssertionError("窗口尺寸异常 %dx%d" % (w, h))
    screen = GDI32.CreateDCW("DISPLAY", None, None, None)
    mem = GDI32.CreateCompatibleDC(screen)
    bmp = GDI32.CreateCompatibleBitmap(screen, w, h)
    GDI32.SelectObject(mem, bmp)
    if not USER32.PrintWindow(ctypes.c_void_p(hwnd), mem, PW_RENDERFULLCONTENT):
        raise AssertionError("PrintWindow 失败 err=%s" % ctypes.get_last_error())
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BIH)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h          # 负值＝自上而下，省得翻转
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    if not GDI32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0):
        raise AssertionError("GetDIBits 失败")
    from PIL import Image
    img = Image.frombytes("RGB", (w, h), buf.raw, "raw", "BGRX")
    img.save(path)
    GDI32.DeleteObject(bmp)
    GDI32.DeleteDC(mem)
    GDI32.DeleteDC(screen)
    return w, h


def seed_stats(mc, env, theme):
    """造一份「像用了两个月」的数据：有侧键、有空档、有档案寿命设置。"""
    stats = env.new_stats(mc, {"version": 3, "settings": {
        "theme": theme, "disclaimer_ok": True,
        "profile_id": "p_main",
        "profiles": [{"id": "p_main", "name": "主力鼠标", "switch": "",
                      "since": (mc.today() - datetime.timedelta(days=58)).isoformat(),
                      "life": {"left": 20000000, "right": 20000000, "middle": 10000000,
                               "x1": 3000000, "x2": 3000000, "wheel": 20000000},
                      "labels": {"x1": "前进", "x2": "后退"}, "hidden": []},
                     {"id": "p_old", "name": "旧鼠标", "switch": "",
                      "since": "2026-05-01", "life": {}, "labels": {}, "hidden": ["x2"]}],
    }})
    t = mc.today()
    days = {}
    for i in range(60):
        d = (t - datetime.timedelta(days=i)).isoformat()
        if i % 11 == 5:                       # 偶尔整天空着（热力图里应显示「空」）
            continue
        base = (i * 617) % 9500
        days[d] = {"left": base, "right": (i * 91) % 2200, "middle": (i * 13) % 400,
                   "x1": (i * 7) % 90, "x2": (i * 3) % 30,
                   "wheel": (i * 11) % 300}
    days[t.isoformat()]["left"] = 3812        # 今天有个能看出环比的数
    days[(t - datetime.timedelta(days=1)).isoformat()]["left"] = 2600
    stats.profile_days["p_main"] = days
    stats.profile_days["p_old"] = {(t - datetime.timedelta(days=d)).isoformat():
                                   {"left": 1200 + d * 7, "right": 300, "middle": 20,
                                    "x1": 0, "x2": 0, "wheel": 60} for d in range(30)}
    stats.select_profile("p_main")
    return stats


def render(tag):
    import tkinter as tk
    os.makedirs(OUT_DIR, exist_ok=True)
    made = []
    try:
        mc.enable_dpi_awareness()
    except Exception:
        pass
    for theme in ("light", "dark"):
        guard = env.isolate(mc)
        env.silence_backend(mc)      # 预览绝不装真钩子、绝不挂托盘图标
        tmp = tempfile.mkdtemp(prefix="mc_prev_")
        try:
            stats = seed_stats(mc, env, theme)
            root = tk.Tk()
            title = "MC-PREVIEW-%s-%s" % (theme, os.getpid())
            app = mc.App(root, stats, queue.Queue(), silent=True)
            root.title(title)
            root.geometry("+%d+%d" % (int(root.winfo_screenwidth() * 0.06),
                                      int(root.winfo_screenheight() * 0.08)))
            for page in PAGES:
                app.show_page(page)
                root.update_idletasks()
                root.update()
                time.sleep(0.35)
                root.update()
                hwnd = USER32.FindWindowW(None, title)
                if not hwnd:
                    raise AssertionError("找不到预览窗口 %s" % title)
                path = os.path.join(OUT_DIR, "%s_%s_%s.png" % (tag, PAGE_CN[page],
                                                                "浅色" if theme == "light" else "深色"))
                w, h = capture(hwnd, path)
                made.append((os.path.basename(path), w, h))
            root.destroy()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            guard.close()
    for name, w, h in made:
        print("  写出  %-34s %dx%d" % (name, w, h))
    return made


if __name__ == "__main__":
    tag = sys.argv[1] if len(sys.argv) > 1 else "v" + mc.APP_VERSION
    render(tag)
    print("PREVIEW_DONE")

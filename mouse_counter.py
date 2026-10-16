# -*- coding: utf-8 -*-
"""鼠标点击次数记录

纯 ctypes 调用 Windows 底层鼠标钩子(WH_MOUSE_LL)，无第三方依赖。
记录 左键 / 右键 / 中键 / 侧键1 / 侧键2 的每日点击次数，另记滚轮格数（上/下合并，不计入点击总数）。
统计当天、近7天日均、近一个月每日热力与总计（亿级缩写显示），支持鼠标档案与寿命预测、
按键显示管理、自动+手动备份、开机自启动，单实例运行。
v2.0.0：顶部快捷栏（首页/设置/鼠标档案/关于）+ 鼠标档案寿命预测 + 滚轮计数 + 亿级缩写 + 备份体系。
v2.1.0：托盘右键菜单支持切换鼠标档案，菜单定位到鼠标指针右上方。
v2.3.0：近28天热力日历（5×7，非本月淡化）、深色模式（跟随系统/手动）、
使用说明内嵌到关于页，交付改为单 exe（图标内嵌，不再依赖 config 文件夹）。
v2.4.0：隐藏系统标题栏，标题+最小化/关闭融合为内置顶栏（可拖拽、双击最小化）；
热力日历无记录格改显示小号「空」字。
v2.4.1：UI 精修——标题栏放大去色差；KPI 去彩条统一蓝灰边框、箭头并入标签行；
寿命数值改中性色只留圆点；热力日历降饱和 20%、改圆点+数字、删图例与文字解释。
数据/配置统一放在用户「文档\\鼠标点击次数记录」，文件名 鼠标点击记录（配置文件，勿删）.json。
"""

import ctypes
import datetime
import json
import locale
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from ctypes import wintypes as wt
from tkinter import messagebox, ttk
from tkinter import font as tkfont

APP_TITLE = "鼠标点击次数记录"
APP_VERSION = "2.4.4"
BYLINE = "by 黎修源"
GITHUB_URL = "https://github.com/lixiuyuanan"
DISCLAIMER_TEXT = (
    "本软件为个人免费工具，与任何鼠标厂商均无关联。"
    "统计数据基于 Windows 系统消息按本地口径记录，可能因权限、驱动或使用环境产生偏差，"
    "仅供参考，不构成产品质量判定依据；请勿将统计结果用于与厂商的售后、索赔等争议场景。"
    "截至目前，作者未在本机环境发现本软件被反作弊系统检测，"
    "但不排除存在影响账号安全（含封号）的风险，请自行评估使用场景。"
    "因使用本软件导致的数据丢失或其他后果，作者不承担责任。")
DISCLAIMER_AGREE = "继续使用本软件，即表示您已阅读并认可上述声明。"
HELP_TEXT = (
    "· 启动：双击 exe，首次会弹一次 UAC（同意才能统计游戏内点击）和免责声明；"
    "确认后右下角出现托盘图标，开始记录。\n"
    "· 托盘图标：左键单击=打开/唤回窗口；右键=控制面板 / 切换鼠标档案 / 退出程序；"
    "悬停显示今日与总计。点窗口右上角叉号只是隐藏到后台，继续记录。\n"
    "· 首页：今日/近7天日均/总点击 + 按键明细（含寿命列）+ 近28天热力日历（小圆点颜色表示用量，"
    "绿→黄→橙→红，接近 8000 次变红；「空」=当天无记录，非本月的补位日期自动淡化）。\n"
    "· 设置：按键显隐与重命名、开机自启动、界面模式（跟随系统/浅色/深色）、备份与恢复、数据文件位置。\n"
    "· 鼠标档案：换鼠标时新建一条并填写各键额定寿命（万次），寿命用量按档案独立统计；"
    "托盘右键可快速切换。\n"
    "· 数据保存在「文档\\鼠标点击次数记录」文件夹内，删除或更换程序文件夹不会丢记录；"
    "每天自动备份，保留最近 7 份。\n"
    "· 卸载：设置里取消开机自启动 → 托盘右键退出程序 → 删除程序文件夹即可。")
FROZEN = getattr(sys, "frozen", False)  # 是否由 PyInstaller 打包成 exe 运行
EXE_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))
DATA_NAME = "鼠标点击记录（配置文件，勿删）.json"
DATA_VERSION = 3
LEGACY_DATA_NAMES = ("点击数据记录（勿删！！！）.json", "data.json")  # 旧文件名，自动兼容迁移
CONFIG_DIR_NAME = "config"           # 程序自带辅助文件夹（图标/脚本）
LEGACY_CONFIG_NAMES = ("其他文件",)  # 旧版本用过的子文件夹名，只用于兼容读取旧数据
DATA_FOLDER_NAME = APP_TITLE         # 数据文件夹名（放在用户「文档」里，跟程序目录分开）


def _dir_writable(path):
    probe = os.path.join(path, ".write_probe.tmp")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("x")
        os.remove(probe)
        return True
    except Exception:
        return False


def _documents_dir():
    """取「文档」文件夹的真实路径（可能被 OneDrive 重定向），拿不到返回 None。"""
    try:
        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                        ("Data4", ctypes.c_byte * 8)]
        # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
        docs_guid = GUID(0xFDD39AD0, 0x238F, 0x46AF,
                         (ctypes.c_byte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wt.DWORD,
                                                 ctypes.c_void_p,
                                                 ctypes.POINTER(ctypes.c_void_p)]
        # 出参按 c_void_p 取原始指针：用 c_wchar_p 接的话，CoTaskMemFree 拿到的
        # 会是 ctypes 自己的缓冲区，等于把别人的内存还给错误的堆。
        ole32 = ctypes.windll.ole32
        ole32.CoTaskMemFree.restype = None
        ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        buf = ctypes.c_void_p()
        if shell32.SHGetKnownFolderPath(ctypes.byref(docs_guid), 0, None,
                                        ctypes.byref(buf)) == 0 and buf.value:
            try:
                return ctypes.wstring_at(buf.value)
            finally:
                try:
                    ole32.CoTaskMemFree(buf)
                except Exception:
                    pass
    except Exception:
        pass
    return None


def _data_search_dirs():
    """按优先级列出数据目录：用户文档 → 用户目录 → 程序目录 → 旧版本位置。"""
    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA") or home
    docs = _documents_dir()
    dirs = []
    if docs:
        dirs.append(os.path.join(docs, DATA_FOLDER_NAME))
    dirs += [os.path.join(home, DATA_FOLDER_NAME),
             os.path.join(EXE_DIR, CONFIG_DIR_NAME), EXE_DIR]
    dirs += [os.path.join(EXE_DIR, name) for name in LEGACY_CONFIG_NAMES]
    dirs += [os.path.join(local, APP_TITLE, CONFIG_DIR_NAME), os.path.join(local, APP_TITLE)]
    seen, out = set(), []
    for folder in dirs:
        key = os.path.normcase(os.path.abspath(folder))
        if key not in seen:
            seen.add(key)
            out.append(folder)
    return out


def _resolve_data_dir(base):
    """数据/日志放「文档\\鼠标点击次数记录」，换电脑、更新/重装程序都不会丢；
    文档不可写时依次退到 用户目录 → exe 同级 config → exe 同级 → LOCALAPPDATA。"""
    for folder in _data_search_dirs():
        try:
            os.makedirs(folder, exist_ok=True)
            if _dir_writable(folder):
                return folder
        except Exception:
            continue
    return base


APP_DIR = _resolve_data_dir(EXE_DIR)  # 数据/日志目录（默认在用户「文档」下）
DATA_FILE = os.path.join(APP_DIR, DATA_NAME)
LOG_FILE = os.path.join(APP_DIR, "error.log")
TRAY_LOG = os.path.join(APP_DIR, "tray.log")  # 托盘事件诊断日志（可删）

DPI_SCALE = 1.0   # 由 enable_dpi_awareness() 写入；96dpi / 100% 缩放 = 1.0
LIFE_REFRESH_SECONDS = 30.0  # 寿命百分比独立刷新周期，点击计数仍按轮询实时更新


def enable_dpi_awareness():
    """声明进程 DPI 感知：125%/150% 缩放下界面不再被系统位图拉伸，字更清晰。
    必须在创建任何窗口（tk.Tk()）之前调用，返回当前缩放系数。"""
    global DPI_SCALE
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = wt.BOOL
        if not user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            raise OSError("SetProcessDpiAwarenessContext 失败")
    except Exception:
        try:
            ctypes.WinDLL("shcore", use_last_error=True).SetProcessDpiAwareness(2)  # Win8.1+
        except Exception:
            try:
                user32.SetProcessDPIAware()   # 更老的系统
            except Exception:
                pass
    dpi = 96
    try:
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        user32.GetDC.restype = ctypes.c_void_p
        user32.GetDC.argtypes = [ctypes.c_void_p]
        user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gdi32.GetDeviceCaps.restype = ctypes.c_int
        gdi32.GetDeviceCaps.argtypes = [ctypes.c_void_p, ctypes.c_int]
        dc = user32.GetDC(None)
        if dc:
            dpi = gdi32.GetDeviceCaps(dc, 88) or 96   # LOGPIXELSX
            user32.ReleaseDC(None, dc)
    except Exception:
        pass
    DPI_SCALE = max(1.0, float(dpi) / 96.0)
    return DPI_SCALE

MUTEX_NAME = "MouseClickCounter_SingleInstance_v1"
SHOW_EVENT_NAME = "MouseClickCounter_ShowWindow_v1"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "鼠标点击次数记录"          # 任务管理器「启动应用」里显示的就是这个名字
RUN_VALUE_LEGACY = "MouseClickCounter"  # 旧版本用过，设置/取消时一并清理
TASK_NAME = "鼠标点击次数记录"           # 计划任务名（最高权限开机自启）
CREATE_NO_WINDOW = 0x08000000

# 按键定义：点击 5 键计入总数；滚轮（上/下合并计一格）单独记录，不计入点击总数
CLICK_BUTTONS = (
    ("left", "左键"),
    ("right", "右键"),
    ("middle", "中键"),
    ("x1", "侧键1"),
    ("x2", "侧键2"),
)
WHEEL_BUTTONS = (
    ("wheel", "滚轮"),
)
BUTTONS = CLICK_BUTTONS + WHEEL_BUTTONS
KEYS = [k for k, _ in BUTTONS]
CLICK_KEYS = [k for k, _ in CLICK_BUTTONS]
WHEEL_KEYS = [k for k, _ in WHEEL_BUTTONS]
FIXED_KEYS = ("left", "right", "middle")   # 不允许隐藏的按键
HIDEABLE_KEYS = ("x1", "x2")               # 只有侧键可隐藏

# 默认额定寿命（次）：微动官方标称多为 2000 万次档，用户可在档案里改
DEFAULT_LIFE = {
    "left": 20000000, "right": 20000000, "middle": 10000000,
    "x1": 5000000, "x2": 5000000, "wheel": 10000000,
}

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_RBUTTONDBLCLK = 0x0206
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONDBLCLK = 0x0209
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONDBLCLK = 0x020D
ERROR_ALREADY_EXISTS = 183
WH_MOUSE_LL = 14
HC_ACTION = 0
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_HOOK_REINSTALL = WM_APP + 2  # 界面线程 → 钩子线程：重装钩子
WM_TRAY_REBUILD = WM_APP + 3    # 界面线程 → 钩子线程：托盘窗口没了，重建一个
WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_CONTEXTMENU = 0x007B
NIN_SELECT = 0x0400
NIN_KEYSELECT = 0x0401
TRAY_LEFT_CODES = frozenset(
    (WM_LBUTTONUP, WM_LBUTTONDOWN, WM_LBUTTONDBLCLK, NIN_SELECT, NIN_KEYSELECT))
TRAY_RIGHT_CODES = frozenset((WM_RBUTTONUP, WM_RBUTTONDOWN, WM_CONTEXTMENU))

LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t

SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x1, 0x4, 0x10
VK_LBUTTON = 0x01

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 1, 2, 4, 0x10
NIIF_INFO = 0x00000001
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
IDI_APPLICATION = 32512
SM_CXSMICON, SM_CYSMICON = 49, 50
MF_STRING, MF_CHECKED, MF_POPUP, MF_SEPARATOR = 0x0, 0x8, 0x10, 0x800
TPM_RETURNCMD, TPM_RIGHTBUTTON, TPM_BOTTOMALIGN = 0x0100, 0x0002, 0x0020
IDM_SHOW, IDM_STARTUP, IDM_EXIT = 1001, 1002, 1003
IDM_PROFILE_BASE = 2000
TRAY_MENU_GAP = 8
TRAY_MENU_STATE = {"profiles": (), "profile_id": None}
TRAY_MENU_LOCK = threading.Lock()
TRAY_WINDOW_CLASS = "MouseClickCounterTrayWindow"
TRAY_TIP = "鼠标点击次数记录 —— 单击显示窗口，右键更多"


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", ctypes.c_void_p),
        ("uID", wt.UINT),
        ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT),
        ("hIcon", ctypes.c_void_p),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", ctypes.c_wchar * 256),
        ("uVersion", wt.UINT),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", wt.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", ctypes.c_void_p),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
    ]


TRAY = {"hwnd": None, "ready": False, "sink": None}
HOOK_STATE = {"installed": False, "last_event": 0.0, "thread_id": 0, "blocked_logged": False}
SINGLETON = {"mutex": None, "kernel32": None}
AUTOSTART = {"mode": ""}  # "task"=最高权限计划任务 / "run"=注册表 / ""=未开启


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wt.POINT),
        ("mouseData", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


def _rotate(path, limit, keep):
    """日志超限时保留尾部若干 KB 重写，而不是整个删掉，保住最近的诊断信息。"""
    try:
        if os.path.getsize(path) <= limit:
            return
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - keep))
            tail = f.read()
        nl = tail.find(b"\n")
        if nl >= 0:
            tail = tail[nl + 1:]
        with open(path, "wb") as f:
            f.write("[日志已轮转，保留最近记录]\n".encode("utf-8"))
            f.write(tail)
    except Exception:
        pass


def log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
        _rotate(LOG_FILE, 200000, 51200)
    except Exception:
        pass


def tray_log(msg):
    """托盘相关事件记到 tray.log，出问题时可以直接看这个文件。"""
    try:
        with open(TRAY_LOG, "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
        _rotate(TRAY_LOG, 65536, 16384)
    except Exception:
        pass


def today():
    return datetime.date.today()


def fmt(n):
    """KPI / 状态栏 / 托盘提示的缩写显示：
    ≥1亿 → 1.23 亿；≥1000万 → 3456.7 万（数字不加千分位）；其余带千分位。"""
    n = int(n)
    if n >= 100000000:
        return "%.2f 亿" % (n / 1e8)
    if n >= 10000000:
        return "%.1f 万" % (n / 1e4)
    return "{:,}".format(n)


def fmt_full(n):
    return "{:,}".format(int(n))


HEAT_RED_CLICKS = 8000   # 热力日历：单日点击达到该值 → 红色


def heat_x(n):
    """热力映射值：x = sqrt(n/8000)×100（0~100）。"""
    return min(100.0, max(0.0, (n / float(HEAT_RED_CLICKS)) ** 0.5 * 100.0))


def desaturate(c, f):
    """颜色向自身灰度靠拢比例 f（0~1），用于热力配色整体降饱和。"""
    try:
        a = tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
        lum = int(round(sum(a) / 3.0))
        return "#%02x%02x%02x" % tuple(
            int(round(a[j] * (1 - f) + lum * f)) for j in range(3))
    except Exception:
        return c


def heat_color(n, empty="#eef1f5"):
    """单日点击数 → 热力颜色：x 走 life_color 同一套渐变，再整体降 20% 饱和度。
    sqrt 拉伸让低值日也有层次；n<=0 返回空色（不走渐变，避免与低频混淆）。"""
    if n <= 0:
        return empty
    return desaturate(life_color(heat_x(n)), 0.2)


def mix_color(c1, c2, f):
    """c1 向 c2 靠拢比例 f（0~1），用于热力日历中非本月格子的淡化。"""
    try:
        a = tuple(int(c1[i:i + 2], 16) for i in (1, 3, 5))
        b = tuple(int(c2[i:i + 2], 16) for i in (1, 3, 5))
        return "#%02x%02x%02x" % tuple(
            int(round(a[j] * (1 - f) + b[j] * f)) for j in range(3))
    except Exception:
        return c1


def system_dark_mode():
    """读 Windows 注册表判断系统应用模式是否为深色；拿不到按浅色处理。"""
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return not bool(value)
    except Exception:
        return False


def life_color(pct):
    """寿命百分比 → 渐变颜色：0-40% 绿，40-70% 绿渐黄，70-90% 黄渐橙，
    90-100% 橙渐红，≥100% 红。相邻锚点间线性插值。"""
    stops = ((0.0, (34, 197, 94)), (40.0, (34, 197, 94)),
             (70.0, (234, 179, 8)), (90.0, (234, 88, 12)), (100.0, (220, 38, 38)))
    if pct <= stops[0][0]:
        rgb = stops[0][1]
    elif pct >= stops[-1][0]:
        rgb = stops[-1][1]
    else:
        rgb = stops[0][1]
        for (a, ca), (b, cb) in zip(stops, stops[1:]):
            if a <= pct <= b:
                f = (pct - a) / (b - a)
                rgb = tuple(int(round(ca[j] + (cb[j] - ca[j]) * f)) for j in range(3))
                break
    return "#%02x%02x%02x" % rgb


def weighted_life_pct(items):
    """合计寿命百分比＝按用量加权：使用最多的键权重 0.6，第二、三名各 0.2
    （最常用的键最容易坏，加权比"最坏档"更贴近整体风险）。
    items 为 [(用量, 百分比)]；不足 3 项时权重按比例归一化；无有效项返回 None。"""
    items = [(u, p) for u, p in items if p is not None]
    if not items:
        return None
    items.sort(key=lambda x: x[0], reverse=True)
    weights = (0.6, 0.2, 0.2)[:len(items)]
    total_w = sum(weights)
    return sum(w * p for (u, p), w in zip(items, weights)) / total_w


WHEEL_NOTCH = 120   # Windows 标准滚轮一格 = 120 delta


def wheel_ticks(magnitude, residual):
    """滚轮按「格」计数：本次 delta + 上次攒下的不足一格量，满一格才进位。
    返回 (本次入账格数, 剩余不足一格的量)。
    高清/分段滚轮（WHEEL_DELTA 的因数，如 30、40）以前每条消息一律记 1 格，
    实测会系统性少计 —— 这是更新日志里挂了很久的已知限制，本版修正。"""
    total = int(magnitude) + int(residual)
    return total // WHEEL_NOTCH, total % WHEEL_NOTCH


def identify_message(msg_id, mouse_data):
    """把鼠标消息翻译成计数字段名（识别逻辑独立成函数，方便单测）。
    按下、双击、滚轮各计 1；松开与移动返回 None。"""
    if msg_id in (WM_LBUTTONDOWN, WM_LBUTTONDBLCLK):
        return "left"
    if msg_id in (WM_RBUTTONDOWN, WM_RBUTTONDBLCLK):
        return "right"
    if msg_id in (WM_MBUTTONDOWN, WM_MBUTTONDBLCLK):
        return "middle"
    if msg_id in (WM_XBUTTONDOWN, WM_XBUTTONDBLCLK):
        xbutton = (mouse_data >> 16) & 0xFFFF
        return "x1" if xbutton == 1 else ("x2" if xbutton == 2 else None)
    if msg_id == WM_MOUSEWHEEL:
        delta = ctypes.c_short((mouse_data >> 16) & 0xFFFF).value
        if delta:
            return "wheel"
    return None


# 钩子回调里只会下发这些消息给识别函数（含双击，避免 DBLCLK 漏计）
COUNT_MSG_IDS = frozenset((
    WM_LBUTTONDOWN, WM_LBUTTONDBLCLK,
    WM_RBUTTONDOWN, WM_RBUTTONDBLCLK,
    WM_MBUTTONDOWN, WM_MBUTTONDBLCLK,
    WM_XBUTTONDOWN, WM_XBUTTONDBLCLK,
    WM_MOUSEWHEEL,
))


class Stats:
    """按天保存的点击/滚轮数据 + 设置（按键显隐、鼠标档案、备份位置等），只在界面线程里读写。"""

    def __init__(self, path):
        self.path = path
        self.days = {}
        self.profile_days = {}
        self.total = {k: 0 for k in KEYS}
        self.settings = {}
        self._active_profile_id = None
        self.wheel_residual = 0
        self.dirty = False
        self._load()

    def _load(self):
        path = self._locate_existing()
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return
        except Exception as exc:
            log("数据文件读取失败，已备份为同名 .bak: %r" % (exc,))
            try:
                os.replace(path, path + ".bak")
            except Exception:
                pass
            return
        if os.path.normcase(os.path.abspath(path)) != os.path.normcase(os.path.abspath(self.path)):
            self._relocate(path)
        try:
            self.apply_payload(raw)
        except Exception as exc:
            # 结构被改坏的 JSON（profiles 不是列表、days 是字符串…）以前会直穿
            # main()：打包版 --noconsole 无声退出，用户只看到「软件打不开」。
            # 现在留一份 .bad 副本、按空数据启动，界面照常计数（旧数据仍可手工捞回）。
            log("数据内容无法解析，已另存为同名 .bad 并按空数据启动: %r" % (exc,))
            self._start_empty()
            try:
                os.replace(path, path + ".bad")     # 挪走，避免下次保存覆盖唯一副本
            except Exception:
                try:
                    shutil.copy2(path, path + ".bad")
                except Exception as exc2:
                    log("保留坏数据副本失败: %r" % (exc2,))

    def _start_empty(self):
        """重置成「全新数据」的内存状态：坏文件已被挪走，从这里重新开始。"""
        self.days = {}
        self.profile_days = {}
        self.total = {k: 0 for k in KEYS}
        self.settings = {}
        self._active_profile_id = None
        self.wheel_residual = 0
        self.dirty = False

    @staticmethod
    def _int_val(v):
        """外部 JSON 里的计数值 → int，坏值一律按 0。
        一个 "abc" 不该让整份数据读不出来（v2.4.1 接手当天实测崩溃点）。"""
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return int(v)
        if isinstance(v, str):
            try:
                return int(float(v.strip().replace(",", "")))
            except ValueError:
                return 0
        return 0

    def _normalize_days(self, days) -> dict:
        """Convert one profile's date map to the current row shape."""
        normalized = {}
        if not isinstance(days, dict):
            return normalized
        iv = self._int_val
        for day, counts in days.items():
            if not isinstance(counts, dict):
                continue
            row = {k: iv(counts.get(k, 0)) for k in KEYS}
            if "wheel" not in counts:
                row["wheel"] = iv(counts.get("wheel_up", 0)) + iv(counts.get("wheel_down", 0))
            normalized[str(day)] = row
        return normalized

    def select_profile(self, profile_id: str) -> dict:
        """Point day and total helpers at one profile's independent bucket."""
        if not isinstance(profile_id, str) or not profile_id:
            # 全新数据文件还没有任何档案：先把档案建出来。否则计数会写进
            # profile_days[None]，JSON 往返后变成 "null" 键，重读时再也找不回来
            # （界面表现＝「数据明明在、显示全是 0」）。
            profile_id = current_profile(self)["id"]
        if not isinstance(self.profile_days, dict):
            self.profile_days = {}
        days = self.profile_days.setdefault(profile_id, {})
        self._active_profile_id = profile_id
        self.days = days
        self.total = {k: 0 for k in KEYS}
        for row in days.values():
            for key in KEYS:
                self.total[key] += self._int_val(row.get(key, 0))
        return days

    def apply_payload(self, raw: dict) -> None:
        """Load v3 per-profile days, migrating the old shared bucket to the active profile."""
        if not isinstance(raw, dict):
            raise ValueError("数据文件根节点不是对象：%r" % (type(raw).__name__,))
        self.settings = (dict(raw.get("settings"))
                         if isinstance(raw.get("settings"), dict) else {})
        self.profile_days = {}
        legacy_days = self._normalize_days(raw.get("days") or {})
        self.days = legacy_days
        self.total = {k: 0 for k in KEYS}
        raw_profile_days = raw.get("profile_days")
        has_profile_days = isinstance(raw_profile_days, dict) and bool(raw_profile_days)
        if has_profile_days:
            for profile_id, days in raw_profile_days.items():
                if isinstance(profile_id, str) and isinstance(days, dict):
                    self.profile_days[profile_id] = self._normalize_days(days)
        profs = ensure_profiles(self)
        active_id = self.settings.get("profile_id")
        if not has_profile_days and legacy_days:
            self.profile_days[active_id] = legacy_days
        for prof in profs:
            self.profile_days.setdefault(prof["id"], {})
        self.select_profile(active_id)
        migrated = (bool(legacy_days and not has_profile_days)
                    or self._int_val(raw.get("version", 1)) < DATA_VERSION)
        if migrated:
            self.dirty = True

    def _locate_existing(self):
        """当前路径没有数据文件时，去旧位置找一份（换目录/旧版本升级都能接上）。"""
        if os.path.exists(self.path):
            return self.path
        for folder in _data_search_dirs():
            for name in (DATA_NAME,) + LEGACY_DATA_NAMES:
                candidate = os.path.join(folder, name)
                if os.path.exists(candidate):
                    log("在旧位置发现数据文件 %s，将迁移到 %s" % (candidate, self.path))
                    return candidate
        return self.path

    def _relocate(self, path):
        """把旧位置的数据文件搬到新位置，避免留下两份互相打架的数据。"""
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            try:
                os.replace(path, self.path)
            except OSError:
                shutil.copy2(path, self.path)   # 跨盘符时 os.replace 会失败
                os.remove(path)
            log("数据文件已迁移到 %s" % self.path)
        except Exception as exc:
            log("数据文件迁移失败（继续读旧位置，保存仍写新位置）: %r" % (exc,))

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        if self.settings.get(key) != value:
            self.settings[key] = value
            self.dirty = True

    def add(self, key, date=None):
        if self._active_profile_id is None:
            self.select_profile(self.settings.get("profile_id"))
        day = (date or today()).isoformat()
        row = self.days.get(day)
        if row is None:
            row = self.days[day] = {k: 0 for k in KEYS}
        row[key] = row.get(key, 0) + 1
        self.total[key] = self.total.get(key, 0) + 1
        self.dirty = True

    def add_wheel_delta(self, magnitude):
        """滚轮按格入账：不足一格的 delta 先攒着，凑满才 +1（见 wheel_ticks）。
        残量只在内存里，重启最多丢半格，不值得为它写盘。"""
        n, self.wheel_residual = wheel_ticks(magnitude, self.wheel_residual)
        for _ in range(n):
            self.add("wheel")
        return n

    def payload(self):
        return {
            "app": APP_TITLE,
            "version": DATA_VERSION,
            "updated": datetime.datetime.now().isoformat(timespec="seconds"),
            "settings": self.settings,
            "profile_days": self.profile_days,
        }

    def save(self, force=False):
        if not self.dirty and not force:
            return
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.payload(), f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, self.path)
            self.dirty = False
            self.auto_backup()
        except Exception as exc:
            log("数据保存失败: %r" % (exc,))

    def backup_dir(self):
        """备份目录：默认 数据目录\\backups，可在设置里改。"""
        d = self.settings.get("backup_dir") or os.path.join(APP_DIR, "backups")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            d = os.path.join(APP_DIR, "backups")
            try:
                os.makedirs(d, exist_ok=True)
            except Exception:
                pass
        return d

    def auto_backup(self):
        """每天首次保存后，往备份目录写一份 auto_YYYY-MM-DD.json，保留最近 7 份。"""
        stamp = today().isoformat()
        if self.settings.get("last_auto_backup") == stamp:
            return
        self.set_setting("last_auto_backup", stamp)
        try:
            d = self.backup_dir()
            dest = os.path.join(d, "auto_%s.json" % stamp)
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(self.payload(), f, ensure_ascii=False, separators=(",", ":"))
            olds = sorted(x for x in os.listdir(d)
                          if x.startswith("auto_") and x.endswith(".json"))[:-7]
            for name in olds:
                try:
                    os.remove(os.path.join(d, name))
                except OSError:
                    pass
            # 写设置会再次置脏，属正常：下次 3 秒保存周期落盘即可
        except Exception as exc:
            log("自动备份失败: %r" % (exc,))

    def manual_backup(self):
        """手动备份，返回文件路径（失败抛异常由界面提示）。"""
        d = self.backup_dir()
        base = "手动备份_%s" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        name = base + ".json"
        n = 1
        # 文件名只精确到秒：同一秒内的第二次备份必须换名，
        # 否则「恢复前自动备份」会把用户刚手动备份的那份原地覆盖掉。
        while os.path.exists(os.path.join(d, name)):
            name = "%s_%d.json" % (base, n)
            n += 1
        dest = os.path.join(d, name)
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(self.payload(), f, ensure_ascii=False, separators=(",", ":"))
        return dest

    def list_backups(self):
        d = self.backup_dir()
        try:
            names = [x for x in os.listdir(d) if x.endswith(".json")]
        except OSError:
            names = []

        def _mtime(n):
            try:
                return os.path.getmtime(os.path.join(d, n))
            except OSError:
                return 0.0

        # 按修改时间倒序，不是按文件名：中文名「手动备份_…」排在 "auto_…" 前面，
        # 以前「从备份恢复」的默认项永远是最旧的手动备份而不是最新那份。
        names.sort(key=_mtime, reverse=True)
        return d, names

    def restore_backup(self, name):
        """从备份目录恢复一份文件。恢复前先把当前数据手动备份，出错可再回来。"""
        src = os.path.join(self.backup_dir(), name)
        with open(src, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict) or not (
                isinstance(raw.get("profile_days"), dict)
                or isinstance(raw.get("days"), dict)):
            raise ValueError("备份文件格式不正确")
        try:
            self.manual_backup()
        except Exception as exc:
            log("恢复前自动备份失败（继续恢复）: %r" % (exc,))
        self.settings = {}
        self.apply_payload(raw)
        self.dirty = True
        self.save(force=True)
        return sum(self.total[k] for k in CLICK_KEYS)

    def day_row(self, date):
        """返回补齐 6 键的一行；老文件里的缺键/坏键在这里统一兜住，
        调用方（表格、KPI、热力）才可以放心地按固定键直接取。"""
        row = self.days.get(date.isoformat())
        if not isinstance(row, dict) or not row:
            return {k: 0 for k in KEYS}
        return {k: self._int_val(row.get(k, 0)) for k in KEYS}

    def sum_range_for(self, profile_id: str, start: datetime.date, end: datetime.date) -> dict:
        """Sum one profile's independent day bucket over a date range."""
        days = self.profile_days.get(profile_id) or {}
        res = {k: 0 for k in KEYS}
        day = start
        while day <= end:
            row = days.get(day.isoformat())
            if row:
                for k in KEYS:
                    res[k] += self._int_val(row.get(k, 0))
            day += datetime.timedelta(days=1)
        return res

    def sum_range(self, start, end):
        return self.sum_range_for(self._active_profile_id, start, end)

    def click_sum(self, row):
        """点击口径合计：只算 5 个点击键，滚轮不计入。"""
        if not isinstance(row, dict):
            return 0
        return sum(self._int_val(row.get(k, 0)) for k in CLICK_KEYS)

    def recorded_days(self):
        return sum(1 for row in self.days.values() if self.click_sum(row) > 0)


# ---------------------------------------------------------------------------
# 鼠标档案（存 settings，随备份走；profile_days 为每个档案独立保存每日计数）

def _valid_profile(p):
    return (isinstance(p, dict) and isinstance(p.get("id"), str)
            and isinstance(p.get("name"), str) and p.get("name"))


def ensure_profiles(stats):
    """保证 settings.profiles 存在且至少一条；没有就建「默认鼠标」，
    since 取最早有记录的那天，保证升级后寿命用量不从零开始。
    返回的列表必须是 settings["profiles"] 同一个对象——
    调用方（new_profile 等）直接 append 后 save 才生效（曾因过滤副本未写回丢档案）。"""
    profs = stats.settings.get("profiles")
    if isinstance(profs, list):
        cleaned = [p for p in profs if _valid_profile(p)]
        if len(cleaned) != len(profs):
            stats.dirty = True
        profs = stats.settings["profiles"] = cleaned
    else:
        profs = []
    if not profs:
        dates = sorted({day for days in getattr(stats, "profile_days", {}).values()
                        for day in days})
        if not dates:
            dates = sorted(stats.days.keys())
        since = dates[0] if dates else today().isoformat()
        profs = [{
            "id": "p1",
            "name": "默认鼠标",
            "switch": "",
            "added": today().isoformat(),
            "since": since,
            "life": dict(DEFAULT_LIFE),
            "labels": {},
            "hidden": [k for k in HIDEABLE_KEYS if k in (stats.settings.get("hidden") or [])],
        }]
        stats.settings["profiles"] = profs
        stats.settings.pop("hidden", None)   # 旧的全局显隐已并入档案
        stats.dirty = True
    pid = stats.settings.get("profile_id")
    if not any(p["id"] == pid for p in profs):
        stats.settings["profile_id"] = profs[0]["id"]
    if (hasattr(stats, "select_profile")
            and getattr(stats, "_active_profile_id", None) != stats.settings["profile_id"]):
        stats.select_profile(stats.settings["profile_id"])
    return profs


def current_profile(stats):
    profs = ensure_profiles(stats)
    pid = stats.settings.get("profile_id")
    for p in profs:
        if p["id"] == pid:
            return p
    return profs[0]


def profile_label(prof, key):
    """按键显示名：档案里的自定义名 > 默认名。"""
    return (prof.get("labels") or {}).get(key) or dict(BUTTONS)[key]


def profile_hidden(prof, key):
    if key not in HIDEABLE_KEYS:      # 左右中固定显示
        return False
    return key in (prof.get("hidden") or [])


def visible_click_keys(prof):
    return [k for k in CLICK_KEYS if not profile_hidden(prof, k)]


def visible_keys(prof):
    return visible_click_keys(prof) + list(WHEEL_KEYS)


def update_tray_menu_state(stats: "Stats") -> None:
    """Publish a thread-safe profile snapshot for the tray menu thread."""
    profs = ensure_profiles(stats)
    profiles = tuple((p["id"], p["name"]) for p in profs)
    active_id = stats.settings.get("profile_id")
    with TRAY_MENU_LOCK:
        TRAY_MENU_STATE["profiles"] = profiles
        TRAY_MENU_STATE["profile_id"] = active_id


def life_percent(used, limit):
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return None
    if limit <= 0:
        return None
    return used / limit * 100.0


_USER32 = None
_SHELL32 = None
_TRAY_WNDPROC = None


def user32_api():
    global _USER32
    if _USER32 is not None:
        return _USER32
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.LoadImageW.restype = ctypes.c_void_p
    u.PostQuitMessage.argtypes = [ctypes.c_int]
    u.LoadIconW.restype = ctypes.c_void_p
    u.LoadIconW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    u.GetSystemMetrics.restype = ctypes.c_int
    u.GetSystemMetrics.argtypes = [ctypes.c_int]
    u.RegisterClassW.restype = wt.WORD
    u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    u.CreateWindowExW.restype = ctypes.c_void_p
    u.CreateWindowExW.argtypes = [
        wt.DWORD, ctypes.c_wchar_p, ctypes.c_wchar_p, wt.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    u.DefWindowProcW.restype = LRESULT
    u.DefWindowProcW.argtypes = [ctypes.c_void_p, wt.UINT, WPARAM, LPARAM]
    u.CreatePopupMenu.restype = ctypes.c_void_p
    u.AppendMenuW.restype = wt.BOOL
    u.AppendMenuW.argtypes = [ctypes.c_void_p, wt.UINT, ctypes.c_size_t, ctypes.c_wchar_p]
    u.TrackPopupMenu.restype = wt.UINT
    u.TrackPopupMenu.argtypes = [
        ctypes.c_void_p, wt.UINT, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
    u.DestroyMenu.argtypes = [ctypes.c_void_p]
    u.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
    u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    u.PostMessageW.argtypes = [ctypes.c_void_p, wt.UINT, WPARAM, LPARAM]
    u.IsWindow.restype = wt.BOOL
    u.IsWindow.argtypes = [ctypes.c_void_p]
    u.DestroyWindow.restype = wt.BOOL
    u.DestroyWindow.argtypes = [ctypes.c_void_p]
    u.RegisterWindowMessageW.restype = wt.UINT
    u.RegisterWindowMessageW.argtypes = [ctypes.c_wchar_p]
    u.UnhookWindowsHookEx.restype = wt.BOOL
    u.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    u.PostThreadMessageW.restype = wt.BOOL
    u.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, WPARAM, LPARAM]
    u.GetLastInputInfo.restype = wt.BOOL
    u.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
    _USER32 = u
    return u


_CAPTION_API = None


def _caption_api():
    """标题栏改造用到的 user32 / dwmapi 函数，签名只声明一次。
    踩坑 15：ctypes 不写 argtypes/restype 时 64 位句柄按 C int 传递，
    轻则截断、重则 OverflowError，而且往往只在打包版才复现。"""
    global _CAPTION_API
    if _CAPTION_API is not None:
        return _CAPTION_API
    u = ctypes.windll.user32
    d = ctypes.windll.dwmapi
    u.GetAncestor.restype = ctypes.c_void_p
    u.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    u.GetWindowLongW.restype = ctypes.c_int
    u.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    u.SetWindowLongW.restype = ctypes.c_int
    u.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    u.SetWindowPos.restype = wt.BOOL
    u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
    # 拖拽线程用到的三个：见 App._drag_loop
    u.GetCursorPos.restype = wt.BOOL
    u.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
    u.GetAsyncKeyState.restype = ctypes.c_short
    u.GetAsyncKeyState.argtypes = [ctypes.c_int]
    u.IsWindow.restype = wt.BOOL
    u.IsWindow.argtypes = [ctypes.c_void_p]
    u.GetDoubleClickTime.restype = wt.UINT
    u.GetDoubleClickTime.argtypes = []
    d.DwmSetWindowAttribute.restype = ctypes.c_long
    d.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, wt.UINT, ctypes.c_void_p,
                                        wt.UINT]
    _CAPTION_API = (u, d)
    return _CAPTION_API


def kernel32_api():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetTickCount.restype = wt.DWORD
    k.GetCurrentThreadId.restype = wt.DWORD
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    return k


def system_idle_seconds():
    """系统层面距上一次鼠标/键盘输入的秒数（含被其他进程吃掉、钩子收不到的情况）。"""
    try:
        u = user32_api()
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not u.GetLastInputInfo(ctypes.byref(info)):
            return None
        tick = kernel32_api().GetTickCount() & 0xFFFFFFFF
        return ((tick - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return None


def is_elevated():
    try:
        return bool(ctypes.WinDLL("shell32").IsUserAnAdmin())
    except Exception:
        return False


TASKBAR_CREATED = {"id": 0}


def is_tray_window_alive():
    hwnd = TRAY.get("hwnd")
    return bool(hwnd) and bool(user32_api().IsWindow(ctypes.c_void_p(hwnd)))


def shell32_api():
    global _SHELL32
    if _SHELL32 is None:
        s = ctypes.WinDLL("shell32", use_last_error=True)
        s.Shell_NotifyIconW.restype = wt.BOOL
        s.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
        _SHELL32 = s
    return _SHELL32


def find_icon_file():
    """图标优先取打包内嵌资源（--add-data），其次 数据目录\\icon.ico 与 config\\icon.ico，
    再次 exe 同级，最后扫一级子目录。"""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        candidate = os.path.join(bundled, "icon.ico")
        if os.path.exists(candidate):
            return candidate
    for folder in (APP_DIR, os.path.join(EXE_DIR, CONFIG_DIR_NAME), EXE_DIR):
        candidate = os.path.join(folder, "icon.ico")
        if os.path.exists(candidate):
            return candidate
    try:
        for entry in sorted(os.listdir(EXE_DIR)):
            sub = os.path.join(EXE_DIR, entry)
            if os.path.isdir(sub):
                candidate = os.path.join(sub, "icon.ico")
                if os.path.exists(candidate):
                    return candidate
    except OSError:
        pass
    return None


def load_tray_icon(u):
    cx = u.GetSystemMetrics(SM_CXSMICON) or 16
    cy = u.GetSystemMetrics(SM_CYSMICON) or 16
    # Declare signatures: a 64-bit module handle passed as a C int would raise
    # OverflowError in the frozen fallback branch (crash loop seen 2026-10-13).
    u.LoadImageW.restype = ctypes.c_void_p
    u.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_uint, ctypes.c_int, ctypes.c_int,
                             ctypes.c_uint]
    path = find_icon_file()
    icon = 0
    if path:
        icon = u.LoadImageW(None, ctypes.c_wchar_p(path), IMAGE_ICON, cx, cy, LR_LOADFROMFILE)
    if not icon and FROZEN:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        icon = u.LoadImageW(kernel32.GetModuleHandleW(None), ctypes.c_void_p(1),
                            IMAGE_ICON, cx, cy, 0)
    if not icon:
        icon = u.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))
    return icon


def _menu_text(value: object) -> str:
    """Escape ampersands so profile names are shown literally in Win32 menus."""
    return str(value).replace("&", "&&")


def show_tray_menu(u: ctypes.WinDLL, hwnd: int, sink: queue.Queue) -> None:
    with TRAY_MENU_LOCK:
        profiles = list(TRAY_MENU_STATE["profiles"])
        active_id = TRAY_MENU_STATE["profile_id"]
    menu = u.CreatePopupMenu()
    submenu = u.CreatePopupMenu()
    profile_commands = {}
    for index, (profile_id, name) in enumerate(profiles):
        command = IDM_PROFILE_BASE + index
        flags = MF_STRING | (MF_CHECKED if profile_id == active_id else 0)
        u.AppendMenuW(submenu, flags, command, _menu_text(name))
        profile_commands[command] = profile_id
    u.AppendMenuW(menu, MF_STRING, IDM_SHOW, "控制面板")
    u.AppendMenuW(menu, MF_SEPARATOR, 0, None)
    u.AppendMenuW(menu, MF_POPUP | MF_STRING, submenu, "切换鼠标档案")
    u.AppendMenuW(menu, MF_SEPARATOR, 0, None)
    u.AppendMenuW(menu, MF_STRING, IDM_EXIT, "退出程序")
    point = wt.POINT()
    u.GetCursorPos(ctypes.byref(point))
    u.SetForegroundWindow(hwnd)
    # Anchor the menu bottom-left slightly above-right of the cursor.
    cmd = u.TrackPopupMenu(
        menu, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_BOTTOMALIGN,
        point.x + TRAY_MENU_GAP, point.y - TRAY_MENU_GAP, 0, hwnd, None)
    u.PostMessageW(hwnd, WM_NULL, 0, 0)
    u.DestroyMenu(menu)
    tray_log("菜单选择 cmd=%s" % cmd)
    if cmd == IDM_SHOW:
        sink.put(("show", None))
    elif cmd == IDM_STARTUP:
        sink.put(("toggle-startup", None))
    elif cmd == IDM_EXIT:
        sink.put(("quit", None))
    elif cmd in profile_commands:
        sink.put(("switch-profile", profile_commands[cmd]))


def _tray_wnd_proc(hwnd, msg, wparam, lparam):
    """托盘窗口的窗口过程。整进程只注册一次，sink 在调用时才从 TRAY 取，
    所以看门狗重建窗口不必再注册新类，也不会像旧写法那样每重建一次就多
    留一个 WINFUNCTYPE 引用和一个窗口类。

    返回必须是原始 int：ctypes 把 LRESULT(c_ssize_t) 往回转时，None 会被当成
    -1（真指针）并抛 ctypes.ArgumentError，异常穿过原生栈直接终止钩子线程。"""
    u = user32_api()
    sink = TRAY.get("sink")
    try:
        if msg == WM_TRAY:
            # 三种格式都要兼容：
            #   V1/本机实测 : wParam=图标ID,      lParam=鼠标消息
            #   V2/V3       : wParam=鼠标消息,    lParam=(x,y)
            #   V4          : wParam=通知码+ID,   lParam=(x,y)
            codes = (wparam & 0xFFFF, lparam & 0xFFFF)
            if set(codes) & TRAY_LEFT_CODES:
                tray_log("托盘左键 wParam=0x%04X lParam=0x%04X" % codes)
                if sink:
                    sink.put(("show", None))
            elif set(codes) & TRAY_RIGHT_CODES:
                tray_log("托盘右键 wParam=0x%04X lParam=0x%04X" % codes)
                # 必须在拥有该窗口的线程里调用，否则 TrackPopupMenu 会直接返回、菜单不显示；
                # 菜单自带的消息循环照常分发，低级鼠标钩子仍会被回调
                if sink:
                    show_tray_menu(u, hwnd, sink)
            return 0
        if TASKBAR_CREATED["id"] and msg == TASKBAR_CREATED["id"]:
            # 资源管理器重启后托盘图标会消失，这里重新添加
            tray_log("收到 TaskbarCreated，重新添加托盘图标")
            TRAY["hwnd"] = hwnd
            add_tray_icon(u, hwnd)
            return 0
        if msg == WM_DESTROY:
            u.PostQuitMessage(0)
            return 0
        ret = u.DefWindowProcW(hwnd, msg, wparam, lparam)
        return int(ret) if ret is not None else 0
    except Exception as exc:
        # 任何异常都不能冒泡出去，否则整个消息线程（含鼠标钩子）会静默退出
        log("托盘消息处理异常: %r" % (exc,))
        return 0


def _register_tray_window_class(u):
    """注册托盘窗口类；已注册（错误 1407/1410）就直接复用第一次的那个 WNDPROC。"""
    global _TRAY_WNDPROC
    if _TRAY_WNDPROC is not None:
        return True
    proc = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_void_p, wt.UINT, WPARAM, LPARAM)(
        _tray_wnd_proc)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    hinst = kernel32.GetModuleHandleW(None)
    wc = WNDCLASSW()
    wc.lpfnWndProc = ctypes.cast(proc, ctypes.c_void_p)
    wc.hInstance = hinst
    wc.lpszClassName = TRAY_WINDOW_CLASS
    atom = u.RegisterClassW(ctypes.byref(wc))
    err = ctypes.get_last_error()
    if not atom and err not in (1407, 1410):     # 已存在 / 类名已注册：复用即可
        log("注册托盘窗口类失败: %s" % err)
        return False
    _TRAY_WNDPROC = proc        # 只在成功后留引用，防止回调对象被 GC
    return True


def create_tray_window(u, sink):
    """在消息线程里建一个隐藏窗口，用于接收托盘图标的鼠标消息。"""
    TRAY["sink"] = sink
    if not _register_tray_window_class(u):
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    hinst = kernel32.GetModuleHandleW(None)
    hwnd = u.CreateWindowExW(0, TRAY_WINDOW_CLASS, APP_TITLE, 0, 0, 0, 0, 0,
                             None, None, hinst, None)
    if not hwnd:
        log("创建托盘窗口失败: %s" % ctypes.get_last_error())
    return hwnd


def ensure_tray_window(u, sink):
    """托盘窗口没了就重建一个并补图标；还在就什么都不做（幂等）。"""
    if is_tray_window_alive():
        return TRAY["hwnd"]
    TRAY["hwnd"] = None
    TRAY["ready"] = False
    hwnd = create_tray_window(u, sink)
    if hwnd:
        add_tray_icon(u, hwnd)
    return hwnd


def add_tray_icon(u, hwnd):
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = hwnd
    nid.uID = 1
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    nid.uCallbackMessage = WM_TRAY
    nid.hIcon = load_tray_icon(u)
    nid.szTip = TRAY_TIP
    if not shell32_api().Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
        log("添加托盘图标失败")
        tray_log("NIM_ADD 失败")
        return False
    TRAY["hwnd"] = hwnd
    TRAY["ready"] = True
    tray_log("NIM_ADD 成功 hwnd=%s hicon=%s" % (hwnd, nid.hIcon))
    return True


def tray_modify(tip=None, info=None, title=None):
    """更新托盘提示/气泡（可从界面线程调用）。"""
    hwnd = TRAY.get("hwnd")
    if not hwnd:
        return
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = hwnd
    nid.uID = 1
    if tip:
        nid.uFlags |= NIF_TIP
        nid.szTip = tip[:120]
    if info:
        nid.uFlags |= NIF_INFO
        nid.szInfo = info[:250]
        nid.szInfoTitle = (title or APP_TITLE)[:60]
        nid.dwInfoFlags = NIIF_INFO
    shell32_api().Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))


def tray_remove():
    hwnd = TRAY.get("hwnd")
    if not hwnd:
        return
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = hwnd
    nid.uID = 1
    shell32_api().Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
    TRAY["hwnd"] = None
    TRAY["ready"] = False


WORKER = {"thread": None}
WORKER_LOCK = threading.Lock()


def start_worker(sink):
    """启动后台线程（鼠标钩子 + 托盘）。线程若意外退出，可由看门狗再次拉起。"""
    with WORKER_LOCK:
        thread = WORKER.get("thread")
        if thread is not None and thread.is_alive():
            return
        thread = threading.Thread(target=_worker, args=(sink,), name="mouse-hook", daemon=True)
        WORKER["thread"] = thread
        thread.start()
        tray_log("后台线程已启动")


def _worker(sink):
    try:
        _worker_main(sink)
    except Exception as exc:
        log("后台线程异常退出: %r" % (exc,))
        tray_log("后台线程异常退出: %r" % (exc,))


def _worker_main(sink):
    user32 = user32_api()
    hookproc_type = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, WPARAM, LPARAM)
    user32.SetWindowsHookExW.restype = ctypes.c_void_p
    user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int, hookproc_type, ctypes.c_void_p, ctypes.c_uint]
    user32.CallNextHookEx.restype = LRESULT
    user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, WPARAM, LPARAM]
    user32.GetMessageW.restype = ctypes.c_int
    user32.GetMessageW.argtypes = [
        ctypes.POINTER(wt.MSG), ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
    TASKBAR_CREATED["id"] = user32.RegisterWindowMessageW("TaskbarCreated")

    def callback(n_code, w_param, l_param):
        # 钩子回调必须极快返回：只识别按键并入队，不做任何耗时操作
        if n_code == HC_ACTION and w_param in COUNT_MSG_IDS:
            try:
                HOOK_STATE["last_event"] = time.monotonic()
                info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                key = identify_message(w_param, info.mouseData)
                if key == "wheel":
                    # 滚轮带幅度：把原始 delta 交给界面线程按格累积，
                    # 否则高清滚轮（30/40 一格）会被系统性少计。
                    delta = ctypes.c_short((info.mouseData >> 16) & 0xFFFF).value
                    sink.put(("wheel-notches", abs(delta)))
                elif key:
                    sink.put(key)
            except Exception as exc:
                log("钩子回调异常: %r" % (exc,))
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    hook_proc = hookproc_type(callback)  # 保留引用，防止被回收导致钩子失效
    handle = user32.SetWindowsHookExW(WH_MOUSE_LL, hook_proc, None, 0)
    HOOK_STATE["proc"] = hook_proc
    HOOK_STATE["handle"] = handle
    HOOK_STATE["installed"] = bool(handle)
    HOOK_STATE["thread_id"] = kernel32_api().GetCurrentThreadId()
    HOOK_STATE["last_event"] = time.monotonic()
    if handle:
        sink.put(("ready", None))
        tray_log("鼠标钩子安装成功")
    else:
        log("安装鼠标钩子失败, GetLastError=%s" % user32.GetLastError())
        tray_log("鼠标钩子安装失败")
        sink.put(("error", "安装鼠标钩子失败，无法记录点击"))

    ensure_tray_window(user32, sink)

    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        try:
            if msg.message == WM_HOOK_REINSTALL:
                reinstall_hook(user32)
                continue
            if msg.message == WM_TRAY_REBUILD:
                # 看门狗发现「线程还活着但托盘窗口没了」时的自救通道：
                # 窗口必须在本线程创建，消息循环才能收到托盘回调。
                ensure_tray_window(user32, sink)
                continue
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:
            log("窗口消息处理异常: %r" % (exc,))
    tray_log("消息循环结束（线程即将退出）")


def reinstall_hook(user32=None):
    """重装低级鼠标钩子。必须在钩子线程里执行（回调会落在调用线程）。"""
    u = user32 or user32_api()
    try:
        old = HOOK_STATE.get("handle")
        if old:
            u.UnhookWindowsHookEx(ctypes.c_void_p(old))
        proc = HOOK_STATE.get("proc")
        handle = u.SetWindowsHookExW(WH_MOUSE_LL, proc, None, 0) if proc else None
        HOOK_STATE["handle"] = handle
        HOOK_STATE["installed"] = bool(handle)
        HOOK_STATE["last_event"] = time.monotonic()
        tray_log("重新安装鼠标钩子：%s" % ("成功" if handle else "失败"))
        return bool(handle)
    except Exception as exc:
        log("重装钩子异常: %r" % (exc,))
        return False


def request_hook_reinstall():
    """从界面线程请求钩子线程重装钩子。"""
    tid = HOOK_STATE.get("thread_id")
    if not tid:
        return False
    try:
        user32_api().PostThreadMessageW(tid, WM_HOOK_REINSTALL, 0, 0)
        return True
    except Exception as exc:
        log("请求重装钩子失败: %r" % (exc,))
        return False


def request_tray_rebuild():
    """从界面线程请求钩子线程重建托盘窗口。

    必须有这条通道：钩子线程还在跑（鼠标照样计数），但它的窗口因为异常
    被销毁了。此时 start_worker 看到「线程活着」会直接 return，看门狗等于
    什么都不做，托盘图标再也回不来，只能重启程序。"""
    tid = HOOK_STATE.get("thread_id")
    if not tid:
        return False
    try:
        user32_api().PostThreadMessageW(tid, WM_TRAY_REBUILD, 0, 0)
        return True
    except Exception as exc:
        log("请求重建托盘窗口失败: %r" % (exc,))
        return False


def pythonw_path():
    if FROZEN:
        return sys.executable
    exe = sys.executable or ""
    if exe.lower().endswith("python.exe"):
        candidate = exe[: -len("python.exe")] + "pythonw.exe"
        if os.path.exists(candidate):
            return candidate
    return exe


def startup_command():
    """自启动命令。带 --startup：开机时静默启动（只驻留托盘，不弹窗口）。"""
    if FROZEN:
        return '"%s" --startup' % sys.executable
    return '"%s" "%s" --startup' % (pythonw_path(), os.path.join(EXE_DIR, "mouse_counter.py"))


def read_run_value():
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
            return value or None
    except FileNotFoundError:
        return None
    except Exception as exc:
        log("读取开机自启动状态失败: %r" % (exc,))
        return None


def write_run_value(value):
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, value)
        for name in (RUN_VALUE_LEGACY,):
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass


def delete_run_values():
    import winreg

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            for name in (RUN_VALUE, RUN_VALUE_LEGACY):
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
    except Exception as exc:
        log("删除开机自启动失败: %r" % (exc,))


def _run_hidden(args):
    """调用 schtasks 等命令行工具，不弹控制台窗口。

    schtasks 的输出是系统 ANSI 码页（中文机器上是 cp936），用 text=True 走
    区域编码再配 errors=replace 才不会炸：PYTHONUTF8/-X utf8 下 text=True 会按
    UTF-8 解码，读线程直接 UnicodeDecodeError，stdout 变 None，
    自愈逻辑于是「看起来没报错、其实什么都没读到」。
    """
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              encoding=locale.getpreferredencoding(False),
                              errors="replace", timeout=20,
                              creationflags=CREATE_NO_WINDOW)
    except Exception as exc:
        log("执行 %s 失败: %r" % (args[:2], exc))
        return None


def task_exists():
    r = _run_hidden(["schtasks", "/query", "/tn", TASK_NAME])
    return bool(r is not None and r.returncode == 0)


def task_command():
    """返回计划任务里配置的可执行文件与参数（读不出来返回 (None, None)）。"""
    r = _run_hidden(["schtasks", "/query", "/tn", TASK_NAME, "/xml", "ONE"])
    if r is None or r.returncode != 0:
        return None, None
    try:
        import xml.etree.ElementTree as ET

        xml_text = r.stdout
        root = ET.fromstring(xml_text)
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        exec_node = root.find(".//t:Actions/t:Exec", ns) or root.find(".//Actions/Exec")
        if exec_node is None:
            return None, None
        command = exec_node.findtext("t:Command", None, ns) or exec_node.findtext("Command")
        arguments = exec_node.findtext("t:Arguments", None, ns) or exec_node.findtext("Arguments")
        return command, arguments
    except Exception as exc:
        log("解析计划任务失败: %r" % (exc,))
        return None, None


TASK_XML_TEMPLATE = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>黎修源</Author>
    <Description>鼠标点击次数记录：登录时静默启动（最高权限），统计全部点击</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>--startup</Arguments>
      <WorkingDirectory>{workdir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def task_run_command():
    """计划任务里要执行的命令（exe 本身；脚本模式则用 pythonw + 脚本）。"""
    if FROZEN:
        return sys.executable, EXE_DIR
    return pythonw_path(), EXE_DIR


def task_user_id():
    domain = os.environ.get("USERDOMAIN") or os.environ.get("COMPUTERNAME") or ""
    user = os.environ.get("USERNAME") or ""
    return ("%s\\%s" % (domain, user)) if domain else user


def _xml_text(s):
    """XML 文本节点转义：路径里的 & 或 < 会让 schtasks 直接报「文档打开失败」，
    以前用户名/路径未转义过，装在 C:\\R&D\\ 这类目录下就会静默失去自启动。"""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def task_xml():
    command, workdir = task_run_command()
    if not FROZEN:
        # 脚本模式：Command 用 pythonw.exe，脚本路径放进 Arguments
        script = os.path.join(EXE_DIR, "mouse_counter.py")
        body = TASK_XML_TEMPLATE.replace("<Arguments>--startup</Arguments>",
                                         "<Arguments>\"%s\" --startup</Arguments>"
                                         % _xml_text(script))
    else:
        body = TASK_XML_TEMPLATE
    return body.format(user=_xml_text(task_user_id()), command=_xml_text(command),
                       workdir=_xml_text(workdir))


def create_logon_task():
    """创建「登录时以最高权限运行」的计划任务（需要管理员权限）。
    用计划任务的好处：开机静默启动、不弹 UAC，而且钩子处于高完整性级别，
    带内核反作弊的游戏内点击也能统计。

    注意：不要用 schtasks /tr 传带引号的命令行 —— schtasks 自己的引号解析会把
    带引号的 exe 路径 + 参数拆错（实测报"参数错误"）。这里改用 /xml 方式，
    参数写在 XML 的 Exec/Command + Arguments 里，最稳。"""
    tmp = os.path.join(APP_DIR, "task_tmp.xml")
    try:
        with open(tmp, "w", encoding="utf-16") as f:
            f.write(task_xml())
    except Exception as exc:
        log("写计划任务 XML 失败: %r" % (exc,))
        return False
    r = _run_hidden(["schtasks", "/create", "/tn", TASK_NAME, "/xml", tmp, "/f"])
    try:
        os.remove(tmp)
    except OSError:
        pass
    ok = bool(r is not None and r.returncode == 0)
    tray_log("创建最高权限计划任务：%s%s"
             % ("成功" if ok else "失败",
                "" if ok else " " + ((r.stderr or r.stdout or "").strip()[:200] if r else "")))
    return ok


def delete_logon_task():
    if not task_exists():
        return True
    r = _run_hidden(["schtasks", "/delete", "/tn", TASK_NAME, "/f"])
    ok = bool(r is not None and r.returncode == 0)
    tray_log("删除计划任务：%s" % ("成功" if ok else "失败"))
    return ok


def startup_enabled():
    return bool(read_run_value()) or task_exists()


def set_startup(enable, stats=None):
    """开关自启动，并把用户的选择记进数据文件（跟着备份走）。

    有管理员权限时用「计划任务 + 最高权限」——开机同样是静默启动，但不弹 UAC，
    而且钩子以高完整性级别运行，游戏内的点击也能统计；
    没有管理员权限时退回注册表 Run（能开机启动，但游戏内可能不计数）。
    """
    if enable:
        if is_elevated() and create_logon_task():
            delete_run_values()  # 避免计划任务和 Run 双启动
            AUTOSTART["mode"] = "task"
        else:
            write_run_value(startup_command())
            AUTOSTART["mode"] = "run"
            if not is_elevated():
                tray_log("普通权限：已写入注册表 Run；建议提权后再开自启动，游戏内计数需要管理员权限")
    else:
        delete_logon_task()
        delete_run_values()
        AUTOSTART["mode"] = ""
    if stats is not None:
        try:
            stats.set_setting("autostart", bool(enable))
            stats.set_setting("autostart_choice", True)   # 用户主动做过选择，之后不再默认开启
            stats.save()
        except Exception as exc:
            log("记录自启动偏好失败: %r" % (exc,))
    return startup_enabled()


def sync_autostart(stats):
    """每次启动时对齐自启动状态（换电脑/挪文件夹后自动恢复）：
      0) 计划任务存在 → 以它为准，路径变了就重建，并清掉重复的注册表项；
      1) 注册表里已有项但路径不是当前 exe → 改写成当前路径；
      2) 注册表和任务都没有、但数据文件记着用户开过 → 重新写入；
      3) 都没有 → 开机静默启动时默认帮用户打开（v2.0.0，避免安装者找不到入口），
         手动启动则不动（尊重"从未选择过"的状态）。"""
    target = startup_command()
    current = read_run_value()
    want = bool(stats.get_setting("autostart", False))
    try:
        if task_exists():
            cmd, _args = task_command()
            if cmd and os.path.normcase(cmd) != os.path.normcase(sys.executable):
                tray_log("计划任务路径已过期（%s），按当前位置重建" % cmd)
                if not create_logon_task():
                    tray_log("重建计划任务失败（可能没有管理员权限）")
            if current:
                delete_run_values()  # 计划任务优先，避免重复启动
            AUTOSTART["mode"] = "task"
            stats.set_setting("autostart", True)
            return True
        if current:
            # 有管理员权限时，顺手把老的「注册表 Run」升级成「最高权限计划任务」，
            # 这样开机就能带着管理员权限静默启动（游戏内也能统计）
            if is_elevated():
                if create_logon_task():
                    delete_run_values()
                    AUTOSTART["mode"] = "task"
                    tray_log("已把注册表自启动迁移为最高权限计划任务")
                    stats.set_setting("autostart", True)
                    return True
                tray_log("迁移为计划任务失败，继续使用注册表自启动")
            if current != target:
                write_run_value(target)
                tray_log("自启动路径已更新为当前位置: %s" % target)
            AUTOSTART["mode"] = "run"
            stats.set_setting("autostart", True)
        elif want:
            if is_elevated() and create_logon_task():
                tray_log("按上次设置重建了最高权限计划任务")
                AUTOSTART["mode"] = "task"
            else:
                write_run_value(target)
                tray_log("未找到自启动项，按上次设置重新写入: %s" % target)
                AUTOSTART["mode"] = "run"
            stats.set_setting("autostart", True)
        else:
            return False
    except Exception as exc:
        log("对齐自启动失败: %r" % (exc,))
        return False
    return startup_enabled()


def default_font():
    return "Microsoft YaHei UI"


class App:
    THEME_LIGHT = dict(BG="#f5f6f8", CARD="#ffffff", FG="#1f2329",
                       MUTED="#6b7280", ACCENT="#2563eb", DANGER="#b91c1c",
                       LINE="#e3e5e9", STRIPE="#fafbfc", BTN_HOVER="#eef1f5",
                       ROW_TOTAL="#eff5ff", ROW_TOTAL_HOVER="#e2ecfb", HEAT_EMPTY="#eef1f5",
                       DEL_FG="#d4a0a0", HEAT_TEXT="#111418",
                       WARN="#b45309", WARN_BG="#fef3c7", WARN_LINE="#fcd34d",
                       GREEN="#22c55e", YELLOW="#eab308",
                       UP="#3e8b5a", DOWN="#a33f3f",
                       CAP_FG="#1f2329", KPI_FRAME="#8b9fb8")
    THEME_DARK = dict(BG="#15171c", CARD="#1f232b", FG="#e8eaed",
                      MUTED="#9aa1ab", ACCENT="#6c9bff", DANGER="#f87171",
                      LINE="#343a44", STRIPE="#262b34", BTN_HOVER="#3a4048",
                      ROW_TOTAL="#26314a", ROW_TOTAL_HOVER="#31406a", HEAT_EMPTY="#2a2f38",
                      DEL_FG="#c07a7a", HEAT_TEXT="#111418",
                      WARN="#fbbf24", WARN_BG="#3b3013", WARN_LINE="#6b5310",
                      GREEN="#22c55e", YELLOW="#eab308",
                      UP="#6faf85", DOWN="#bd7f7f",
                      CAP_FG="#ffffff", KPI_FRAME="#5b6a80")
    FONT = default_font()
    PAGES = ("home", "settings", "profiles", "about")
    PAGE_NAMES = {"home": "首页", "settings": "设置", "profiles": "鼠标档案", "about": "关于"}

    def __init__(self, root, stats, sink, silent=False):
        self.root = root
        self.stats = stats
        self.sink = sink
        self.cells = {}
        self.row_widgets = {}
        self._row_bg = {}
        self._row_hl = {}
        self.kpi = {}
        self.kpi_short = {}
        self.kpi_full = {}
        self.last_save = 0.0
        self.last_tip = 0.0
        self.last_health = 0.0
        self.last_status = 0.0
        self.last_life_refresh = float("-inf")
        self.hook_warning = False
        self.blocked_strikes = 0
        self.hinted = False
        self.page = "home"
        self.pages = {}
        self.nav = {}
        self.theme_mode = stats.get_setting("theme") or "auto"   # auto/light/dark
        if self.theme_mode not in ("auto", "light", "dark"):
            self.theme_mode = "auto"
        self._apply_palette()
        self._build()
        self.refresh(force=True)
        self.root.after(400, self.poll)
        if not silent and not stats.get_setting("disclaimer_ok"):
            self.root.after(300, self.show_disclaimer)

    # ---------- 主题 ----------

    def _apply_palette(self):
        """Resolve auto→system and copy the palette onto instance attrs.
        All UI code reads self.BG / self.CARD ... so a rebuild repaints."""
        dark = (system_dark_mode() if self.theme_mode == "auto"
                else self.theme_mode == "dark")
        theme = self.THEME_DARK if dark else self.THEME_LIGHT
        self.dark = dark
        for k, v in theme.items():
            setattr(self, k, v)

    def _apply_style(self):
        """Style ttk widgets (scrollbar/combobox/checkbutton) from palette."""
        try:
            st = ttk.Style()
            st.theme_use("clam")
            st.configure(".", background=self.BG, foreground=self.FG,
                         fieldbackground=self.CARD, bordercolor=self.LINE,
                         font=(self.FONT, 9))
            st.configure("TFrame", background=self.BG)
            st.configure("TLabel", background=self.BG, foreground=self.FG)
            st.configure("TCheckbutton", background=self.CARD,
                         foreground=self.FG, focuscolor=self.CARD)
            st.map("TCheckbutton",
                   background=[("active", self.CARD)],
                   foreground=[("active", self.FG)])
            st.configure("TScrollbar", background=self.LINE,
                         troughcolor=self.BG, arrowcolor=self.MUTED,
                         bordercolor=self.BG, relief="flat")
            st.map("TScrollbar", background=[("active", self.MUTED)])
            st.configure("TCombobox", fieldbackground=self.CARD,
                         background=self.CARD, foreground=self.FG,
                         arrowcolor=self.FG, bordercolor=self.LINE)
            st.map("TCombobox",
                   fieldbackground=[("readonly", self.CARD)],
                   foreground=[("readonly", self.FG)],
                   selectbackground=[("readonly", self.CARD)],
                   selectforeground=[("readonly", self.FG)])
        except Exception:
            pass

    def _entry(self, parent, **kw):
        """Theme-aware tk.Entry wrapper."""
        kw.setdefault("font", (self.FONT, 9))
        kw.setdefault("relief", "solid")
        kw.setdefault("bd", 1)
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bg", self.CARD)
        kw.setdefault("fg", self.FG)
        kw.setdefault("insertbackground", self.FG)
        kw.setdefault("selectbackground", self.ACCENT)
        kw.setdefault("selectforeground", self.CARD)
        return tk.Entry(parent, **kw)

    def switch_theme(self, mode):
        """Change color mode and rebuild the whole window."""
        if mode == self.theme_mode:
            return
        keep_page = self.page
        self.theme_mode = mode
        self.stats.set_setting("theme", mode)
        self.stats.save(force=True)
        self._apply_palette()
        for child in self.root.winfo_children():
            child.destroy()
        self.cells = {}
        self.life_cells = {}
        self.life_dots = {}
        self.table_row_widgets = {}
        self._row_bg = {}
        self._row_hl = {}
        self.kpi = {}
        self.kpi_delta = {}
        self.kpi_full = {}
        self.kpi_short = {}
        self.pages = {}
        self.nav = {}
        self._fixed_h = 0
        self._placed = True
        self._apply_style()
        self._build()
        # _build() 末尾会 show_page("home")：用户是在设置页里改的主题，
        # 重建完必须回到原来那一页，否则每改一次主题都被踢回首页。
        if keep_page != "home":
            self.show_page(keep_page)
        self.refresh(force=True, life_now=True)

    def _strip_native_caption(self, _e=None):
        """Hide the OS title bar but keep taskbar presence, minimize box and Alt+F4
        by clearing WS_CAPTION/WS_THICKFRAME and asking DWM for rounded corners.
        Bound to <Map>: Tk restores WS_CAPTION whenever it (re-)maps the window,
        so this must run after every map (first show, minimize→restore, deiconify)."""
        try:
            u, d = _caption_api()
            hwnd = u.GetAncestor(self.root.winfo_id(), 2)  # GA_ROOT
            if not hwnd:
                return
            GWL_STYLE, WS_CAPTION, WS_THICKFRAME = -16, 0x00C00000, 0x00040000
            style = u.GetWindowLongW(hwnd, GWL_STYLE)
            if style & (WS_CAPTION | WS_THICKFRAME):
                u.SetWindowLongW(hwnd, GWL_STYLE, style & ~(WS_CAPTION | WS_THICKFRAME))
                SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_FRAMECHANGED = 1, 2, 4, 0x20
                u.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                               SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED)
            pref = ctypes.c_int(2)                      # DWMWCP_ROUND
            d.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
        except Exception as exc:
            log("去除系统标题栏失败: %r" % (exc,))

    def _title_press(self, e):
        """标题栏按下：双击＝最小化，否则让后台线程按光标实时移动窗口。"""
        dbl = self._dbl_ms
        if dbl and e.time - self._cap_last_t <= dbl:
            self._cap_last_t = -10 ** 9      # 这一对已经消费掉，不算下一次的双击
            self.root.iconify()
            return
        self._cap_last_t = e.time
        off = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())
        hwnd = self._root_hwnd()
        if not hwnd:
            # 拿不到根句柄才退回 Tk 逐帧移动：慢半拍，但窗口总比动不了强
            self._drag_off = off
            return
        self._drag_off = None
        stop = threading.Event()             # 每次按下各自一个，旧线程立刻收工
        self._drag_stop = stop
        threading.Thread(target=self._drag_loop, args=(hwnd, off, stop),
                         daemon=True, name="TitleDrag").start()

    def _root_hwnd(self):
        try:
            return _caption_api()[0].GetAncestor(self.root.winfo_id(), 2)  # GA_ROOT
        except Exception as exc:
            log("取根窗口句柄失败: %r" % (exc,))
            return None

    def _drag_loop(self, hwnd, off, stop):
        """约 250Hz 采样光标 + SetWindowPos：移动不经过 Tcl，所以跟手。
        这里**绝不能碰任何 Tk 对象**——Tcl 解释器不可重入，v2.4.3 就是
        在 Tk 回调里跑系统模态移动循环，sent message 重入派发给 Tk → abort() 闪退。
        松手由 GetAsyncKeyState 自己判，抬起事件丢了也不会卡住。"""
        try:
            u, _d = _caption_api()
            pt = wt.POINT()
            p_pt = ctypes.pointer(pt)      # 循环外建一次，每帧省一次分配
            while not stop.is_set():
                if not (u.GetAsyncKeyState(VK_LBUTTON) & 0x8000):
                    break
                if not u.IsWindow(hwnd) or not u.GetCursorPos(p_pt):
                    break
                u.SetWindowPos(hwnd, None, pt.x - off[0], pt.y - off[1], 0, 0,
                               SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
                time.sleep(0.004)
        except Exception as exc:
            log("拖拽线程异常: %r" % (exc,))
        finally:
            stop.set()

    def _drag_move(self, e):
        off = self._drag_off
        if off:
            self.root.geometry("+%d+%d" % (e.x_root - off[0], e.y_root - off[1]))

    def _drag_end(self, _e=None):
        self._drag_off = None
        self._drag_stop.set()

    def show_disclaimer(self):
        """首次打开（未确认过声明）弹出免责声明，确认后才继续使用。"""
        F, px = self.FONT, self.px
        dlg = tk.Toplevel(self.root)
        dlg.title("免责声明")
        dlg.configure(bg=self.BG)
        dlg.transient(self.root)
        dlg.resizable(False, False)
        box = self._card(dlg)
        box.pack(fill="both", expand=True, padx=px(12), pady=px(12))
        inner = tk.Frame(box, bg=self.CARD)
        inner.pack(fill="both", expand=True, padx=px(14), pady=px(12))
        tk.Label(inner, text="【免责声明】", bg=self.CARD, fg=self.FG,
                 font=(F, 12, "bold")).pack(anchor="w")
        tk.Label(inner, text=DISCLAIMER_TEXT, bg=self.CARD, fg=self.FG,
                 font=(F, 10), justify="left",
                 wraplength=px(470)).pack(anchor="w", pady=(px(8), 0))
        tk.Label(inner, text=DISCLAIMER_AGREE, bg=self.CARD, fg=self.ACCENT,
                 font=(F, 10, "bold"), justify="left",
                 wraplength=px(470)).pack(anchor="w", pady=(px(10), 0))
        btns = tk.Frame(dlg, bg=self.BG)
        btns.pack(pady=(0, px(14)))

        def agree():
            self.stats.set_setting("disclaimer_ok", True)
            self.stats.save(force=True)
            dlg.destroy()

        def decline():
            dlg.destroy()
            self.quit_app()

        self._btn(btns, "我已阅读并认可，继续使用", agree).pack(side="left",
                                                            padx=px(8))
        self._btn(btns, "不同意并退出", decline, danger=True).pack(side="left")
        self._center_dialog(dlg)
        dlg.grab_set()
        dlg.focus_force()

    # ---------- 构建 ----------

    def _build(self):
        root = self.root
        root.title(APP_TITLE)
        root.configure(bg=self.BG)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        icon = find_icon_file()
        if icon:
            try:
                root.iconbitmap(icon)
            except Exception:
                pass
        try:
            self._apply_style()
        except Exception:
            pass

        root.bind("<Escape>", lambda _e: self.hide())
        F = self.FONT
        self.kpi = {}
        self.kpi_full = {}
        self.kpi_short = {}
        self.s = DPI_SCALE                      # 1.0=100% 缩放，1.25=125%，1.5=150%
        try:                                    # 字号也按真实 DPI 换算（1pt=1/72 英寸）
            root.tk.call("tk", "scaling", 1.3333 * self.s)
        except Exception:
            pass

        def px(v):
            return max(1, int(round(v * self.s)))
        self.px = px
        sh = root.winfo_screenheight() / self.s   # 换回逻辑高度再判断小屏
        self.compact = sh < 900
        self.m = m = px(18)                       # 左右外边距
        pad = px(4 if self.compact else 5)
        self.row_h = px(19 if self.compact else 23)    # 近七天每行高度
        self.kpi_pad = kpi_pad = px(7 if self.compact else 9)
        self._kpi_pad = kpi_pad
        self.pad = pad

        # --- 内置标题栏：系统标题栏隐藏，最小化/关闭与拖拽融合在窗口内 ---
        # Strip is applied on <Map> (see _strip_native_caption): Tk re-adds WS_CAPTION
        # when it maps the window, so clearing it before mapping has no effect.
        bar_bg = self.CARD                   # 标题栏走卡片色，和正文底分开，窗口才有「外壳」感
        bar = tk.Frame(root, bg=bar_bg)
        bar.pack(fill="x", side="top")
        self.title_bar = bar
        self._drag_off = None
        self._drag_stop = threading.Event()
        self._drag_stop.set()             # 没有进行中的拖拽；按下时才换成新的事件对象
        self._cap_last_t = -10 ** 9
        try:
            self._dbl_ms = _caption_api()[0].GetDoubleClickTime() or 500
        except Exception:
            self._dbl_ms = 500

        title_lab = tk.Label(bar, text=APP_TITLE, bg=bar_bg, fg=self.CAP_FG,
                             font=(F, 13, "bold"))
        title_lab.pack(side="left", padx=(px(12), 0), pady=px(8))
        btns_f = tk.Frame(bar, bg=bar_bg)
        btns_f.pack(side="right")
        close_lab = tk.Label(btns_f, text="✕", bg=bar_bg, fg=self.CAP_FG,
                             font=(F, 12, "bold"), padx=px(12), pady=px(7))
        close_lab.pack(side="right")
        min_lab = tk.Label(btns_f, text="—", bg=bar_bg, fg=self.CAP_FG,
                           font=(F, 11, "bold"), padx=px(12), pady=px(7))
        min_lab.pack(side="right")

        def _hover_close(e):
            close_lab.configure(bg=self.DANGER if e.type == "5" else bar_bg,
                                fg="#ffffff" if e.type == "5" else self.CAP_FG)

        def _hover_min(e):
            min_lab.configure(bg=self.BTN_HOVER if e.type == "5" else bar_bg)

        close_lab.bind("<Enter>", _hover_close)
        close_lab.bind("<Leave>", _hover_close)
        min_lab.bind("<Enter>", _hover_min)
        min_lab.bind("<Leave>", _hover_min)
        close_lab.bind("<Button-1>", lambda _e: self.on_close())
        min_lab.bind("<Button-1>", lambda _e: root.iconify())
        for _w in (bar, title_lab):
            _w.bind("<Button-1>", self._title_press)
            _w.bind("<B1-Motion>", self._drag_move)
            _w.bind("<ButtonRelease-1>", self._drag_end)

        # --- 顶部：快捷栏 ---
        head = tk.Frame(root, bg=self.BG)
        head.pack(fill="x", padx=m, pady=(px(10), px(6)))

        nav = tk.Frame(head, bg=self.BG)
        nav.pack(anchor="w", pady=(px(8), 0))
        for name in self.PAGES:
            b = tk.Label(nav, text=self.PAGE_NAMES[name], font=(F, 10, "bold"),
                         bg=self.BG, fg=self.MUTED, padx=px(14), pady=px(5),
                         cursor="hand2")
            b.pack(side="left", padx=(0 if name == "home" else px(6), 0))
            b.bind("<Button-1>", lambda _e, n=name: self.show_page(n))
            b.bind("<Enter>", lambda _e, n=name: self._nav_hover(n, True))
            b.bind("<Leave>", lambda _e, n=name: self._nav_hover(n, False))
            self.nav[name] = b

        # --- 底部状态栏（固定，不随内容滚动） ---
        self.status = tk.Label(root, text="", bg=self.BG, fg=self.MUTED,
                               font=(F, 8), anchor="w")
        self.status.pack(side="bottom", fill="x", padx=m, pady=(0, px(10)))

        # --- 页面容器：可滚动（内容过高/小屏时滚动，标准模式无滚动条） ---
        scroll_wrap = tk.Frame(root, bg=self.BG)
        scroll_wrap.pack(side="top", fill="both", expand=True, padx=(m, m))
        self._scroll = tk.Canvas(scroll_wrap, bg=self.BG, highlightthickness=0,
                                 bd=0, width=1, height=1)
        self._scroll_sb = ttk.Scrollbar(scroll_wrap, orient="vertical",
                                        command=self._scroll.yview)
        self._scroll.configure(yscrollcommand=self._scroll_sb.set)
        self._scroll.pack(side="left", fill="both", expand=True)
        self.container = tk.Frame(self._scroll, bg=self.BG)
        self._scroll_win = self._scroll.create_window((0, 0), window=self.container,
                                                      anchor="nw")
        self.container.bind("<Configure>", self._sync_scrollregion)
        self._scroll.bind("<Configure>",
                          lambda e: self._scroll.itemconfigure(self._scroll_win,
                                                               width=e.width))
        root.bind_all("<MouseWheel>", self._on_wheel)

        self.pages["home"] = self._build_home(self.container, m, pad, px(14))
        self.pages["settings"] = self._build_settings(self.container)
        self.pages["profiles"] = self._build_profiles(self.container)
        self.pages["about"] = self._build_about(self.container)

        self._win_w = px(560)
        root.bind("<Map>", self._strip_native_caption)
        root.update_idletasks()
        # 记录除滚动内容外的固定高度（首次滚动 Canvas 高度≈0）
        self._chrome_h = max(px(120), root.winfo_reqheight())
        self.show_page("home")
        root.minsize(px(500), px(300))

    def _card(self, parent):
        return tk.Frame(parent, bg=self.CARD, highlightthickness=1,
                        highlightbackground=self.LINE)

    def _btn(self, parent, text, cmd, danger=False):
        b = tk.Button(parent, text=text, command=cmd, font=(self.FONT, 9),
                      bg=self.CARD, fg=self.DANGER if danger else self.FG,
                      activebackground=self.BTN_HOVER,
                      activeforeground=self.DANGER if danger else self.FG,
                      relief="solid", bd=1, highlightthickness=0,
                      padx=self.px(10), pady=self.px(3), cursor="hand2")
        # tk.Button 只有按下色，没有悬停色；不加这对绑定就完全不像能点
        b.bind("<Enter>", lambda _e: b.configure(
            bg=self.WARN_BG if danger else self.BTN_HOVER))
        b.bind("<Leave>", lambda _e: b.configure(bg=self.CARD))
        return b

    def _check(self, parent, text, var, command=None, bg=None):
        """自绘复选框：选中＝实心蓝方块，未选＝空心方块。
        tk.Checkbutton 的指示器画对钩、ttk 画叉，同一页两种风格，所以自己画。"""
        bg = bg or parent["bg"]
        row = tk.Frame(parent, bg=bg)
        box = tk.Label(row, font=(self.FONT, 10), bg=bg, width=1, cursor="hand2")
        box.pack(side="left")
        lab = tk.Label(row, text=text, bg=bg, fg=self.FG, font=(self.FONT, 9),
                       cursor="hand2")
        lab.pack(side="left", padx=(self.px(2), 0))

        def redraw(*_a):
            on = bool(var.get())
            box.configure(text="■" if on else "□",
                          fg=self.ACCENT if on else self.MUTED)

        def click(_e=None):
            var.set(not bool(var.get()))
            if command:
                command()          # 命令里可能把 var 改回去（失败回滚），trace 会再刷一次

        var.trace_add("write", redraw)
        for wgt in (row, box, lab):
            wgt.bind("<Button-1>", click)
        row.toggle = click          # 唯一的切换入口，测试和键盘事件都走这里
        redraw()
        return row

    def _nav_hover(self, name, enter):
        """非激活页签悬停时给一点底色；激活页签由 show_page 负责，别覆盖。"""
        if self.page == name:
            return
        b = self.nav.get(name)
        if b is not None:
            b.configure(bg=self.BTN_HOVER if enter else self.BG)

    def _row_hover(self, key, enter):
        """明细表整行悬停高亮：底色记在 _row_bg/_row_hl，移出原样还原，
        不会盖掉斑马纹和合计行。"""
        base = self._row_bg.get(key)
        if base is None:
            return
        bg = self._row_hl.get(key, self.BTN_HOVER) if enter else base
        for wgt in self.table_row_widgets.get(key, ()):
            try:
                wgt.configure(bg=bg)
                for child in wgt.winfo_children():   # 寿命列的小圆点和数字在 life_cell 里
                    child.configure(bg=bg)
            except tk.TclError:
                pass                                  # 切主题销毁期间可能已经没了

    def _section_head(self, box, title):
        """卡片小标题：左侧蓝色竖条 + 加粗黑色文字，与首页卡片风格统一。"""
        bar = tk.Frame(box, bg=self.CARD)
        bar.pack(fill="x", padx=self.px(14), pady=(self.px(10), self.px(4)))
        tk.Frame(bar, bg=self.ACCENT, width=self.px(3),
                 height=self.px(13)).pack(side="left", padx=(0, self.px(7)))
        tk.Label(bar, text=title, bg=self.CARD, fg=self.FG,
                 font=(self.FONT, 10, "bold")).pack(side="left")
        return bar

    def _build_home(self, parent, m, pad, kpi_px):
        F = self.FONT
        px = self.px
        page = tk.Frame(parent, bg=self.BG)
        self.date_label = tk.Label(page, text="", bg=self.BG, fg=self.MUTED,
                                   font=(F, 9))
        self.date_label.pack(anchor="w", pady=(0, px(4)))
        page.profile_label = None

        kpi = tk.Frame(page, bg=self.BG)
        kpi.pack(fill="x", pady=(0, 0))
        self.kpi_delta = {}
        for col, (key, caption) in enumerate(
                (("today", "今日点击"),
                 ("avg7", "近7天日均"),
                 ("total", "总点击次数"))):
            kpi.columnconfigure(col, weight=1, uniform="kpi")
            box = self._card(kpi)
            box.configure(highlightbackground=self.KPI_FRAME)
            box.grid(row=0, column=col, sticky="nsew",
                     padx=(0 if col == 0 else px(8), 0))
            inner = tk.Frame(box, bg=self.CARD)
            inner.pack(fill="both", expand=True)
            value = tk.Label(inner, text="-", bg=self.CARD,
                             fg=self.FG if key == "total" else self.ACCENT,
                             font=(F, 15 if self.compact else 17, "bold"))
            value.pack(anchor="w", padx=kpi_px, pady=(self._kpi_pad, 0))
            cap_row = tk.Frame(inner, bg=self.CARD)
            cap_row.pack(anchor="w", padx=kpi_px, pady=(0, self._kpi_pad))
            tk.Label(cap_row, text=caption, bg=self.CARD, fg=self.MUTED,
                     font=(F, 9)).pack(side="left")
            delta = tk.Label(cap_row, text="", bg=self.CARD, fg=self.MUTED,
                             font=(F, 8, "bold"))
            delta.pack(side="left", padx=(px(6), 0))
            self.kpi[key] = value
            self.kpi_delta[key] = delta
            value.bind("<Enter>", lambda _e, k=key: self._kpi_hover(k, True))
            value.bind("<Leave>", lambda _e, k=key: self._kpi_hover(k, False))

        card = self._card(page)
        card.pack(fill="x", pady=(px(10), 0))
        self.table_card = card
        headers = ["按键", "寿命", "今日", "近7天日均", "总计"]
        self.table_headers = headers
        self.life_cells = {}
        self.life_dots = {}
        for col, text in enumerate(headers):
            card.columnconfigure(col, weight=2 if col == 0 else 1)
            tk.Label(card, text=text, bg=self.CARD, fg=self.MUTED, font=(F, 9, "bold"),
                     padx=kpi_px, pady=px(6),
                     anchor="w" if col in (0, 1) else "e").grid(
                row=0, column=col, sticky="we")
        tk.Frame(card, bg=self.LINE, height=1).grid(
            row=1, column=0, columnspan=len(headers), sticky="ew")
        self.table_row_widgets = {}
        rows = list(BUTTONS) + [("__total__", "合计")]
        self.table_rows = rows
        for i, (key, label) in enumerate(rows):
            self._make_table_row(card, i, key)
        tk.Label(page, text="注：合计＝表中显示各键自动相加（不含滚轮，隐藏侧键不计入）；"
                            "右上角「总点击次数」含隐藏键的真实总数",
                 bg=self.BG,
                 fg=self.MUTED, font=(F, 8), anchor="w").pack(fill="x", pady=(px(3), 0))

        # --- 近一月热力日历 ---
        week = self._card(page)
        week.pack(fill="x", pady=(px(10), 0))
        week_head = tk.Frame(week, bg=self.CARD)
        week_head.pack(fill="x", padx=kpi_px, pady=(px(9), px(2)))
        tk.Label(week_head, text="近一个月每日点击", bg=self.CARD, fg=self.FG,
                 font=(F, 10, "bold")).pack(side="left")
        self.week_note = tk.Label(week_head, text="", bg=self.CARD, fg=self.MUTED,
                                  font=(F, 9))
        self.week_note.pack(side="right")
        self.heat_h = px(24)
        self.canvas = tk.Canvas(week, bg=self.CARD, height=px(16) + 5 * self.heat_h + px(6),
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="x", padx=px(10), pady=(px(2), 0))
        self.canvas.bind("<Configure>", lambda _e: self.draw_heat())
        self.canvas.bind("<Motion>", self.heat_hover)
        self.canvas.bind("<Leave>", self.heat_leave)

        # 图例：不写出来没人知道那一串深浅圆点代表什么量级
        legend = tk.Frame(week, bg=self.CARD)
        self.heat_legend = legend
        legend.pack(fill="x", padx=px(10), pady=(px(2), px(9)))
        tk.Label(legend, text="少", bg=self.CARD, fg=self.MUTED,
                 font=(F, 8)).pack(side="left")
        for n in (200, 2000, 4000, 6400, 8000):
            tk.Label(legend, text="●", bg=self.CARD, fg=heat_color(n, self.HEAT_EMPTY),
                     font=(F, 8)).pack(side="left", padx=(px(3), 0))
        tk.Label(legend, text="多", bg=self.CARD, fg=self.MUTED,
                 font=(F, 8)).pack(side="left", padx=(px(3), 0))
        tk.Label(legend, text="空心圆＝0 次　·　「空」＝无记录　·　「—」＝还没到",
                 bg=self.CARD, fg=self.MUTED, font=(F, 8)).pack(side="right")

        btns = tk.Frame(page, bg=self.BG)
        btns.pack(fill="x", pady=(px(10), px(12)))
        self.btn_admin = self._btn(btns, "以管理员身份重启", self.restart_as_admin)
        self.btn_admin.pack(side="left", padx=(0, px(8)))
        self.warn_anchor = btns
        self.home_buttons_frame = btns
        for text, cmd, danger in (("隐藏到后台", self.hide, False),
                                  ("退出程序", self.quit_app, True)):
            self._btn(btns, text, cmd, danger).pack(side="left", padx=(0, px(8)))
        return page

    def _make_table_row(self, card, i, key):
        F = self.FONT
        px = self.px
        pad = self.pad
        bold = key == "__total__"
        bg = self.ROW_TOTAL if bold else (self.STRIPE if i % 2 else self.CARD)
        # 合计行本来就在蓝底上，用 BTN_HOVER 会把它「洗白」，所以单独给一档
        self._row_bg[key] = bg
        self._row_hl[key] = self.ROW_TOTAL_HOVER if bold else self.BTN_HOVER
        font = (F, 10, "bold" if bold else "normal")
        fg = self.ACCENT if bold else self.FG
        widgets = []
        lab = tk.Label(card, text="", bg=bg, fg=fg, font=font,
                       padx=px(14), pady=pad, anchor="w")
        widgets.append(lab)
        lab.grid(row=i + 2, column=0, sticky="we")
        life_cell = tk.Frame(card, bg=bg)
        life_cell.grid(row=i + 2, column=1, sticky="we")
        dot = tk.Label(life_cell, text="●", bg=bg, fg=bg,
                       font=(F, 7))
        dot.pack(side="left", padx=(px(6), px(3)))
        self.life_dots[key] = dot
        life = tk.Label(life_cell, text="", bg=bg, fg=fg,
                        font=(F, 10, "bold" if bold else "normal"),
                        padx=px(2), pady=pad, anchor="w")
        life.pack(side="left")
        self.life_cells[key] = life
        widgets.append(life_cell)
        for col in range(2, len(self.table_headers)):
            lab = tk.Label(card, text="-", bg=bg, fg=fg, font=font,
                           padx=px(14), pady=pad, anchor="e")
            lab.grid(row=i + 2, column=col, sticky="we")
            self.cells[(key, col)] = lab
            widgets.append(lab)
        for wgt in widgets:
            wgt.bind("<Enter>", lambda _e, k=key: self._row_hover(k, True))
            wgt.bind("<Leave>", lambda _e, k=key: self._row_hover(k, False))
        self.table_row_widgets[key] = widgets

    def _build_settings(self, parent):
        F = self.FONT
        px = self.px
        page = tk.Frame(parent, bg=self.BG)

        def section(title):
            box = self._card(page)
            box.pack(fill="x", pady=(0, px(10)))
            self._section_head(box, title)
            return box

        # --- 按键管理 ---
        box = section("按键显示与命名（隐藏只是不显示，仍然照常计数）")
        self.btn_rows = {}
        for i, (key, default_name) in enumerate(CLICK_BUTTONS):
            row = tk.Frame(box, bg=(self.CARD if i % 2 == 0 else self.STRIPE))
            row.pack(fill="x", padx=px(14), pady=0)
            var = tk.BooleanVar(value=True)
            if key in HIDEABLE_KEYS:
                self._check(row, "显示", var, self.apply_key_settings,
                            bg=row["bg"]).pack(side="left", pady=px(3))
            else:
                tk.Label(row, text="固定显示", bg=row["bg"], fg=self.MUTED,
                         font=(F, 9)).pack(side="left", padx=(px(2), 0), pady=px(3))
            entry = self._entry(row, width=12)
            entry.pack(side="right")
            self._btn(row, "重命名",
                      lambda k=key, e=entry: self.rename_key(k, e)).pack(
                side="right", padx=(0, px(6)))
            tk.Label(row, text="自定义名称：", bg=row["bg"], fg=self.FG,
                     font=(F, 9)).pack(side="right", padx=(0, px(6)))
            tk.Label(row, text="（原：%s）" % default_name, bg=row["bg"],
                     fg=self.MUTED, font=(F, 8)).pack(side="left",
                                                      padx=(px(10), 0))
            self.btn_rows[key] = (var, entry)
        row = tk.Frame(box, bg=self.CARD)
        row.pack(fill="x", padx=px(14), pady=(px(4), px(8)))
        tk.Label(row, text="滚轮：上/下滚合并记录（滚一格计 1），固定显示，不计入合计与总点击",
                 bg=self.CARD,
                 fg=self.MUTED, font=(F, 9)).pack(side="left")
        self._load_key_settings_to_ui()

        # --- 开机自启动 ---
        box = section("开机自启动")
        row = tk.Frame(box, bg=self.CARD)
        row.pack(fill="x", padx=px(14), pady=(px(2), px(8)))
        self.autostart = tk.BooleanVar(value=startup_enabled())
        self._check(row, "登录后台静默运行（管理员权限可统计游戏内点击）",
                    self.autostart, self.toggle_startup,
                    bg=self.CARD).pack(side="left")
        self.autostart_warn = tk.Label(row, bg=self.CARD, fg=self.WARN, font=(F, 9))
        self.autostart_warn.pack(side="right")
        self.autostart.trace_add(
            "write", lambda *_a: self.autostart_warn.configure(
                text="" if self.autostart.get() else "⚠ 未开启，开机后不会自动记录"))
        self.autostart_warn.configure(text="" if self.autostart.get()
                                     else "⚠ 未开启，开机后不会自动记录")

        # --- 界面模式（深色模式开关）---
        box = section("界面模式")
        row = tk.Frame(box, bg=self.CARD)
        row.pack(fill="x", padx=px(14), pady=(px(2), px(8)))
        self.theme_var = tk.StringVar(value=self.theme_mode)
        self.theme_btns = {}
        for mode, label in (("auto", "跟随系统"), ("light", "浅色"), ("dark", "深色")):
            b = tk.Radiobutton(row, text=label, value=mode, variable=self.theme_var,
                               indicatoron=False, command=self._on_theme_choice,
                               font=(F, 9), width=9, relief="solid", bd=1,
                               highlightthickness=0, cursor="hand2",
                               padx=px(10), pady=px(3))
            b.pack(side="left", padx=(0, px(6)))
            self.theme_btns[mode] = b
        self._repaint_theme_btns()
        tk.Label(row, text="（深色模式仅影响本软件窗口，不改动系统主题）",
                 bg=self.CARD, fg=self.MUTED, font=(F, 8)).pack(side="left",
                                                               padx=(px(4), 0))

        # --- 备份 ---
        box = section("备份与恢复")
        r1 = tk.Frame(box, bg=self.CARD)
        r1.pack(fill="x", padx=px(14), pady=px(2))
        self._btn(r1, "更改…", self.change_backup_dir).pack(side="right")
        tk.Label(r1, text="备份位置：", bg=self.CARD, fg=self.FG,
                 font=(F, 9)).pack(side="left")
        self.backup_dir_label = tk.Label(r1, text="", bg=self.CARD, fg=self.MUTED,
                                         font=(F, 9), anchor="w", justify="left",
                                         wraplength=px(300))
        self.backup_dir_label.pack(side="left", fill="x", expand=True,
                                   padx=(px(4), px(6)))
        r2 = tk.Frame(box, bg=self.CARD)
        r2.pack(fill="x", padx=px(14), pady=px(2))
        self._btn(r2, "立即手动备份", self.manual_backup).pack(side="left")
        self._btn(r2, "从备份恢复…", self.restore_dialog).pack(side="left",
                                                          padx=(px(8), 0))
        r3 = tk.Frame(box, bg=self.CARD)
        r3.pack(fill="x", padx=px(14), pady=(px(2), px(8)))
        tk.Label(r3, text="每天首次保存自动备份一份，保留最近 7 份；备份含计数、设置与鼠标档案",
                 bg=self.CARD, fg=self.MUTED, font=(F, 8)).pack(side="left")

        # --- 数据文件夹 ---
        box = section("数据文件")
        r = tk.Frame(box, bg=self.CARD)
        r.pack(fill="x", padx=px(14), pady=(px(2), px(8)))
        self._btn(r, "打开数据文件夹", self.open_folder).pack(side="right")
        self.data_file_label = tk.Label(r, text="", bg=self.CARD, fg=self.MUTED,
                                        font=(F, 9), anchor="w", justify="left",
                                        wraplength=px(360))
        self.data_file_label.pack(side="left", fill="x", expand=True, padx=(0, px(6)))
        return page

    def _build_profiles(self, parent):
        F = self.FONT
        px = self.px
        page = tk.Frame(parent, bg=self.BG)
        head = tk.Frame(page, bg=self.BG)
        head.pack(fill="x", pady=(0, px(6)))
        tk.Label(head, text="每行一只鼠标；切换档案后按档案起始日重新计算寿命用量，历史数据不删",
                 bg=self.BG, fg=self.MUTED, font=(F, 9)).pack(side="left")
        self._btn(head, "＋ 新建档案", self.new_profile).pack(side="right")
        self.profile_list = tk.Frame(page, bg=self.BG)
        self.profile_list.pack(fill="x")
        tk.Label(page, text="寿命为额定参考值：以官方数据为准，受使用环境影响极大",
                 bg=self.BG, fg=self.MUTED, font=(F, 8)).pack(anchor="w", pady=(px(6), 0))
        return page

    def _build_about(self, parent):
        F = self.FONT
        px = self.px
        page = tk.Frame(parent, bg=self.BG)
        box = self._card(page)
        box.pack(fill="x")
        inner = tk.Frame(box, bg=self.CARD)
        inner.pack(fill="x", padx=px(14), pady=px(10))
        tk.Label(inner, text=APP_TITLE, bg=self.CARD, fg=self.FG,
                 font=(F, 12, "bold")).pack(anchor="w")
        tk.Label(inner, text="版本 v%s　%s" % (APP_VERSION, BYLINE), bg=self.CARD,
                 fg=self.FG, font=(F, 10)).pack(anchor="w", pady=(px(4), 0))
        link = tk.Label(inner, text="GitHub 主页：" + GITHUB_URL, bg=self.CARD,
                        fg=self.ACCENT, font=(F, 10, "underline"), cursor="hand2")
        link.pack(anchor="w", pady=(px(4), 0))
        link.bind("<Button-1>", lambda _e: self._open_link(GITHUB_URL))
        declare = DISCLAIMER_TEXT + DISCLAIMER_AGREE
        tk.Label(inner, text="【免责声明】", bg=self.CARD, fg=self.FG,
                 font=(F, 11, "bold")).pack(anchor="w", pady=(px(12), 0))
        tk.Label(inner, text=declare, bg=self.CARD, fg=self.FG,
                 font=(F, 10), justify="left",
                 wraplength=px(490)).pack(anchor="w", pady=(px(4), px(4)))
        tk.Label(inner, text="【使用说明】", bg=self.CARD, fg=self.FG,
                 font=(F, 11, "bold")).pack(anchor="w", pady=(px(10), 0))
        tk.Label(inner, text=HELP_TEXT, bg=self.CARD, fg=self.FG,
                 font=(F, 10), justify="left",
                 wraplength=px(490)).pack(anchor="w", pady=(px(4), 0))
        return page

    # ---------- 页面切换 ----------

    def show_page(self, name):
        self.page = name
        for k, f in self.pages.items():
            if k == name:
                f.pack(fill="x")
            else:
                f.pack_forget()
        for k, b in self.nav.items():
            active = (k == name)
            b.configure(bg=self.CARD if active else self.BG,
                        fg=self.ACCENT if active else self.MUTED,
                        relief="solid" if active else "flat", bd=1)
        if name == "profiles":
            self.render_profiles()
        if name == "settings":
            self._refresh_settings_labels()
        self._fit_window()

    def _sync_scrollregion(self, _e=None):
        try:
            self._scroll.configure(scrollregion=self._scroll.bbox("all"))
        except Exception:
            pass

    def _on_wheel(self, event):
        try:
            top, bottom = self._scroll.yview()
        except Exception:
            return
        if bottom >= 1.0 and top <= 0.0:      # 内容未溢出，无需滚动
            return
        step = int(-1 * (event.delta / 120)) or (-1 if event.delta < 0 else 1)
        self._scroll.yview_scroll(step, "units")

    def _fit_window(self):
        self.root.update_idletasks()
        # 所有页面统一用首页的内容高度并冻结：切页 / 警告框显隐都不再改变窗口大小
        if not getattr(self, "_fixed_h", 0):
            home = self.pages.get("home")
            content_h = home.winfo_reqheight() if home else 0
            avail = self.root.winfo_screenheight() - self.px(40)
            h = min(self._chrome_h + content_h + self.px(6), avail)
            h = max(h, self.px(320))
            self._fixed_h = h
        h = self._fixed_h
        if not getattr(self, "_placed", False):
            # 首次：水平居中、垂直放在屏幕上 1/3 处；之后左上角固定，窗口大小恒定
            x = (self.root.winfo_screenwidth() - self._win_w) // 2
            y = max(self.px(20), (self.root.winfo_screenheight() - h) // 3)
            self._placed = True
        else:
            x, y = self.root.winfo_x(), self.root.winfo_y()
            if y + h > self.root.winfo_screenheight() - self.px(20):
                y = max(self.px(20), self.root.winfo_screenheight() - self.px(20) - h)
        self.root.geometry("%dx%d+%d+%d" % (self._win_w, h, x, y))
        self._sync_scrollregion()

    def _kpi_hover(self, key, enter):
        """悬停 KPI 看精确全值，移出回到缩写。
        移出时只改这一个 Label：以前调 refresh(force=True) 会重绘整页
        （表格 + 35 格热力日历），鼠标每划过一次就全量刷新一次。"""
        lab = self.kpi.get(key)
        if lab is None:
            return
        if enter:
            if key in self.kpi_full:
                lab.configure(text=self.kpi_full[key])
        elif self.page == "home" and key in self.kpi_short:
            lab.configure(text=self.kpi_short[key])

    # ---------- 刷新 ----------

    def poll(self):
        changed = False
        while True:
            try:
                item = self.sink.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, tuple):
                kind, payload = item
                if kind == "ready":
                    self.status.configure(
                        text="运行中 · 后台监听中（系统级鼠标钩子，空闲占用≈0）", fg=self.MUTED)
                elif kind == "error":
                    self.status.configure(text=str(payload), fg=self.DANGER)
                elif kind == "show":
                    self.show()
                elif kind == "quit":
                    self.quit_app()
                    return
                elif kind == "toggle-startup":
                    self.autostart.set(not self.autostart.get())
                    self.toggle_startup()
                elif kind == "switch-profile":
                    self.switch_profile_by_id(payload)
                elif kind == "wheel-notches":
                    # 滚轮一格算一次，不足一格的量由 Stats 累积到下一条消息
                    self.stats.add_wheel_delta(payload)
                    changed = True
                    HOOK_STATE["blocked_logged"] = False
            else:
                self.stats.add(item)
                changed = True
                HOOK_STATE["blocked_logged"] = False  # 收到真实点击 → 钩子工作正常
        now = datetime.datetime.now().timestamp()
        if self.stats.dirty:
            if changed is False or now - self.last_save > 3:
                self.stats.save()
                self.last_save = now
        self.refresh(force=changed)
        if now - self.last_tip >= 5:
            self.update_tip()
            self.last_tip = now
        if now - self.last_status >= 2:
            self.last_status = now
            self.update_status()
        if now - self.last_health >= 3:
            self.last_health = now
            self.health_check()
        self.root.after(400, self.poll)

    def health_check(self):
        """看门狗：后台线程或托盘图标没了就重新拉起，避免"图标在但点了没反应"。"""
        thread = WORKER.get("thread")
        thread_alive = thread is not None and thread.is_alive()
        tray_alive = is_tray_window_alive()
        if thread_alive and tray_alive:
            self.check_hook_blocked()
            return
        tray_log("看门狗：后台线程存活=%s 托盘窗口存活=%s" % (thread_alive, tray_alive))
        TRAY["hwnd"] = None
        TRAY["ready"] = False
        if thread_alive:
            # 线程还在（计数没停），只是窗口丢了 —— start_worker 这种情况
            # 会直接 return，所以必须让钩子线程自己在消息循环里重建。
            if not request_tray_rebuild():
                tray_log("重建请求投递失败，等待下一轮看门狗")
            return
        start_worker(self.sink)

    def check_hook_blocked(self):
        """判断钩子是不是被"吃掉了"：系统检测到鼠标活动，但钩子长时间收不到任何事件。"""
        if not HOOK_STATE.get("installed") or not HOOK_STATE.get("last_event"):
            return
        idle = system_idle_seconds()
        hook_idle = time.monotonic() - HOOK_STATE["last_event"]
        if idle is not None and idle < 2.0 and hook_idle > 30.0:
            self.blocked_strikes += 1
        else:
            self.blocked_strikes = 0
        if self.blocked_strikes < 2:
            self.hook_warning = False
            return
        self.blocked_strikes = 0
        self.hook_warning = True
        if not HOOK_STATE.get("blocked_logged"):
            HOOK_STATE["blocked_logged"] = True
            tray_log("⚠ 系统检测到鼠标活动，但钩子已 %.0f 秒收不到事件："
                     "疑似被反作弊驱动/更高权限窗口拦截（管理员权限=%s）"
                     % (hook_idle, is_elevated()))
        request_hook_reinstall()  # 尝试自愈（被系统摘除时有效）

    def update_status(self):
        parts = ["v" + APP_VERSION, "运行中"]
        elevated = is_elevated()
        parts.append("管理员权限" if elevated
                     else "普通权限 ⚠ 未获取管理员权限，可能无法记录游戏内点击")
        if AUTOSTART["mode"] == "task":
            parts.append("自启动:计划任务(最高权限)")
        elif AUTOSTART["mode"] == "run":
            parts.append("自启动:注册表")
        if self.hook_warning:
            parts.append("⚠ 钩子疑似被拦截，游戏内可能不计数")
        self.status.configure(
            text=" · ".join(parts),
            fg=self.DANGER if self.hook_warning else (self.MUTED if elevated else self.WARN))

    def refresh(self, force=False, life_now=False):
        now_mono = time.monotonic()
        life_due = life_now or (now_mono - self.last_life_refresh >= LIFE_REFRESH_SECONDS)
        if not force and not life_due:
            return
        stats = self.stats
        prof = current_profile(stats)
        update_tray_menu_state(stats)
        if not force:
            self.last_life_refresh = now_mono
            self._refresh_life_cells(prof, visible_keys(prof))
            if self.page == "profiles":
                self.render_profiles()
            return
        t = today()
        # 热力日历：今天往前 27 天（共 28 天），向两侧补齐到整周（周一开头）成 5×7
        grid_start = t - datetime.timedelta(days=27 + ((t - datetime.timedelta(days=27)).weekday()))
        self.heat_grid = [grid_start + datetime.timedelta(days=i) for i in range(35)]
        self.heat_month = t.month
        days7 = [t - datetime.timedelta(days=i) for i in range(6, -1, -1)]
        rows7 = [stats.day_row(d) for d in days7]
        today_row = rows7[-1]
        sum7 = {k: sum(row.get(k, 0) for row in rows7) for k in KEYS}
        avg7 = {k: sum7[k] / 7.0 for k in KEYS}
        avg7_all = stats.click_sum(sum7) / 7.0
        total = dict(stats.total)
        total_all = stats.click_sum(total)

        def kpi_set(key, short, full):
            lab = self.kpi[key]
            self.kpi_short[key] = short
            self.kpi_full[key] = full
            if self.page == "home":
                lab.configure(text=short)

        kpi_set("today", fmt(stats.click_sum(today_row)),
                fmt_full(stats.click_sum(today_row)))
        kpi_set("avg7", "%.1f" % avg7_all, "%.1f" % avg7_all)
        kpi_set("total", fmt(total_all), fmt_full(total_all) + " 次")

        # KPI 环比箭头：今日 vs 昨日、近7天日均 vs 前7天日均；总点击无环比口径显示"—"
        yday = stats.click_sum(stats.day_row(t - datetime.timedelta(days=1)))
        today_all = stats.click_sum(today_row)
        prev7 = stats.click_sum(stats.sum_range(
            t - datetime.timedelta(days=13), t - datetime.timedelta(days=7))) / 7.0

        def kpi_delta(key, cur, base):
            lab = self.kpi_delta.get(key)
            if lab is None or self.page != "home":
                return
            if base and base > 0:
                pct = (cur - base) / base * 100.0
                if pct >= 0:
                    lab.configure(text="▲ +%.1f%%" % pct, fg=self.UP)
                else:
                    lab.configure(text="▼ %.1f%%" % pct, fg=self.DOWN)
            else:
                lab.configure(text="—", fg=self.MUTED)

        kpi_delta("today", today_all, yday)
        kpi_delta("avg7", avg7_all, prev7)
        kpi_delta("total", 0, 0)

        vis = visible_keys(prof)
        chart = {k: (today_row[k], avg7[k], total[k]) for k in KEYS}
        # 合计＝表里显示的各点击键自动相加（不含滚轮；隐藏的侧键不参与），
        # 保证「各行相加＝合计」所见即所算；KPI 总点击仍是全部 5 个点击键的真实总数。
        vis_click = [k for k in CLICK_KEYS if k in vis]
        chart["__total__"] = (
            sum(today_row[k] for k in vis_click),
            sum(sum7[k] for k in vis_click) / 7.0,
            sum(total[k] for k in vis_click))
        for key, widgets in self.table_row_widgets.items():
            if key != "__total__" and key not in vis:
                for wgt in widgets:
                    wgt.grid_remove()
                continue
            for wgt in widgets:
                wgt.grid()
            if key == "__total__":
                widgets[0].configure(text="合计")
            else:
                widgets[0].configure(text=profile_label(prof, key))
        for (key, col), lab in self.cells.items():
            if key not in chart:
                continue
            if key != "__total__" and key not in vis:
                continue
            value = chart[key][col - 2]
            lab.configure(text=fmt(int(value)) if col in (2, 4) else "%.1f" % value)

        if life_due:
            self.last_life_refresh = now_mono
            self._refresh_life_cells(prof, vis)
            if self.page == "profiles":
                self.render_profiles()

        weekday = "一二三四五六日"[t.weekday()]
        self.date_label.configure(text="今天 %s  星期%s　·　当前档案：%s"
                                  % (t.isoformat(), weekday, prof["name"]))
        self._heat_default_note = ""
        self.week_note.configure(text="")
        self.draw_heat()

    def _refresh_life_cells(self, prof: dict, vis: list[str]) -> None:
        """Refresh the life column from the profile's independent history."""
        used_p, _since = self.profile_usage(prof)
        life_lim = prof.get("life") or DEFAULT_LIFE
        w_items = []
        for key in KEYS:
            lab = self.life_cells.get(key)
            if lab is None or key not in vis:
                continue
            dot = self.life_dots.get(key)
            pct = life_percent(used_p.get(key, 0), life_lim.get(key, 0))
            if pct is None:
                lab.configure(text="—", fg=self.MUTED)
                if dot is not None:
                    dot.configure(fg=dot.cget("bg"))
            else:
                w_items.append((used_p.get(key, 0), pct))
                lab.configure(text="%.2f%%" % pct, fg=self.FG)
                if dot is not None:
                    dot.configure(fg=life_color(pct))
        lab = self.life_cells.get("__total__")
        if lab is not None:
            dot = self.life_dots.get("__total__")
            agg = weighted_life_pct(w_items)
            if agg is None:
                lab.configure(text="—", fg=self.MUTED)
                if dot is not None:
                    dot.configure(fg=dot.cget("bg"))
            else:
                lab.configure(text="%.2f%%" % agg, fg=self.FG)
                if dot is not None:
                    dot.configure(fg=life_color(agg))

    def profile_usage(self, prof):
        """档案用量：从 since 日起累计该档案独立的点击键与滚轮。"""
        try:
            since = datetime.date.fromisoformat(prof.get("since") or today().isoformat())
        except ValueError:
            since = today()
        used = self.stats.sum_range_for(prof["id"], since, today())
        return used, since

    def draw_heat(self):
        """近一个月 5×7 热力日历：sqrt(n/8000) 走 life_color 渐变，
        窗口外补位日期淡化，今天描边，悬停在右侧注释显示明细。"""
        canvas = getattr(self, "canvas", None)
        grid = getattr(self, "heat_grid", None)
        if canvas is None or not grid:
            return
        F = self.FONT
        px = getattr(self, "px", lambda v: v)
        canvas.delete("all")
        width = canvas.winfo_width()
        if width < px(80):
            width = px(500)
        t = today()
        month = getattr(self, "heat_month", t.month)
        gap = px(3)
        head_h = px(16)
        cell_h = getattr(self, "heat_h", px(24))
        cell_w = max(px(20), (width - 6 * gap) // 7)
        self._heat_layout = (width, gap, head_h, cell_h, cell_w)
        # 星期表头
        for c, wd in enumerate("一二三四五六日"):
            cx = c * (cell_w + gap) + cell_w // 2
            canvas.create_text(cx, head_h // 2 + px(2), text=wd, font=(F, 8),
                               fill=self.MUTED)
        for idx, day in enumerate(grid):
            r, c = divmod(idx, 7)
            x0 = c * (cell_w + gap)
            y0 = head_h + r * cell_h
            x1, y1 = x0 + cell_w, y0 + cell_h - 2
            in_win = (day.month == month)
            is_today = day == t
            is_future = day > t
            cy = (y0 + y1) // 2
            row = self.stats.days.get(day.isoformat())
            cnt = self.stats.click_sum(row) if row else None
            if is_future:
                # 补齐到整周会把「还没到的日子」也画进来，标成「空」会被读成
                # 「那天我一次都没点」，必须和真正的无记录区分开。
                canvas.create_text((x0 + x1) // 2, cy, text="—", font=(F, 7),
                                   fill=self.MUTED)
            elif cnt is None:
                canvas.create_text((x0 + x1) // 2, cy, text="空", font=(F, 7),
                                   fill=self.MUTED)
            else:
                dot_col = heat_color(cnt, self.HEAT_EMPTY)
                if not in_win:
                    dot_col = mix_color(dot_col, self.HEAT_EMPTY, 0.82)
                tcol = self.FG if in_win else self.MUTED
                d = px(3)
                canvas.create_oval(x0 + px(5), cy - d, x0 + px(5) + d * 2, cy + d,
                                   fill=dot_col,
                                   outline=self.LINE if cnt == 0 else "")
                canvas.create_text(x0 + px(13), cy, anchor="w", text=fmt(cnt),
                                   font=(F, 8, "bold" if is_today else "normal"),
                                   fill=tcol)
            if is_today:
                canvas.create_rectangle(x0, y0, x1, y1, fill="",
                                        outline=self.ACCENT, width=2)
            self._heat_cells = getattr(self, "_heat_cells", {})
            self._heat_cells[idx] = (day, cnt, in_win)

    def heat_hover(self, event):
        """鼠标悬停热力格 → 右侧注释显示 日期+星期+次数。"""
        layout = getattr(self, "_heat_layout", None)
        cells = getattr(self, "_heat_cells", None)
        if not layout or not cells or not getattr(self, "week_note", None):
            return
        width, gap, head_h, cell_h, cell_w = layout
        c = event.x // (cell_w + gap) if cell_w + gap else -1
        r = (event.y - head_h) // cell_h if cell_h else -1
        lab = getattr(self, "week_note", None)
        if not (0 <= c < 7 and 0 <= r < 5):
            lab.configure(text=self._heat_default_note)
            return
        info = cells.get(r * 7 + c)
        if not info:
            return
        day, cnt, in_win = info
        wd = "一二三四五六日"[day.weekday()]
        if day > today():
            n_txt = "还没到这一天"
        else:
            n_txt = fmt_full(cnt) + " 次" if cnt is not None else "无记录"
        tag = "" if in_win else "（非本月）"
        lab.configure(text="%s 周%s%s：%s" % (day.isoformat(), wd, tag, n_txt))

    def heat_leave(self, _e=None):
        if getattr(self, "week_note", None):
            self.week_note.configure(text=self._heat_default_note)

    # ---------- 设置页交互 ----------

    def _load_key_settings_to_ui(self):
        prof = current_profile(self.stats)
        for key, (var, entry) in self.btn_rows.items():
            var.set(not profile_hidden(prof, key))
            entry.delete(0, "end")
            entry.insert(0, (prof.get("labels") or {}).get(key, ""))

    def _refresh_settings_labels(self):
        d = self.stats.backup_dir()
        self.backup_dir_label.configure(text=d)
        self.data_file_label.configure(text=DATA_FILE)

    def _on_theme_choice(self):
        mode = self.theme_var.get()
        if mode != self.theme_mode:
            self.switch_theme(mode)

    def _repaint_theme_btns(self):
        """Radio buttons as flat toggle chips: active chip uses accent."""
        for mode, b in getattr(self, "theme_btns", {}).items():
            active = (mode == self.theme_mode)
            try:
                b.configure(bg=self.ACCENT if active else self.CARD,
                            fg=self.CARD if active else self.FG,
                            activebackground=self.ACCENT if active else self.BTN_HOVER,
                            activeforeground=self.CARD if active else self.FG,
                            selectcolor=self.ACCENT if active else self.CARD)
            except tk.TclError:
                pass

    def apply_key_settings(self):
        prof = current_profile(self.stats)
        hidden = [k for k in HIDEABLE_KEYS
                  if k in self.btn_rows and not self.btn_rows[k][0].get()]
        prof["hidden"] = hidden
        self.stats.dirty = True
        self.refresh(force=True, life_now=True)

    def rename_key(self, key, entry):
        prof = current_profile(self.stats)
        text = entry.get().strip()
        if not text:
            prof.get("labels", {}).pop(key, None)
        else:
            prof.setdefault("labels", {})[key] = text[:12]
        self.btn_rows[key][1].delete(0, "end")
        self.btn_rows[key][1].insert(0, prof.get("labels", {}).get(key, ""))
        self.stats.dirty = True
        self.refresh(force=True, life_now=True)
        if self.page == "profiles":
            self.render_profiles()

    def change_backup_dir(self):
        dlg = self._simple_dialog("更改备份位置", "新备份文件夹绝对路径（留空=默认 数据目录\\backups）",
                                  self.stats.settings.get("backup_dir") or "")
        val = dlg.result
        if val is None:
            return
        val = val.strip().strip('"')
        if not val:
            self.stats.set_setting("backup_dir", None)
        else:
            try:
                os.makedirs(val, exist_ok=True)
                if not _dir_writable(val):
                    raise OSError("目录不可写")
            except Exception as exc:
                messagebox.showerror(APP_TITLE, "备份位置不可用：\n%r" % (exc,))
                return
            self.stats.set_setting("backup_dir", val)
        self.stats.save()
        self._refresh_settings_labels()
        messagebox.showinfo(APP_TITLE, "备份位置已更新。")

    def manual_backup(self):
        try:
            dest = self.stats.manual_backup()
        except Exception as exc:
            messagebox.showerror(APP_TITLE, "手动备份失败：\n%r" % (exc,))
            return
        messagebox.showinfo(APP_TITLE, "已备份到：\n%s" % dest)

    def _backup_label(self, path):
        """下拉框里带上点击总量与时间：备份目录里出现过内容完全不同的同名文件，
        只给文件名的话，恢复错哪份都要等恢复完才知道。"""
        base = os.path.basename(path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            days = raw.get("profile_days")
            if not isinstance(days, dict):
                days = {"": raw.get("days")} if isinstance(raw.get("days"), dict) else {}
            total = sum(Stats._int_val(row.get(k, 0))
                        for per in days.values() if isinstance(per, dict)
                        for row in per.values() if isinstance(row, dict)
                        for k in CLICK_KEYS)
            stamp = str(raw.get("updated") or "")[:16].replace("T", " ")
            return "%s　·　点击 %s 次　·　%s" % (base, fmt_full(total), stamp)
        except Exception as exc:
            log("读取备份摘要失败 %s: %r" % (base, exc))
            return base

    def restore_dialog(self):
        d, names = self.stats.list_backups()
        if not names:
            messagebox.showinfo(APP_TITLE, "备份目录里暂时没有备份文件：\n%s" % d)
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("从备份恢复")
        dlg.configure(bg=self.BG)
        dlg.transient(self.root)
        dlg.resizable(False, False)
        F = self.FONT
        px = self.px
        tk.Label(dlg, text="选择要恢复的备份（恢复前会自动备份当前数据）",
                 bg=self.BG, fg=self.FG, font=(F, 9)).pack(anchor="w",
                                                           padx=px(12), pady=(px(10), px(4)))
        labels = [self._backup_label(os.path.join(d, n)) for n in names]
        # width 单位是「字符」，中文标签按字符算会算少一半，Combobox 被撑到 480px 才够；
        # 所以按真实像素量出来再折算，取不到字体度量时退回 54。
        box = ttk.Combobox(dlg, values=labels, state="readonly", width=54)
        try:
            fnt = tkfont.Font(family=F, size=9, root=self.root)
            unit = max(1, fnt.measure("0"))
            need = max(fnt.measure(s) for s in labels)
            box.configure(width=min(90, max(40, need // unit + 2)))
        except Exception as exc:
            log("备份下拉框宽度测算失败: %r" % exc)
        box.set(labels[0])
        box.pack(padx=px(12))

        def do_restore():
            sel = box.get()
            if sel not in labels:
                return
            name = names[labels.index(sel)]
            if not messagebox.askyesno(
                    APP_TITLE, "将用备份「%s」覆盖当前全部数据（含设置与档案），确定？" % name,
                    parent=dlg):
                return
            try:
                total = self.stats.restore_backup(name)
            except Exception as exc:
                messagebox.showerror(APP_TITLE, "恢复失败：\n%r" % (exc,))
                return
            self._load_key_settings_to_ui()
            self.refresh(force=True, life_now=True)
            dlg.destroy()
            messagebox.showinfo(APP_TITLE, "已恢复，点击总计 %s 次。" % fmt_full(total))

        btns = tk.Frame(dlg, bg=self.BG)
        btns.pack(pady=px(10))
        self._btn(btns, "恢复", do_restore).pack(side="left", padx=px(6))
        self._btn(btns, "取消", dlg.destroy).pack(side="left")
        self._center_dialog(dlg)

    def _simple_dialog(self, title, label, initial):
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.configure(bg=self.BG)
        dlg.transient(self.root)
        dlg.resizable(False, False)
        F, px = self.FONT, self.px
        tk.Label(dlg, text=label, bg=self.BG, fg=self.FG, font=(F, 9)).pack(
            anchor="w", padx=px(12), pady=(px(10), px(4)))
        entry = self._entry(dlg, width=52)
        entry.insert(0, initial)
        entry.pack(padx=px(12))
        entry.focus_set()
        dlg.result = None

        def ok(_e=None):
            dlg.result = entry.get()
            dlg.destroy()

        btns = tk.Frame(dlg, bg=self.BG)
        btns.pack(pady=px(10))
        self._btn(btns, "确定", ok).pack(side="left", padx=px(6))
        self._btn(btns, "取消", lambda: (setattr(dlg, "result", None),
                                         dlg.destroy())).pack(side="left")
        dlg.bind("<Return>", ok)
        self._center_dialog(dlg)
        dlg.grab_set()
        self.root.wait_window(dlg)
        return dlg

    def _center_dialog(self, dlg):
        dlg.update_idletasks()
        w, h = dlg.winfo_width(), dlg.winfo_height()
        x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        dlg.geometry("+%d+%d" % (max(0, x), max(0, y)))

    # ---------- 档案页 ----------

    def render_profiles(self):
        for c in self.profile_list.winfo_children():
            c.destroy()
        F, px = self.FONT, self.px
        profs = ensure_profiles(self.stats)
        pid = self.stats.settings.get("profile_id")
        for prof in profs:
            active = prof["id"] == pid
            box = tk.Frame(self.profile_list, bg=self.CARD, highlightthickness=1,
                           highlightbackground=self.ACCENT if active else self.LINE)
            box.pack(fill="x", pady=(0, px(6)))
            row1 = tk.Frame(box, bg=self.CARD)
            row1.pack(fill="x", padx=px(14), pady=(px(7), 0))
            name = prof["name"] + ("（%s）" % prof["switch"] if prof.get("switch") else "")
            name_lab = tk.Label(row1, text=name, bg=self.CARD,
                                fg=self.ACCENT if active else self.FG,
                                font=(F, 10, "bold"), anchor="w", justify="left",
                                wraplength=px(280))
            name_lab.pack(side="left")
            tk.Label(row1, text="　%s　添加 %s" % ("● 使用中" if active else "",
                                                   prof.get("added", "-")),
                     bg=self.CARD, fg=self.MUTED, font=(F, 9)).pack(side="left")
            # 删除放最右，红字，点击后二次确认 + 强制等待 3 秒
            self._btn(row1, "删除", lambda p=prof: self.delete_profile(p),
                      danger=True).pack(side="right", padx=(0, px(6)))
            self._btn(row1, "编辑",
                      lambda p=prof: self.edit_profile(p)).pack(
                side="right", padx=(0, px(6)))
            if not active:
                self._btn(row1, "使用此档案",
                          lambda p=prof: self.switch_profile(p)).pack(side="right")
            used, since = self.profile_usage(prof)
            life = prof.get("life") or DEFAULT_LIFE
            parts, w_items = [], []
            for k in KEYS:
                if profile_hidden(prof, k):      # 侧键显隐同首页：隐藏的键不显示数值
                    continue
                pct = life_percent(used.get(k, 0), life.get(k, 0))
                if pct is not None:
                    name = "滚轮" if k == "wheel" else profile_label(prof, k)
                    parts.append("%s %.2f%%" % (name, pct))
                    w_items.append((used.get(k, 0), pct))
            agg = weighted_life_pct(w_items)
            color = self.FG if agg is not None else self.MUTED
            if agg is not None:
                parts.insert(0, "加权 %.2f%%" % agg)
            row2 = tk.Frame(box, bg=self.CARD)
            row2.pack(fill="x", padx=px(14), pady=(px(3), 0))
            tk.Label(row2, text="寿命使用量（自 %s 起）" % since.isoformat(),
                     bg=self.CARD, fg=self.MUTED, font=(F, 9)).pack(side="left")
            row3 = tk.Frame(box, bg=self.CARD)
            row3.pack(fill="x", padx=px(14), pady=(px(1), px(8)))
            tk.Label(row3, text="　".join(parts) or "—",
                     bg=self.CARD, fg=color, font=(F, 10), anchor="w", justify="left",
                     wraplength=px(480)).pack(side="left")

    def delete_profile(self, prof):
        """删除档案：二次确认，强制等待 3 秒后红色「确认删除」才可点。
        只删档案本身（名称/寿命/显隐设置），点击历史数据一律保留。"""
        F, px = self.FONT, self.px
        profs = ensure_profiles(self.stats)
        if len(profs) <= 1:
            messagebox.showwarning(APP_TITLE, "至少要保留一个鼠标档案。", parent=self.root)
            return
        dlg = tk.Toplevel(self.root)
        dlg.title("删除鼠标档案")
        dlg.configure(bg=self.BG)
        dlg.transient(self.root)
        dlg.resizable(False, False)
        box = self._card(dlg)
        box.pack(fill="both", expand=True, padx=px(12), pady=px(12))
        inner = tk.Frame(box, bg=self.CARD)
        inner.pack(fill="both", expand=True, padx=px(14), pady=px(12))
        tk.Label(inner, text="⚠️ 确认删除档案？", bg=self.CARD, fg=self.DANGER,
                 font=(F, 12, "bold")).pack(anchor="w")
        tk.Label(inner,
                 text="「%s」将从档案列表中删除（名称、微动、寿命上限、按键显隐）。\n"
                      "历史点击数据不会被删除；若删除的是当前档案，会自动切换到其它档案。"
                      % prof["name"],
                 bg=self.CARD, fg=self.FG, font=(F, 10), justify="left",
                 wraplength=px(440)).pack(anchor="w", pady=(px(8), 0))
        tip = tk.Label(inner, text="", bg=self.CARD, fg=self.MUTED, font=(F, 9))
        tip.pack(anchor="w", pady=(px(8), 0))

        btns = tk.Frame(dlg, bg=self.BG)
        btns.pack(pady=(0, px(14)))
        ok_btn = tk.Button(btns, text="确认删除", state="disabled", font=(F, 9),
                           bg=self.CARD, fg=self.DEL_FG, activebackground=self.BTN_HOVER,
                           relief="solid", bd=1, highlightthickness=0,
                           padx=px(12), pady=px(3))
        ok_btn.pack(side="left", padx=px(8))
        tk.Button(btns, text="取消", command=dlg.destroy, font=(F, 9),
                  bg=self.CARD, fg=self.FG, activebackground=self.BTN_HOVER,
                  relief="solid", bd=1, highlightthickness=0,
                  padx=px(12), pady=px(3)).pack(side="left")

        remain = [3.0]

        def do_delete():
            profs = self.stats.settings["profiles"]
            for i, p in enumerate(profs):
                if p["id"] == prof["id"]:
                    profs.pop(i)
                    break
            if self.stats.settings.get("profile_id") == prof["id"]:
                self.stats.settings["profile_id"] = profs[0]["id"]
            self.stats.select_profile(self.stats.settings["profile_id"])
            self.stats.dirty = True
            self.stats.save()
            dlg.destroy()
            self._load_key_settings_to_ui()
            self.refresh(force=True, life_now=True)
            self.render_profiles()

        def tick():
            if not dlg.winfo_exists():
                return
            remain[0] -= 0.1
            if remain[0] > 0:
                tip.configure(text="请仔细阅读警告，%.0f 秒后可确认删除" % remain[0])
                dlg.after(100, tick)
                return
            tip.configure(text="")
            ok_btn.configure(state="normal", fg=self.DANGER, cursor="hand2",
                            command=do_delete)

        self._center_dialog(dlg)
        dlg.grab_set()
        tick()

    def switch_profile_by_id(self, profile_id: str) -> None:
        """Switch profiles from a tray-menu command, tolerating stale menus."""
        prof = next((p for p in ensure_profiles(self.stats)
                     if p["id"] == profile_id), None)
        if prof is None:
            update_tray_menu_state(self.stats)
            return
        self.switch_profile(prof)

    def switch_profile(self, prof):
        self.stats.settings["profile_id"] = prof["id"]
        self.stats.select_profile(prof["id"])
        self.stats.dirty = True
        self.stats.save()
        self._load_key_settings_to_ui()
        self.refresh(force=True, life_now=True)
        self.render_profiles()

    def _profile_form(self, prof=None):
        """档案编辑/新建表单，返回 dict 或 None。
        各键额定寿命单独填写（单位：万次），留空 = 不统计该键寿命。"""
        editing = prof is not None
        prof = prof or {"name": "", "switch": "", "life": {},
                        "labels": {}, "hidden": [], "added": today().isoformat(),
                        "since": today().isoformat()}
        dlg = tk.Toplevel(self.root)
        dlg.title("编辑鼠标档案" if editing else "新建鼠标档案")
        dlg.configure(bg=self.BG)
        dlg.transient(self.root)
        dlg.resizable(False, False)
        F, px = self.FONT, self.px
        life = prof.get("life") or {}

        def card(title):
            box = self._card(dlg)
            box.pack(fill="x", padx=px(14), pady=(px(10), 0))
            self._section_head(box, title)
            grid = tk.Frame(box, bg=self.CARD)
            grid.pack(fill="x", padx=px(14), pady=(px(2), px(8)))
            return grid

        def field(grid, r, label, initial="", tip="", width=10, right=True):
            tk.Label(grid, text=label, bg=self.CARD, fg=self.FG,
                     font=(F, 9)).grid(row=r, column=0, sticky="w", pady=px(3))
            e = self._entry(grid, width=width,
                            justify="right" if right else "left")
            e.insert(0, initial)
            e.grid(row=r, column=1, sticky="w", padx=(px(8), 0), pady=px(3))
            if tip:
                tk.Label(grid, text=tip, bg=self.CARD, fg=self.MUTED,
                         font=(F, 8)).grid(row=r, column=2, sticky="w",
                                           padx=(px(8), 0))
            return e

        def wan_text(v):
            """次数 → 万次文本：20000000→'2000'；1500000→'150'；空/0→''"""
            try:
                v = int(v)
            except (TypeError, ValueError):
                return ""
            if v <= 0:
                return ""
            return ("%.3f" % (v / 10000.0)).rstrip("0").rstrip(".")

        g1 = card("基本信息")
        e_name = field(g1, 0, "鼠标名称 *", prof["name"], "必填",
                       width=15, right=False)
        e_switch = field(g1, 1, "微动型号", prof.get("switch") or "",
                         "选填，如：欧姆龙光微动", width=15, right=False)

        g2 = card("额定寿命（万次，选填；留空＝不统计该键）")
        e_keys = {}
        for i, (key, label) in enumerate(list(CLICK_BUTTONS) + [("wheel", "滚轮")]):
            tip = "上+下滚格数合计" if key == "wheel" else ""
            init = wan_text(life.get(key) if editing else DEFAULT_LIFE.get(key))
            e_keys[key] = field(g2, i, label + "（万次）", init, tip)
        result = {}

        def save():
            name = e_name.get().strip()
            if not name:
                messagebox.showwarning(APP_TITLE, "鼠标名称不能为空。", parent=dlg)
                return
            new_life = {}
            try:
                for k, e in e_keys.items():
                    t = e.get().strip().replace(",", "")
                    new_life[k] = int(float(t) * 10000) if t else 0
            except ValueError:
                messagebox.showwarning(APP_TITLE, "寿命请填写数字（单位：万次）。",
                                       parent=dlg)
                return
            result.update({"name": name[:24], "switch": e_switch.get().strip()[:24],
                           "life": new_life})
            dlg.destroy()

        btns = tk.Frame(dlg, bg=self.BG)
        btns.pack(pady=px(12))
        self._btn(btns, "保存", save).pack(side="left", padx=px(6))
        self._btn(btns, "取消", dlg.destroy).pack(side="left")
        dlg.bind("<Return>", lambda _e: save())
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        self._center_dialog(dlg)
        dlg.grab_set()
        self.root.wait_window(dlg)
        return result or None

    def new_profile(self):
        data = self._profile_form()
        if not data:
            return
        profs = ensure_profiles(self.stats)
        pid = "p%d" % (int(time.time() * 1000) % 100000000)
        prof = {
            "id": pid,
            "name": data["name"],
            "switch": data["switch"],
            "added": today().isoformat(),
            "since": today().isoformat(),
            "life": data["life"],
            "labels": {},
            "hidden": [],
        }
        profs.append(prof)
        self.stats.settings["profile_id"] = pid
        self.stats.select_profile(pid)
        self.stats.dirty = True
        self.stats.save()
        self._load_key_settings_to_ui()
        self.refresh(force=True, life_now=True)
        self.render_profiles()

    def edit_profile(self, prof):
        data = self._profile_form(prof)
        if not data:
            return
        prof["name"] = data["name"]
        prof["switch"] = data["switch"]
        prof["life"] = data["life"]
        self.stats.dirty = True
        self.stats.save()
        self.refresh(force=True, life_now=True)
        self.render_profiles()

    # ---------- 原有行为 ----------

    def hide(self):
        self.root.withdraw()
        if not self.hinted:
            self.hinted = True
            tray_modify(info="程序已在后台继续记录，单击右下角托盘图标可重新打开窗口。")

    def show(self):
        """把窗口叫到最前面（deiconify + 临时置顶，避免被其他窗口盖住看着像没反应）。"""
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.attributes("-topmost", True)
            self.root.after(500, lambda: self.root.attributes("-topmost", False))
        except Exception:
            pass
        self.root.focus_force()

    def update_tip(self):
        prof = current_profile(self.stats)
        self.tip_count = "%s｜今日 %s｜总计 %s" % (
            prof["name"], fmt(self.stats.click_sum(self.stats.day_row(today()))),
            fmt(self.stats.click_sum(self.stats.total)))
        tray_modify(tip="%s｜%s" % (APP_TITLE, self.tip_count))

    def _open_link(self, url):
        try:
            webbrowser.open(url)
        except Exception as exc:
            log("打开链接失败: %r" % (exc,))

    def toggle_startup(self):
        try:
            enabled = set_startup(self.autostart.get(), self.stats)
            self.autostart.set(enabled)
            if not enabled:
                self.status.configure(text="开机自启动已关闭", fg=self.MUTED)
            elif is_elevated():
                self.status.configure(text="开机自启动已开启（计划任务·最高权限·静默）", fg=self.MUTED)
                tray_modify(info="已开启开机自启动：登录时以管理员权限静默运行，不弹 UAC。")
            else:
                self.status.configure(text="开机自启动已开启（普通权限·游戏内可能不计数）", fg=self.MUTED)
                messagebox.showinfo(
                    APP_TITLE,
                    "已设置开机自启动，但当前是普通权限，开机后统计不到游戏内的点击。\n\n"
                    "请先点「以管理员身份重启」并同意 UAC，再勾选本项，\n"
                    "程序会改用「计划任务 + 最高权限」方式，开机静默运行且不弹 UAC。")
        except Exception as exc:
            self.autostart.set(startup_enabled())
            messagebox.showerror(APP_TITLE, "设置开机自启动失败：\n%r" % (exc,))

    def open_folder(self):
        """打开数据文件所在的文件夹（在用户「文档」里，不在程序目录，重装程序不会丢）。"""
        folder = APP_DIR if os.path.isdir(APP_DIR) else EXE_DIR
        try:
            os.startfile(folder)
        except Exception as exc:
            log("打开数据文件夹失败: %r" % (exc,))

    def restart_as_admin(self):
        """游戏（带内核反作弊）常以高权限运行，普通权限的钩子收不到它们的输入，
        这里提供一键提权重启用于验证/解决。"""
        if is_elevated():
            messagebox.showinfo(APP_TITLE, "当前已经是管理员权限运行，无需重启。")
            return
        self.stats.save(force=True)
        if relaunch_elevated():
            tray_remove()
            self.root.destroy()
        else:
            messagebox.showinfo(APP_TITLE, "已取消授权或提权失败，程序继续以普通权限运行。")

    def on_close(self):
        # 点右上角 X = 直接隐藏到后台（与「隐藏到后台」按钮一致），程序继续记录。
        self.hide()

    def quit_app(self):
        self.stats.save(force=True)
        tray_remove()
        self.root.destroy()


def acquire_single_instance():
    """返回 (本实例是否唯一, (kernel32, 事件句柄))。已存在实例时唤醒其窗口。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateEventW.restype = ctypes.c_void_p
    kernel32.CreateEventW.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.SetEvent.argtypes = [ctypes.c_void_p]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]

    ctypes.set_last_error(0)
    mutex = kernel32.CreateMutexW(None, 0, MUTEX_NAME)
    already = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    SINGLETON["mutex"] = mutex
    event = kernel32.CreateEventW(None, 0, 0, SHOW_EVENT_NAME)
    if already:
        kernel32.SetEvent(event)
        return False, None
    return True, (kernel32, event)


def watch_show_event(kernel32, event, sink):
    def worker():
        while True:
            kernel32.WaitForSingleObject(event, 0xFFFFFFFF)
            sink.put(("show", None))

    threading.Thread(target=worker, name="show-event", daemon=True).start()


def relaunch_elevated(extra=""):
    """以管理员身份重启自身；成功返回 True（调用方随后退出本实例）。"""
    tail = (" " + extra) if extra else ""
    args = ("--elevated" + tail) if FROZEN else '"%s" --elevated%s' % (
        os.path.join(EXE_DIR, "mouse_counter.py"), tail)
    handle = SINGLETON.get("mutex")
    if handle:
        try:
            kernel32_api().CloseHandle(ctypes.c_void_p(handle))
        except Exception:
            pass
        SINGLETON["mutex"] = None
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.ShellExecuteW.restype = ctypes.c_void_p
        shell32.ShellExecuteW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
            ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
        ret = shell32.ShellExecuteW(None, "runas", sys.executable, args, EXE_DIR, 1)
        if (ret or 0) > 32:
            tray_log("已请求以管理员身份重启")
            return True
        log("提权被取消或失败, 返回值=%s" % (ret,))
    except Exception as exc:
        log("提权重启失败: %r" % (exc,))
    # 失败/被取消 → 重新抢占单实例互斥体，继续以普通权限运行
    try:
        k = kernel32_api()
        k.CreateMutexW.restype = ctypes.c_void_p
        k.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        SINGLETON["mutex"] = k.CreateMutexW(None, 0, MUTEX_NAME)
    except Exception:
        pass
    return False


def dump():
    stats = Stats(DATA_FILE)
    t = today()
    today_row = stats.day_row(t)
    week7_row = stats.sum_range(t - datetime.timedelta(days=6), t)
    prof = current_profile(stats)
    print("app=%s version=%s date=%s profile=%s" %
          (APP_TITLE, APP_VERSION, t.isoformat(), prof["name"]))
    for key, label in BUTTONS:
        print("%s today=%d week7_avg=%.3f total=%d"
              % (label, today_row[key], week7_row[key] / 7.0, stats.total[key]))
    print("clicks today=%d week7_avg=%.3f total=%d days=%d startup=%s"
          % (stats.click_sum(today_row), stats.click_sum(week7_row) / 7.0,
             stats.click_sum(stats.total), stats.recorded_days(), startup_enabled()))


def main():
    enable_dpi_awareness()   # 必须在 tk.Tk() 之前声明，否则界面会被系统拉伸发虚
    args = [a.lower() for a in sys.argv[1:]]
    if "--dump" in args:
        dump()
        return
    if "--set-startup" in args:
        if not is_elevated() and "--elevated" not in args:
            if relaunch_elevated("--set-startup"):
                return  # 提权成功：由管理员实例去创建计划任务
        print("开机自启动：%s" % ("已开启" if set_startup(True, Stats(DATA_FILE)) else "设置失败"))
        return
    if "--clear-startup" in args:
        print("开机自启动：%s" % ("已关闭" if not set_startup(False, Stats(DATA_FILE)) else "关闭失败"))
        return

    unique, handle = acquire_single_instance()
    if not unique:
        return
    kernel32, event = handle

    silent = "--startup" in args  # 由开机自启动拉起：只驻留托盘，不弹窗口

    # 手动启动时自动申请管理员权限：游戏（内核反作弊）以高权限运行，
    # 普通权限的低级钩子收不到它们的输入。带 --elevated 的一次性进程不再递归提权。
    if not silent and "--elevated" not in args and not is_elevated():
        if relaunch_elevated():
            return

    tray_log("程序启动 v%s pid=%s frozen=%s silent=%s cwd=%s"
             % (APP_VERSION, os.getpid(), FROZEN, silent, os.getcwd()))
    sink = queue.Queue()
    stats = Stats(DATA_FILE)
    synced = sync_autostart(stats)
    if silent and not synced and not stats.get_setting("autostart_choice"):
        # 开机首次自启（从未手动开/关过）→ 默认帮用户开启，避免安装者找不到入口
        if set_startup(True, stats):
            tray_log("首次开机自启：默认开启自启动")
    root = tk.Tk()
    if silent:
        root.withdraw()
    app = App(root, stats, sink, silent=silent)
    watch_show_event(kernel32, event, sink)
    start_worker(sink)
    try:
        root.mainloop()
    finally:
        stats.save(force=True)


if __name__ == "__main__":
    main()

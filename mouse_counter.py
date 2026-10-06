# -*- coding: utf-8 -*-
"""鼠标点击次数记录

纯 ctypes 调用 Windows 底层鼠标钩子(WH_MOUSE_LL)，无第三方依赖。
记录 左键 / 右键 / 中键 / 侧键1 / 侧键2 的每日点击次数，另记滚轮格数（上/下合并，不计入点击总数）。
统计当天、近7天日均、近七天每日与总计（亿级缩写显示），支持鼠标档案与寿命预测、
按键显示管理、自动+手动备份、开机自启动，单实例运行。
v2.0.0：顶部快捷栏（首页/设置/鼠标档案/关于）+ 鼠标档案寿命预测 + 滚轮计数 + 亿级缩写 + 备份体系。
v2.1.0：托盘右键菜单支持切换鼠标档案，菜单定位到鼠标指针右上方。
数据/配置统一放在用户「文档\\鼠标点击次数记录」，文件名 鼠标点击记录（配置文件，勿删）.json。
"""

import ctypes
import datetime
import json
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

APP_TITLE = "鼠标点击次数记录"
APP_VERSION = "2.2.0"
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
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wt.DWORD,
                                                 ctypes.c_void_p,
                                                 ctypes.POINTER(ctypes.c_wchar_p)]
        buf = ctypes.c_wchar_p()
        if shell32.SHGetKnownFolderPath(ctypes.byref(docs_guid), 0, None,
                                        ctypes.byref(buf)) == 0 and buf.value:
            path = buf.value
            try:
                ctypes.windll.ole32.CoTaskMemFree(buf)
            except Exception:
                pass
            return path
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


TRAY = {"hwnd": None, "ready": False}
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
        self.apply_payload(raw)

    @staticmethod
    def _normalize_days(days: dict) -> dict:
        """Convert one profile's date map to the current row shape."""
        normalized = {}
        for day, counts in (days or {}).items():
            if not isinstance(counts, dict):
                continue
            row = {k: int(counts.get(k, 0) or 0) for k in KEYS}
            if "wheel" not in counts:
                row["wheel"] = (int(counts.get("wheel_up", 0) or 0)
                                + int(counts.get("wheel_down", 0) or 0))
            normalized[str(day)] = row
        return normalized

    def select_profile(self, profile_id: str) -> dict:
        """Point day and total helpers at one profile's independent bucket."""
        if not isinstance(self.profile_days, dict):
            self.profile_days = {}
        days = self.profile_days.setdefault(profile_id, {})
        self._active_profile_id = profile_id
        self.days = days
        self.total = {k: 0 for k in KEYS}
        for row in days.values():
            for key in KEYS:
                self.total[key] += int(row.get(key, 0) or 0)
        return days

    def apply_payload(self, raw: dict) -> None:
        """Load v3 per-profile days, migrating the old shared bucket to the active profile."""
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
                    or int(raw.get("version", 1) or 1) < DATA_VERSION)
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
        name = "手动备份_%s.json" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = os.path.join(self.backup_dir(), name)
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(self.payload(), f, ensure_ascii=False, separators=(",", ":"))
        return dest

    def list_backups(self):
        d = self.backup_dir()
        try:
            names = [x for x in os.listdir(d) if x.endswith(".json")]
        except OSError:
            names = []
        return d, sorted(names, reverse=True)

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
        return self.days.get(date.isoformat()) or {k: 0 for k in KEYS}

    def sum_range_for(self, profile_id: str, start: datetime.date, end: datetime.date) -> dict:
        """Sum one profile's independent day bucket over a date range."""
        days = self.profile_days.get(profile_id) or {}
        res = {k: 0 for k in KEYS}
        day = start
        while day <= end:
            row = days.get(day.isoformat())
            if row:
                for k in KEYS:
                    res[k] += row.get(k, 0)
            day += datetime.timedelta(days=1)
        return res

    def sum_range(self, start, end):
        return self.sum_range_for(self._active_profile_id, start, end)

    def click_sum(self, row):
        """点击口径合计：只算 5 个点击键，滚轮不计入。"""
        return sum(row.get(k, 0) for k in CLICK_KEYS)

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
_WNDPROC_REFS = []


def user32_api():
    global _USER32
    if _USER32 is not None:
        return _USER32
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.LoadImageW.restype = ctypes.c_void_p
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
    """图标优先取 数据目录\\icon.ico 与 config\\icon.ico，其次 exe 同级，最后扫一级子目录。"""
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


def create_tray_window(u, sink):
    """在消息线程里建一个隐藏窗口，用于接收托盘图标的鼠标消息。"""
    wndproc_type = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_void_p, wt.UINT, WPARAM, LPARAM)

    def wnd_proc(hwnd, msg, wparam, lparam):
        # 任何异常都不能冒泡出去，否则整个消息线程（含鼠标钩子）会静默退出
        try:
            if msg == WM_TRAY:
                # 三种格式都要兼容：
                #   V1/本机实测 : wParam=图标ID,      lParam=鼠标消息
                #   V2/V3       : wParam=鼠标消息,    lParam=(x,y)
                #   V4          : wParam=通知码+ID,   lParam=(x,y)
                codes = (wparam & 0xFFFF, lparam & 0xFFFF)
                if set(codes) & TRAY_LEFT_CODES:
                    tray_log("托盘左键 wParam=0x%04X lParam=0x%04X" % codes)
                    sink.put(("show", None))
                elif set(codes) & TRAY_RIGHT_CODES:
                    tray_log("托盘右键 wParam=0x%04X lParam=0x%04X" % codes)
                    # 必须在拥有该窗口的线程里调用，否则 TrackPopupMenu 会直接返回、菜单不显示；
                    # 菜单自带的消息循环照常分发，低级鼠标钩子仍会被回调
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
            return u.DefWindowProcW(hwnd, msg, wparam, lparam)
        except Exception as exc:
            log("托盘消息处理异常: %r" % (exc,))
            return 0

    proc = wndproc_type(wnd_proc)
    _WNDPROC_REFS.append(proc)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    hinst = kernel32.GetModuleHandleW(None)
    class_name = "MouseClickCounterTrayWindow"
    wc = WNDCLASSW()
    wc.lpfnWndProc = ctypes.cast(proc, ctypes.c_void_p)
    wc.hInstance = hinst
    wc.lpszClassName = class_name
    if not u.RegisterClassW(ctypes.byref(wc)) and ctypes.get_last_error() != 1410:
        log("注册托盘窗口类失败: %s" % ctypes.get_last_error())
    hwnd = u.CreateWindowExW(0, class_name, APP_TITLE, 0, 0, 0, 0, 0,
                             None, None, hinst, None)
    if not hwnd:
        log("创建托盘窗口失败: %s" % ctypes.get_last_error())
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
                if key:
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

    tray_hwnd = create_tray_window(user32, sink)
    if tray_hwnd:
        add_tray_icon(user32, tray_hwnd)

    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        try:
            if msg.message == WM_HOOK_REINSTALL:
                reinstall_hook(user32)
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
    """调用 schtasks 等命令行工具，不弹控制台窗口。"""
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=20,
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


def task_xml():
    command, workdir = task_run_command()
    if not FROZEN:
        # 脚本模式：Command 用 pythonw.exe，脚本路径放进 Arguments
        script = os.path.join(EXE_DIR, "mouse_counter.py")
        body = TASK_XML_TEMPLATE.replace("<Arguments>--startup</Arguments>",
                                         "<Arguments>\"%s\" --startup</Arguments>" % script)
    else:
        body = TASK_XML_TEMPLATE
    return body.format(user=task_user_id(), command=command, workdir=workdir)


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
    BG = "#f5f6f8"
    CARD = "#ffffff"
    FG = "#1f2329"
    MUTED = "#6b7280"
    ACCENT = "#2563eb"
    DANGER = "#b91c1c"
    WARN = "#b45309"
    WARN_BG = "#fef3c7"
    LINE = "#e3e5e9"
    STRIPE = "#fafbfc"
    BAR = "#9dbcfa"
    BAR_BG = "#eef1f5"
    FONT = default_font()
    PAGES = ("home", "settings", "profiles", "about")
    PAGE_NAMES = {"home": "首页", "settings": "设置", "profiles": "鼠标档案", "about": "关于"}

    def __init__(self, root, stats, sink, silent=False):
        self.root = root
        self.stats = stats
        self.sink = sink
        self.cells = {}
        self.row_widgets = {}
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
        self._build()
        self.refresh(force=True)
        self.root.after(400, self.poll)
        if not silent and not stats.get_setting("disclaimer_ok"):
            self.root.after(300, self.show_disclaimer)

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
            ttk.Style().theme_use("vista")
        except Exception:
            pass

        root.bind("<Escape>", lambda _e: self.hide())
        F = self.FONT
        self.kpi = {}
        self.kpi_full = {}
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

        # --- 顶部：标题行 + 快捷栏 ---
        head = tk.Frame(root, bg=self.BG)
        head.pack(fill="x", padx=m, pady=(px(14), px(6)))
        title_row = tk.Frame(head, bg=self.BG)
        title_row.pack(fill="x")
        tk.Label(title_row, text=APP_TITLE, bg=self.BG, fg=self.FG,
                 font=(F, 15, "bold")).pack(side="left")

        nav = tk.Frame(head, bg=self.BG)
        nav.pack(anchor="w", pady=(px(8), 0))
        for name in self.PAGES:
            b = tk.Label(nav, text=self.PAGE_NAMES[name], font=(F, 10, "bold"),
                         bg=self.BG, fg=self.MUTED, padx=px(14), pady=px(5),
                         cursor="hand2")
            b.pack(side="left", padx=(0 if name == "home" else px(6), 0))
            b.bind("<Button-1>", lambda _e, n=name: self.show_page(n))
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
        # 先把两个警告黄框按"齐全"基线摆上：冻结高度后，警告显隐不再让窗口变高变低
        self.warn_box.pack(anchor="w", pady=(px(6), 0),
                           in_=self.pages["home"], before=self.home_buttons_frame)
        self.autostart_box.pack(anchor="w", pady=(px(6), 0),
                                 in_=self.pages["home"], before=self.home_buttons_frame)
        root.update_idletasks()
        # 记录除滚动内容外的固定高度（首次滚动 Canvas 高度≈0）
        self._chrome_h = max(px(120), root.winfo_reqheight())
        self.show_page("home")
        root.minsize(px(500), px(300))

    def _card(self, parent):
        return tk.Frame(parent, bg=self.CARD, highlightthickness=1,
                        highlightbackground=self.LINE)

    def _btn(self, parent, text, cmd, danger=False):
        return tk.Button(parent, text=text, command=cmd, font=(self.FONT, 9),
                         bg=self.CARD, fg=self.DANGER if danger else self.FG,
                         activebackground="#eef1f5",
                         activeforeground=self.DANGER if danger else self.FG,
                         relief="solid", bd=1, highlightthickness=0,
                         padx=self.px(10), pady=self.px(3), cursor="hand2")

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
        for col, (key, caption) in enumerate((("today", "今日点击"),
                                              ("avg7", "近7天日均"),
                                              ("total", "总点击次数"))):
            kpi.columnconfigure(col, weight=1, uniform="kpi")
            box = self._card(kpi)
            box.grid(row=0, column=col, sticky="nsew",
                     padx=(0 if col == 0 else px(8), 0))
            value = tk.Label(box, text="-", bg=self.CARD,
                             fg=self.FG if key == "total" else self.ACCENT,
                             font=(F, 15 if self.compact else 17, "bold"))
            value.pack(anchor="w", padx=kpi_px, pady=(self._kpi_pad, 0))
            tk.Label(box, text=caption, bg=self.CARD, fg=self.MUTED,
                     font=(F, 9)).pack(anchor="w", padx=kpi_px, pady=(0, self._kpi_pad))
            self.kpi[key] = value
            value.bind("<Enter>", lambda _e, k=key: self._kpi_hover(k, True))
            value.bind("<Leave>", lambda _e, k=key: self._kpi_hover(k, False))

        card = self._card(page)
        card.pack(fill="x", pady=(px(10), 0))
        self.table_card = card
        headers = ["按键", "寿命", "今日", "近7天日均", "总计"]
        self.table_headers = headers
        self.life_cells = {}
        for col, text in enumerate(headers):
            card.columnconfigure(col, weight=2 if col == 0 else 1)
            tk.Label(card, text=text, bg=self.CARD, fg=self.MUTED, font=(F, 9),
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

        week = self._card(page)
        week.pack(fill="x", pady=(px(10), 0))
        week_head = tk.Frame(week, bg=self.CARD)
        week_head.pack(fill="x", padx=kpi_px, pady=(px(9), px(2)))
        tk.Label(week_head, text="近七天每日点击", bg=self.CARD, fg=self.FG,
                 font=(F, 10, "bold")).pack(side="left")
        self.week_note = tk.Label(week_head, text="", bg=self.CARD, fg=self.MUTED,
                                  font=(F, 9))
        self.week_note.pack(side="right")
        self.canvas = tk.Canvas(week, bg=self.CARD, height=7 * self.row_h + px(4),
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="x", padx=px(10), pady=(px(2), px(8)))
        self.canvas.bind("<Configure>", lambda _e: self.draw_bars())

        # 权限提示黄框（仅普通权限时显示）
        self.warn_box = tk.Frame(page, bg=self.WARN_BG, highlightthickness=1,
                                 highlightbackground="#fcd34d")
        tk.Label(self.warn_box, text="⚠️ 未开启管理员权限，可能无法记录游戏内点击",
                 bg=self.WARN_BG, fg=self.WARN, font=(F, 9)).pack(
            anchor="w", padx=px(10), pady=px(4))

        # 自启动提示黄框（仅未开启自启动时显示，点击去设置页开启）
        self.autostart_box = tk.Frame(page, bg=self.WARN_BG, highlightthickness=1,
                                      highlightbackground="#fcd34d", cursor="hand2")
        tk.Label(self.autostart_box,
                 text="⚠️ 未开启开机自启动，重启后不会自动记录 —— 点此去「设置」开启",
                 bg=self.WARN_BG, fg=self.WARN, font=(F, 9),
                 cursor="hand2").pack(anchor="w", padx=px(10), pady=px(4))
        self.autostart_box.bind("<Button-1>", lambda _e: self.show_page("settings"))
        for _w in self.autostart_box.winfo_children():
            _w.bind("<Button-1>", lambda _e: self.show_page("settings"))

        btns = tk.Frame(page, bg=self.BG)
        btns.pack(fill="x", pady=(px(10), px(12)))
        self.btn_admin = tk.Button(btns, text="以管理员身份重启", command=self.restart_as_admin,
                                   font=(F, 9), bg=self.CARD, fg=self.FG,
                                   activebackground="#eef1f5", relief="solid", bd=1,
                                   highlightthickness=0, padx=px(10), pady=px(4),
                                   cursor="hand2")
        self.btn_admin.pack(side="left", padx=(0, px(8)))
        self.warn_anchor = btns
        self.home_buttons_frame = btns
        for text, cmd, danger in (("隐藏到后台", self.hide, False),
                                  ("退出程序", self.quit_app, True)):
            tk.Button(btns, text=text, command=cmd, font=(F, 9),
                      bg=self.CARD, fg=self.DANGER if danger else self.FG,
                      activebackground="#eef1f5",
                      activeforeground=self.DANGER if danger else self.FG,
                      relief="solid", bd=1, highlightthickness=0,
                      padx=px(10), pady=px(4),
                      cursor="hand2").pack(side="left", padx=(0, px(8)))
        return page

    def _make_table_row(self, card, i, key):
        F = self.FONT
        px = self.px
        pad = self.pad
        bold = key == "__total__"
        bg = "#eff5ff" if bold else (self.STRIPE if i % 2 else self.CARD)
        font = (F, 10, "bold" if bold else "normal")
        fg = self.ACCENT if bold else self.FG
        widgets = []
        lab = tk.Label(card, text="", bg=bg, fg=fg, font=font,
                       padx=px(14), pady=pad, anchor="w")
        widgets.append(lab)
        lab.grid(row=i + 2, column=0, sticky="we")
        life = tk.Label(card, text="", bg=bg, fg=fg, font=(F, 10, "bold" if bold else "normal"),
                        padx=px(6), pady=pad, anchor="w")
        life.grid(row=i + 2, column=1, sticky="we")
        self.life_cells[key] = life
        widgets.append(life)
        for col in range(2, len(self.table_headers)):
            lab = tk.Label(card, text="-", bg=bg, fg=fg, font=font,
                           padx=px(14), pady=pad, anchor="e")
            lab.grid(row=i + 2, column=col, sticky="we")
            self.cells[(key, col)] = lab
            widgets.append(lab)
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
                chk = tk.Checkbutton(row, text="显示", variable=var,
                                     bg=row["bg"], activebackground=row["bg"],
                                     font=(F, 9), command=self.apply_key_settings)
                chk.pack(side="left", pady=px(3))
            else:
                tk.Label(row, text="固定显示", bg=row["bg"], fg=self.MUTED,
                         font=(F, 9)).pack(side="left", padx=(px(2), 0), pady=px(3))
            entry = tk.Entry(row, font=(F, 9), width=12, relief="solid", bd=1,
                             highlightthickness=0)
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
        ttk.Checkbutton(row, text="登录后台静默运行（管理员权限可统计游戏内点击）",
                        variable=self.autostart,
                        command=self.toggle_startup).pack(side="left")
        # 未勾选时的 ⚠️ 放在贴近右侧、留约 4 个字符空隙，避免遮挡文字也不贴边
        self.autostart_warn = tk.Label(row, text="", bg=self.CARD, fg=self.WARN,
                                       font=(F, 12))
        self.autostart_warn.pack(side="right", padx=(0, px(44)))

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
        if enter:
            if key in self.kpi_full:
                self.kpi[key].configure(text=self.kpi_full[key])
        else:
            self.refresh(force=True)

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
        if thread is not None and thread.is_alive() and is_tray_window_alive():
            self.check_hook_blocked()
            return
        tray_log("看门狗：后台线程存活=%s 托盘窗口存活=%s，准备重启"
                 % (thread is not None and thread.is_alive(), is_tray_window_alive()))
        TRAY["hwnd"] = None
        TRAY["ready"] = False
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
        parts.append("管理员权限" if is_elevated() else "普通权限")
        if AUTOSTART["mode"] == "task":
            parts.append("自启动:计划任务(最高权限)")
        elif AUTOSTART["mode"] == "run":
            parts.append("自启动:注册表")
        if self.hook_warning:
            parts.append("⚠ 钩子疑似被拦截，游戏内可能不计数")
        self.status.configure(text=" · ".join(parts),
                              fg=self.DANGER if self.hook_warning else self.MUTED)
        # 权限黄框：非管理员显示在「以管理员身份重启」按钮下方
        show_warn = not is_elevated()
        if show_warn and not self.warn_box.winfo_ismapped():
            self.warn_box.pack(anchor="w", pady=(self.px(6), 0),
                               in_=self.pages["home"], before=self.home_buttons_frame)
        elif not show_warn and self.warn_box.winfo_ismapped():
            self.warn_box.pack_forget()
        self.autostart_warn.configure(
            text="⚠️" if not self.autostart.get() else "")
        # 自启动黄框：未开启时挂在首页权限黄框下方，开启后隐藏
        auto_on = AUTOSTART["mode"] in ("task", "run") or self.autostart.get()
        if not auto_on and not self.autostart_box.winfo_ismapped():
            self.autostart_box.pack(anchor="w", pady=(self.px(6), 0),
                                    in_=self.pages["home"],
                                    before=self.home_buttons_frame)
        elif auto_on and self.autostart_box.winfo_ismapped():
            self.autostart_box.pack_forget()

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
        self.days7 = [t - datetime.timedelta(days=i) for i in range(6, -1, -1)]
        self.rows7 = [stats.day_row(d) for d in self.days7]
        today_row = self.rows7[-1]
        sum7 = {k: sum(row[k] for row in self.rows7) for k in KEYS}
        avg7 = {k: sum7[k] / 7.0 for k in KEYS}
        avg7_all = stats.click_sum(sum7) / 7.0
        total = dict(stats.total)
        total_all = stats.click_sum(total)

        def kpi_set(key, short, full):
            lab = self.kpi[key]
            if self.page == "home":
                lab.configure(text=short)
            self.kpi_full[key] = full

        kpi_set("today", fmt(stats.click_sum(today_row)),
                fmt_full(stats.click_sum(today_row)))
        kpi_set("avg7", "%.1f" % avg7_all, "%.1f" % avg7_all)
        kpi_set("total", fmt(total_all), fmt_full(total_all) + " 次")

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
        self.week_note.configure(text="日均 %.1f 次" % avg7_all)
        self.draw_bars()

    def _refresh_life_cells(self, prof: dict, vis: list[str]) -> None:
        """Refresh the life column from the profile's independent history."""
        used_p, _since = self.profile_usage(prof)
        life_lim = prof.get("life") or DEFAULT_LIFE
        w_items = []
        for key in KEYS:
            lab = self.life_cells.get(key)
            if lab is None or key not in vis:
                continue
            pct = life_percent(used_p.get(key, 0), life_lim.get(key, 0))
            if pct is None:
                lab.configure(text="—", fg=self.MUTED)
            else:
                w_items.append((used_p.get(key, 0), pct))
                lab.configure(text="%.2f%%" % pct, fg=life_color(pct))
        lab = self.life_cells.get("__total__")
        if lab is not None:
            agg = weighted_life_pct(w_items)
            if agg is None:
                lab.configure(text="—", fg=self.MUTED)
            else:
                lab.configure(text="%.2f%%" % agg, fg=life_color(agg))

    def profile_usage(self, prof):
        """档案用量：从 since 日起累计该档案独立的点击键与滚轮。"""
        try:
            since = datetime.date.fromisoformat(prof.get("since") or today().isoformat())
        except ValueError:
            since = today()
        used = self.stats.sum_range_for(prof["id"], since, today())
        return used, since

    def draw_bars(self):
        """近七天每日点击量条形图，今天用强调色高亮。"""
        canvas = getattr(self, "canvas", None)
        days7 = getattr(self, "days7", None)
        rows7 = getattr(self, "rows7", None)
        if canvas is None or not days7 or not rows7:
            return
        F = self.FONT
        px = getattr(self, "px", lambda v: v)
        canvas.delete("all")
        width = canvas.winfo_width()
        if width < px(80):
            width = px(500)
        label_w, val_w = px(96), px(66)
        x0 = label_w
        bar_max = max(px(40), width - label_w - val_w - px(8))
        counts = [self.stats.click_sum(row) for row in rows7]
        peak = max(counts) or 1
        t = today()
        row_h = getattr(self, "row_h", px(23))
        for i, (day, cnt) in enumerate(zip(days7, counts)):
            y = i * row_h + px(2)
            top = y + px(4)
            bottom = y + row_h - px(4)
            cy = (top + bottom) // 2
            is_today = day == t
            fg = self.ACCENT if is_today else self.FG
            canvas.create_text(px(2), cy, anchor="w", font=(F, 8),
                               fill=fg if is_today else self.MUTED,
                               text="%s 周%s" % (day.strftime("%m-%d"),
                                                 "一二三四五六日"[day.weekday()]))
            canvas.create_rectangle(x0, top, x0 + bar_max, bottom,
                                    fill=self.BAR_BG, outline="")
            if cnt:
                bar = max(px(3), int(bar_max * cnt / peak))
                canvas.create_rectangle(x0, top, x0 + bar, bottom,
                                        fill=self.ACCENT if is_today else self.BAR,
                                        outline="")
            canvas.create_text(width - px(2), cy, anchor="e", font=(F, 8),
                               fill=fg, text=fmt(cnt))

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
        box = ttk.Combobox(dlg, values=names, state="readonly", width=44)
        box.set(names[0])
        box.pack(padx=px(12))

        def do_restore():
            name = box.get()
            if not name:
                return
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
        tk.Button(btns, text="恢复", command=do_restore, font=(F, 9), relief="solid", bd=1,
                  padx=px(10), pady=px(3)).pack(side="left", padx=px(6))
        tk.Button(btns, text="取消", command=dlg.destroy, font=(F, 9), relief="solid", bd=1,
                  padx=px(10), pady=px(3)).pack(side="left")
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
        entry = tk.Entry(dlg, font=(F, 9), width=52, relief="solid", bd=1)
        entry.insert(0, initial)
        entry.pack(padx=px(12))
        entry.focus_set()
        dlg.result = None

        def ok(_e=None):
            dlg.result = entry.get()
            dlg.destroy()

        btns = tk.Frame(dlg, bg=self.BG)
        btns.pack(pady=px(10))
        tk.Button(btns, text="确定", command=ok, font=(F, 9), relief="solid", bd=1,
                  padx=px(10), pady=px(3)).pack(side="left", padx=px(6))
        tk.Button(btns, text="取消", command=lambda: (setattr(dlg, "result", None),
                                                      dlg.destroy()),
                  font=(F, 9), relief="solid", bd=1, padx=px(10),
                  pady=px(3)).pack(side="left")
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
            color = life_color(agg) if agg is not None else self.MUTED
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
                           bg=self.CARD, fg="#d4a0a0", activebackground="#eef1f5",
                           relief="solid", bd=1, highlightthickness=0,
                           padx=px(12), pady=px(3))
        ok_btn.pack(side="left", padx=px(8))
        tk.Button(btns, text="取消", command=dlg.destroy, font=(F, 9),
                  bg=self.CARD, fg=self.FG, activebackground="#eef1f5",
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
            e = tk.Entry(grid, font=(F, 9), width=width, relief="solid", bd=1,
                         highlightthickness=0,
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

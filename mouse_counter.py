# -*- coding: utf-8 -*-
"""鼠标点击次数记录

纯 ctypes 调用 Windows 底层鼠标钩子(WH_MOUSE_LL)，无第三方依赖。
记录 左键 / 右键 / 中键 / 侧键1 / 侧键2 的每日点击次数，统计当天、本周日均、
近7天日均与总计，支持开机自启动，单实例运行。
"""

import ctypes
import datetime
import json
import os
import queue
import sys
import threading
import tkinter as tk
from ctypes import wintypes as wt
from tkinter import messagebox, ttk

APP_TITLE = "鼠标点击次数记录"
BYLINE = "by 黎修源"
FROZEN = getattr(sys, "frozen", False)  # 是否由 PyInstaller 打包成 exe 运行
APP_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))
DATA_NAME = "点击数据记录（勿删！！！）.json"
LEGACY_DATA_NAMES = ("data.json",)  # 旧版本文件名，存在时自动兼容读取
DATA_FILE = os.path.join(APP_DIR, DATA_NAME)
LOG_FILE = os.path.join(APP_DIR, "error.log")
TRAY_LOG = os.path.join(APP_DIR, "tray.log")  # 托盘事件诊断日志（可删）

MUTEX_NAME = "MouseClickCounter_SingleInstance_v1"
SHOW_EVENT_NAME = "MouseClickCounter_ShowWindow_v1"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "MouseClickCounter"

BUTTONS = (
    ("left", "左键"),
    ("right", "右键"),
    ("middle", "中键"),
    ("x1", "侧键1"),
    ("x2", "侧键2"),
)
KEYS = [k for k, _ in BUTTONS]

WH_MOUSE_LL = 14
HC_ACTION = 0
WM_LBUTTONDOWN = 0x0201
WM_RBUTTONDOWN = 0x0204
WM_MBUTTONDOWN = 0x0207
WM_XBUTTONDOWN = 0x020B
ERROR_ALREADY_EXISTS = 183
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
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
MF_STRING, MF_CHECKED, MF_SEPARATOR = 0x0, 0x8, 0x800
TPM_RETURNCMD, TPM_RIGHTBUTTON = 0x0100, 0x0002
IDM_SHOW, IDM_STARTUP, IDM_EXIT = 1001, 1002, 1003
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


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wt.POINT),
        ("mouseData", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


def log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
        if os.path.getsize(LOG_FILE) > 200000:
            os.remove(LOG_FILE)
    except Exception:
        pass


def tray_log(msg):
    """托盘相关事件记到 tray.log，出问题时可以直接看这个文件。"""
    try:
        with open(TRAY_LOG, "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
        if os.path.getsize(TRAY_LOG) > 65536:
            os.remove(TRAY_LOG)
    except Exception:
        pass


def today():
    return datetime.date.today()


class Stats:
    """按天保存的点击数据，只在界面线程里读写。"""

    def __init__(self, path):
        self.path = path
        self.days = {}
        self.total = {k: 0 for k in KEYS}
        self.dirty = False
        self._load()

    def _load(self):
        path = self.path
        if not os.path.exists(path):
            for legacy in LEGACY_DATA_NAMES:
                candidate = os.path.join(APP_DIR, legacy)
                if os.path.exists(candidate):
                    path = candidate
                    log("从旧文件名 %s 读取数据，下次保存会自动写成 %s" % (legacy, DATA_NAME))
                    break
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
        for day, counts in (raw.get("days") or {}).items():
            row = {k: int(counts.get(k, 0) or 0) for k in KEYS}
            self.days[day] = row
            for k in KEYS:
                self.total[k] += row[k]

    def add(self, key, date=None):
        day = (date or today()).isoformat()
        row = self.days.get(day)
        if row is None:
            row = self.days[day] = {k: 0 for k in KEYS}
        row[key] = row.get(key, 0) + 1
        self.total[key] = self.total.get(key, 0) + 1
        self.dirty = True

    def save(self, force=False):
        if not self.dirty and not force:
            return
        payload = {
            "app": APP_TITLE,
            "version": 1,
            "updated": datetime.datetime.now().isoformat(timespec="seconds"),
            "days": self.days,
        }
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, self.path)
            self.dirty = False
        except Exception as exc:
            log("数据保存失败: %r" % (exc,))

    def day_row(self, date):
        return self.days.get(date.isoformat()) or {k: 0 for k in KEYS}

    def sum_range(self, start, end):
        res = {k: 0 for k in KEYS}
        day = start
        while day <= end:
            row = self.days.get(day.isoformat())
            if row:
                for k in KEYS:
                    res[k] += row.get(k, 0)
            day += datetime.timedelta(days=1)
        return res

    def recorded_days(self):
        return sum(1 for row in self.days.values() if sum(row.values()) > 0)


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
    _USER32 = u
    return u


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
    """图标可以在 exe 同级，也可以放在任意一级子文件夹里（如 其他文件\\icon.ico）。"""
    direct = os.path.join(APP_DIR, "icon.ico")
    if os.path.exists(direct):
        return direct
    try:
        for entry in sorted(os.listdir(APP_DIR)):
            sub = os.path.join(APP_DIR, entry)
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


def show_tray_menu(u, hwnd, sink):
    menu = u.CreatePopupMenu()
    u.AppendMenuW(menu, MF_STRING, IDM_SHOW, "控制面板")
    u.AppendMenuW(menu, MF_SEPARATOR, 0, None)
    u.AppendMenuW(menu, MF_STRING, IDM_EXIT, "关闭程序")
    point = wt.POINT()
    u.GetCursorPos(ctypes.byref(point))
    u.SetForegroundWindow(hwnd)
    cmd = u.TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON,
                           point.x, point.y, 0, hwnd, None)
    u.PostMessageW(hwnd, WM_NULL, 0, 0)
    u.DestroyMenu(menu)
    tray_log("菜单选择 cmd=%s" % cmd)
    if cmd == IDM_SHOW:
        sink.put(("show", None))
    elif cmd == IDM_STARTUP:
        sink.put(("toggle-startup", None))
    elif cmd == IDM_EXIT:
        sink.put(("quit", None))


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
        if n_code == HC_ACTION:
            try:
                key = None
                if w_param == WM_LBUTTONDOWN:
                    key = "left"
                elif w_param == WM_RBUTTONDOWN:
                    key = "right"
                elif w_param == WM_MBUTTONDOWN:
                    key = "middle"
                elif w_param == WM_XBUTTONDOWN:
                    info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    xbutton = (info.mouseData >> 16) & 0xFFFF
                    key = "x1" if xbutton == 1 else ("x2" if xbutton == 2 else None)
                if key:
                    sink.put(key)
            except Exception as exc:
                log("钩子回调异常: %r" % (exc,))
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    hook_proc = hookproc_type(callback)  # 保留引用，防止被回收导致钩子失效
    handle = user32.SetWindowsHookExW(WH_MOUSE_LL, hook_proc, None, 0)
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
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:
            log("窗口消息处理异常: %r" % (exc,))
    tray_log("消息循环结束（线程即将退出）")


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
    if FROZEN:
        return '"%s"' % sys.executable
    return '"%s" "%s"' % (pythonw_path(), os.path.join(APP_DIR, "mouse_counter.py"))


def startup_enabled():
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
            return bool(value)
    except FileNotFoundError:
        return False
    except Exception as exc:
        log("读取开机自启动状态失败: %r" % (exc,))
        return False


def set_startup(enable):
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enable:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass
    return startup_enabled()


class App:
    BG = "#f5f6f8"
    CARD = "#ffffff"
    FG = "#1f2329"
    MUTED = "#6b7280"
    ACCENT = "#2563eb"
    DANGER = "#b91c1c"

    def __init__(self, root, stats, sink):
        self.root = root
        self.stats = stats
        self.sink = sink
        self.cells = {}
        self.last_save = 0.0
        self.last_tip = 0.0
        self.last_health = 0.0
        self.hinted = False
        self._build()
        self.refresh(force=True)
        self.root.after(400, self.poll)

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

        head = tk.Frame(root, bg=self.BG)
        head.pack(fill="x", padx=16, pady=(14, 6))
        title_row = tk.Frame(head, bg=self.BG)
        title_row.pack(fill="x")
        tk.Label(title_row, text=APP_TITLE, bg=self.BG, fg=self.FG,
                 font=("Microsoft YaHei UI", 15, "bold")).pack(side="left")
        tk.Label(title_row, text=BYLINE, bg=self.BG, fg=self.MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="right", pady=(8, 0))
        self.date_label = tk.Label(head, text="", bg=self.BG, fg=self.MUTED,
                                   font=("Microsoft YaHei UI", 9))
        self.date_label.pack(anchor="w", pady=(2, 0))

        card = tk.Frame(root, bg=self.CARD, highlightthickness=1,
                        highlightbackground="#e3e5e9")
        card.pack(fill="both", expand=True, padx=16, pady=(4, 8))

        headers = ["按键", "今日", "本周日均", "近7天日均", "总计"]
        for col, text in enumerate(headers):
            tk.Label(card, text=text, bg=self.CARD, fg=self.MUTED,
                     font=("Microsoft YaHei UI", 9)).grid(
                row=0, column=col, padx=14, pady=(12, 6), sticky="e" if col else "w")
        tk.Frame(card, bg="#e8eaee", height=1).grid(
            row=1, column=0, columnspan=len(headers), sticky="ew", padx=12)

        rows = list(BUTTONS) + [("__total__", "合计")]
        for i, (key, label) in enumerate(rows):
            r = i + 2
            bold = key == "__total__"
            font = ("Microsoft YaHei UI", 10, "bold" if bold else "normal")
            fg = self.ACCENT if bold else self.FG
            tk.Label(card, text=label, bg=self.CARD, fg=fg, font=font,
                     anchor="w").grid(row=r, column=0, padx=14, pady=5, sticky="w")
            for col in range(1, len(headers)):
                lab = tk.Label(card, text="-", bg=self.CARD, fg=fg, font=font, anchor="e")
                lab.grid(row=r, column=col, padx=14, pady=5, sticky="e")
                self.cells[(key, col)] = lab
        card.columnconfigure(0, weight=1)

        info = tk.Frame(root, bg=self.BG)
        info.pack(fill="x", padx=16)
        self.info_week = tk.Label(info, text="", bg=self.BG, fg=self.MUTED,
                                  font=("Microsoft YaHei UI", 9), anchor="w")
        self.info_week.pack(anchor="w")
        self.info_all = tk.Label(info, text="", bg=self.BG, fg=self.MUTED,
                                 font=("Microsoft YaHei UI", 9), anchor="w")
        self.info_all.pack(anchor="w", pady=(2, 0))

        opts = tk.Frame(root, bg=self.BG)
        opts.pack(fill="x", padx=16, pady=(10, 4))
        self.autostart = tk.BooleanVar(value=startup_enabled())
        ttk.Checkbutton(opts, text="开机自动启动（登录后自动在后台记录）",
                        variable=self.autostart,
                        command=self.toggle_startup).pack(anchor="w")

        btns = tk.Frame(root, bg=self.BG)
        btns.pack(fill="x", padx=16, pady=(6, 12))
        for text, cmd in (("隐藏到后台", self.hide),
                          ("打开数据文件夹", self.open_folder),
                          ("退出程序", self.quit_app)):
            ttk.Button(btns, text=text, command=cmd, width=14).pack(side="left", padx=(0, 8))

        self.status = tk.Label(root, text="", bg=self.BG, fg=self.MUTED,
                               font=("Microsoft YaHei UI", 8), anchor="w")
        self.status.pack(fill="x", padx=16, pady=(0, 10))

        root.update_idletasks()
        w = 470
        h = root.winfo_reqheight() + 6
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, (sh - h) // 3))
        root.minsize(430, h)

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
            else:
                self.stats.add(item)
                changed = True
        now = datetime.datetime.now().timestamp()
        if self.stats.dirty:
            if changed is False or now - self.last_save > 3:
                self.stats.save()
                self.last_save = now
        self.refresh(force=changed)
        if now - self.last_tip >= 5:
            self.update_tip()
            self.last_tip = now
        if now - self.last_health >= 3:
            self.last_health = now
            self.health_check()
        self.root.after(400, self.poll)

    def health_check(self):
        """看门狗：后台线程或托盘图标没了就重新拉起，避免"图标在但点了没反应"。"""
        thread = WORKER.get("thread")
        if thread is not None and thread.is_alive() and is_tray_window_alive():
            return
        tray_log("看门狗：后台线程存活=%s 托盘窗口存活=%s，准备重启"
                 % (thread is not None and thread.is_alive(), is_tray_window_alive()))
        TRAY["hwnd"] = None
        TRAY["ready"] = False
        start_worker(self.sink)

    def refresh(self, force=False):
        if not force:
            return
        t = today()
        week_start = t - datetime.timedelta(days=t.weekday())
        week_days = (t - week_start).days + 1
        today_row = self.stats.day_row(t)
        week_row = self.stats.sum_range(week_start, t)
        week7_row = self.stats.sum_range(t - datetime.timedelta(days=6), t)
        chart = {}
        for k in KEYS:
            chart[k] = (today_row[k], week_row[k] / week_days,
                        week7_row[k] / 7.0, self.stats.total[k])
        chart["__total__"] = (sum(today_row.values()), sum(week_row.values()) / week_days,
                              sum(week7_row.values()) / 7.0, sum(self.stats.total.values()))
        for (key, col), lab in self.cells.items():
            value = chart[key][col - 1]
            lab.configure(text="%d" % value if col in (1, 4) else "%.1f" % value)

        weekday = "一二三四五六日"[t.weekday()]
        self.date_label.configure(text="今天 %s  星期%s" % (t.isoformat(), weekday))
        self.info_week.configure(
            text="本周（%s 起，共 %d 天）累计 %d 次   |   今日合计 %d 次"
                 % (week_start.strftime("%m月%d日"), week_days,
                    sum(week_row.values()), sum(today_row.values())))
        days = self.stats.recorded_days()
        total = sum(self.stats.total.values())
        self.info_all.configure(
            text="总计：记录 %d 天，共 %d 次，日均 %.1f 次"
                 % (days, total, total / days if days else 0.0))

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
        tray_modify(tip="%s｜今日 %d 次｜总计 %d 次"
                    % (APP_TITLE, sum(self.stats.day_row(today()).values()),
                       sum(self.stats.total.values())))

    def toggle_startup(self):
        try:
            enabled = set_startup(self.autostart.get())
            self.autostart.set(enabled)
            self.status.configure(
                text="开机自启动已开启" if enabled else "开机自启动已关闭", fg=self.MUTED)
        except Exception as exc:
            self.autostart.set(startup_enabled())
            messagebox.showerror(APP_TITLE, "设置开机自启动失败：\n%r" % (exc,))

    def open_folder(self):
        os.startfile(APP_DIR)

    def on_close(self):
        choice = messagebox.askyesnocancel(
            APP_TITLE,
            "关闭窗口后程序会继续在后台记录点击。\n\n"
            "是(Y)：隐藏到后台继续记录\n否(N)：完全退出程序\n取消：返回")
        if choice is None:
            return
        if choice:
            self.hide()
        else:
            self.quit_app()

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
    kernel32.CreateMutexW(None, 0, MUTEX_NAME)
    already = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
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


def dump():
    stats = Stats(DATA_FILE)
    t = today()
    week_start = t - datetime.timedelta(days=t.weekday())
    week_days = (t - week_start).days + 1
    today_row = stats.day_row(t)
    week_row = stats.sum_range(week_start, t)
    week7_row = stats.sum_range(t - datetime.timedelta(days=6), t)
    print("date=%s" % t.isoformat())
    for key, label in BUTTONS:
        print("%s today=%d week_avg=%.3f week7_avg=%.3f total=%d"
              % (label, today_row[key], week_row[key] / week_days,
                 week7_row[key] / 7.0, stats.total[key]))
    print("sum today=%d week_avg=%.3f week7_avg=%.3f total=%d days=%d startup=%s"
          % (sum(today_row.values()), sum(week_row.values()) / week_days,
             sum(week7_row.values()) / 7.0, sum(stats.total.values()),
             stats.recorded_days(), startup_enabled()))


def main():
    args = [a.lower() for a in sys.argv[1:]]
    if "--dump" in args:
        dump()
        return
    if "--set-startup" in args:
        print("开机自启动：%s" % ("已开启" if set_startup(True) else "设置失败"))
        return
    if "--clear-startup" in args:
        print("开机自启动：%s" % ("已关闭" if not set_startup(False) else "关闭失败"))
        return

    unique, handle = acquire_single_instance()
    if not unique:
        return
    kernel32, event = handle

    sink = queue.Queue()
    stats = Stats(DATA_FILE)
    root = tk.Tk()
    app = App(root, stats, sink)
    watch_show_event(kernel32, event, sink)
    start_worker(sink)
    try:
        root.mainloop()
    finally:
        stats.save(force=True)


if __name__ == "__main__":
    main()

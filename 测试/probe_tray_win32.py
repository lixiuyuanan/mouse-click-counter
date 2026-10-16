# -*- coding: utf-8 -*-
"""真 Win32 托盘窗口探针（不挂图标、不装钩子，桌面上看不到任何东西）。

单元测试里的 create_tray_window 用的是假 u 对象，注册类 / 建窗 / 回调这三步
的真实 ctypes 行为从没被覆盖过 —— 而 踩坑 15 那类「签名没声明，只在打包版炸」
的问题只有真调用才能暴露。本探针在独立线程里：
    注册窗口类 → 建隐藏窗口 → PostMessage 一条 WM_TRAY → 断言 sink 收到指令
    → 再建第二个窗口（验证类只注册一次、WNDPROC 引用不增长）
    → 由创建线程发 WM_CLOSE 拆窗（验证 WM_DESTROY → PostQuitMessage → 线程干净退出）

跑法：python -X utf8 测试/probe_tray_win32.py
"""
import ctypes
import os
import queue
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import env                                               # noqa: E402
import mouse_counter as mc                               # noqa: E402

WM_CLOSE = 0x0010


def post(hwnd, msg, wparam, lparam):
    return mc.user32_api().PostMessageW(ctypes.c_void_p(hwnd), msg, wparam, lparam)


def drain(sink, timeout=1.0):
    out = []
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            out.append(sink.get_nowait())
        except queue.Empty:
            time.sleep(0.02)
    return out


def main():
    # 路径全部锁进临时目录：探针只验窗口/消息，不该往用户文档里写任何东西
    guard = env.isolate(mc)
    problems = []
    box = {}
    done = threading.Event()
    sink = queue.Queue()
    try:
        u = mc.user32_api()
        u.GetMessageW.restype = ctypes.c_int
        u.GetMessageW.argtypes = [ctypes.POINTER(mc.wt.MSG), ctypes.c_void_p,
                                  ctypes.c_uint, ctypes.c_uint]

        def run():
            h = mc.create_tray_window(u, sink)
            box["hwnd"], box["proc"] = h, mc._TRAY_WNDPROC
            msg = mc.wt.MSG()
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                u.TranslateMessage(ctypes.byref(msg))
                u.DispatchMessageW(ctypes.byref(msg))
            done.set()

        t = threading.Thread(target=run, name="probe-tray", daemon=True)
        t.start()
        for _ in range(60):
            if box.get("hwnd"):
                break
            time.sleep(0.05)
        hwnd = box.get("hwnd")
        if not hwnd:
            raise AssertionError("托盘窗口没建出来：%s" % _tail(mc.LOG_FILE))
        print("  OK    注册窗口类 + 创建隐藏窗口 hwnd=%s" % hwnd)

        mc.TRAY["hwnd"] = hwnd
        if not u.IsWindow(ctypes.c_void_p(hwnd)):
            problems.append("IsWindow 判不出刚建好的窗口存活，看门狗会误判")

        # 左键：V1 把消息放 lParam，V2/V3/V4 放 wParam，两种都要认
        post(hwnd, mc.WM_TRAY, 1, mc.WM_LBUTTONUP)
        post(hwnd, mc.WM_TRAY, mc.WM_LBUTTONUP, 0)
        got = drain(sink)
        if got.count(("show", None)) != 2:
            problems.append("左键两种编码未各派发一条 show，实际收到 %r" % (got,))
        else:
            print("  OK    WM_TRAY 左键两种编码各派发一条 show")

        # 普通消息走 DefWindowProcW：返回值必须是合法 int。
        # 若回调返回 None，ctypes 把 LRESULT 回转时会当成 -1 指针并抛 ArgumentError，
        # 异常穿过原生栈直接带走整个钩子线程（计数和托盘一起没了）。
        post(hwnd, 0x0400, 0, 0)
        time.sleep(0.25)
        if done.is_set():
            problems.append("派发一条普通消息后线程退出（WNDPROC 返回值有问题）")
        else:
            print("  OK    普通消息经 DefWindowProcW 返回，线程健在")

        # 第二个窗口：必须复用第一次注册的类和同一个 WNDPROC，引用不再增长
        again = mc.create_tray_window(u, sink)
        if not again:
            problems.append("第二次创建窗口失败，类复用逻辑有问题")
        if mc._TRAY_WNDPROC is not box["proc"]:
            problems.append("WNDPROC 引用被换掉：旧窗口过程随时可能被 GC 变成野指针")
        else:
            print("  OK    第二次创建复用同一窗口类，WNDPROC 引用未增加")
        if again:
            u.DestroyWindow(ctypes.c_void_p(again))

        # 拆窗必须由创建它的线程执行：界面线程直接 DestroyWindow(钩子线程的窗口)
        # 会失败，所以发 WM_CLOSE 让钩子线程在 DispatchMessage 里自己拆 ——
        # DefWindowProc(WM_CLOSE) → WM_DESTROY → PostQuitMessage → 消息循环结束。
        # 产品里 quit_app 不调 DestroyWindow（进程退出时系统连窗口带钩子一起回收），
        # 但看门狗将来若要主动拆窗，必须走这条通道。
        post(hwnd, WM_CLOSE, 0, 0)
        if not done.wait(3.0):
            problems.append("WM_CLOSE 后消息循环没退出（WM_DESTROY/PostQuitMessage 有问题）")
        else:
            print("  OK    创建线程经 WM_CLOSE 拆窗 → PostQuitMessage，线程干净退出")
        t.join(2.0)

        if mc.TRAY.get("sink") is not sink:
            problems.append("create_tray_window 未把 sink 记进 TRAY，回调取不到队列")

        for p in problems:
            print("  FAIL  %s" % p)
        print("\n%s" % ("TRAY PROBE FAIL" if problems else "TRAY PROBE PASS"))
        return 1 if problems else 0
    finally:
        mc.TRAY["hwnd"] = None
        mc.TRAY["sink"] = None
        guard.close()


def _tail(path):
    try:
        data = open(path, encoding="utf-8").read()
        return data[-400:].strip()
    except OSError:
        return "(无日志)"


if __name__ == "__main__":
    sys.exit(main())

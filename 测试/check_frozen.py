# -*- coding: utf-8 -*-
"""静态核对 PyInstaller 产物，不启动 exe。

为什么不启动：exe 一起来，APP_DIR 就解析到「文档\\鼠标点击次数记录」（真实数据目录），
开发环境没有任何隔离手段能拦住它写；再说 --onefile --noconsole 的托盘程序跑起来
就没法在不使用 taskkill 的前提下关掉。所以冻结版只验静态事实，运行时行为留给用户启动。

用法：python 测试/check_frozen.py [exe路径]
不给路径就找 _pkg_tmp/dist/鼠标点击计数V{APP_VERSION}.exe。
"""
import ctypes
import os
import sys
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import mouse_counter as mc   # noqa: E402

IS64 = sys.maxsize > 2 ** 32
fails = []


def ok(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


def find_exe(argv):
    if len(argv) > 1:
        return argv[1]
    p = os.path.join(ROOT, "_pkg_tmp", "dist",
                     "鼠标点击计数V%s.exe" % mc.APP_VERSION)
    return p if os.path.exists(p) else None


def read_manifest(path):
    """从 PE 里取出 RT_MANIFEST。句柄与指针类返回值必须显式声明 restype，
    否则 64 位下被 ctypes 截成 32 位（踩坑 15），后面 LockResource 直接 ArgumentError。"""
    k = ctypes.windll.kernel32
    k.LoadLibraryExW.restype = wintypes.HMODULE
    k.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
    for fn, rest in (("FindResourceW", ctypes.c_void_p),
                     ("LoadResource", ctypes.c_void_p),
                     ("LockResource", ctypes.c_void_p)):
        f = getattr(k, fn)
        f.restype = rest
    k.FindResourceW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p]
    k.SizeofResource.restype = wintypes.DWORD
    k.SizeofResource.argtypes = [wintypes.HMODULE, ctypes.c_void_p]
    k.LoadResource.argtypes = [wintypes.HMODULE, ctypes.c_void_p]
    k.LockResource.argtypes = [ctypes.c_void_p]
    RT_MANIFEST = 24
    k.GetLastError.restype = wintypes.DWORD
    mod = k.LoadLibraryExW(path, None, 0x00000002)   # LOAD_LIBRARY_AS_DATAFILE
    if not mod:
        return None, "LoadLibraryExW 失败 GetLastError=%d" % k.GetLastError()
    try:
        for rid in (1, 2, 3):        # CREATEPROCESS / EXTERNAL / RESOURCE MANIFEST
            h = k.FindResourceW(mod, ctypes.c_void_p(rid), ctypes.c_void_p(RT_MANIFEST))
            if not h:
                continue
            n = k.SizeofResource(mod, h)
            p = k.LockResource(k.LoadResource(mod, h))
            if p and n:
                return ctypes.string_at(p, n).decode("utf-8", "replace"), None
    finally:
        k.FreeLibrary.argtypes = [wintypes.HMODULE]
        k.FreeLibrary(mod)
    return None, "没找到 RT_MANIFEST"


def read_version(path):
    v = ctypes.WinDLL("version")
    v.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    v.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR,
                                         ctypes.POINTER(wintypes.DWORD)]
    v.GetFileVersionInfoW.restype = wintypes.BOOL
    v.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                     wintypes.DWORD, ctypes.c_void_p]
    v.VerQueryValueW.restype = wintypes.BOOL
    v.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                 ctypes.POINTER(ctypes.c_void_p),
                                 ctypes.POINTER(wintypes.UINT)]
    zero = wintypes.DWORD(0)
    size = v.GetFileVersionInfoSizeW(path, ctypes.byref(zero))
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not v.GetFileVersionInfoW(path, 0, size, buf):
        return None

    def query(sub):
        pv, pl = ctypes.c_void_p(), wintypes.UINT()
        if not v.VerQueryValueW(buf, sub, ctypes.byref(pv), ctypes.byref(pl)):
            return None
        return pv, pl

    r = query("\\")
    out = {}
    if r and r[1].value >= 24:
        ffi = ctypes.cast(r[0], ctypes.POINTER(ctypes.c_uint32 * (r[1].value // 4)))
        for name, i in (("file", 2), ("product", 4)):
            ms, ls = ffi[0][i], ffi[0][i + 1]
            out[name] = "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    # StringFileInfo 走原始字节匹配：VerQueryValueW 的子块路径要精确的
    # Translation 键，PyInstaller 写出来的键序在不同版本里变过，
    # 而我们要的答案只是「这些字符串在不在资源里」，不必趟那个坑。
    for key, want in (("ProductName", mc.APP_TITLE), ("FileDescription", mc.APP_TITLE),
                      ("CompanyName", "黎修源"), ("LegalCopyright", "2026")):
        if want.encode("utf-16-le") in bytes(buf.raw):
            out[key] = want
    return out or None


def main():
    exe = find_exe(sys.argv)
    if not exe:
        print("FAIL 找不到待检查的 exe，先打包或把路径作为参数传入")
        return 1
    print("检查 %s（%d B）" % (exe, os.path.getsize(exe)))
    ok(os.path.isabs(exe), "路径是绝对路径")
    ok(IS64, "本机 64 位 Python（截断类 ctypes bug 只在 64 位暴露）")

    from PyInstaller.archive.readers import CArchiveReader
    try:
        ar = CArchiveReader(exe)
        names = list(ar.toc.keys()) if isinstance(ar.toc, dict) else [e[0] for e in ar.toc]
    except Exception as exc:
        print("FAIL 归档读不出来：%r（exe 可能损坏或不是 PyInstaller 产物）" % exc)
        return 1
    low = [n.lower() for n in names]
    ok(any(n == "mouse_counter" or n.endswith("mouse_counter.pyc") for n in names),
       "归档含 mouse_counter 主模块（共 %d 条目）" % len(names))
    ok("icon.ico" in names or any(n.endswith("icon.ico") for n in low),
       "icon.ico 已内嵌（缺失 → exe 图标变默认）")
    ok(any("tcl" in n or n.startswith("_tkinter") for n in low), "Tk 运行时已打包")
    ok(not any(mc.DATA_NAME in n or "鼠标点击记录" in n for n in names),
       "没有把数据文件打进 exe")

    ver = read_version(exe)
    ok(bool(ver), "版本资源可读")
    if ver:
        want = mc.APP_VERSION + ".0"
        ok(ver.get("file") == want, "FileVersion = %s（应为 %s）"
           % (ver.get("file"), want))
        ok(str(ver.get("product", "")).startswith(mc.APP_VERSION),
           "ProductVersion = %s" % ver.get("product"))
        for key in ("ProductName", "FileDescription", "CompanyName", "LegalCopyright"):
            ok(key in ver, "%s 资源里能找到（%r）" % (key, ver.get(key, "")))

    mani, err = read_manifest(exe)
    if mani:
        ok("asInvoker" in mani, "清单 level = asInvoker（双击不弹 UAC）")
        ok("requireAdministrator" not in mani, "清单无 requireAdministrator")
        if "dpiAware" not in mani:
            print("INFO 清单未声明 dpiAware，依赖 main() 里 tk.Tk() 之前的 "
                  "enable_dpi_awareness()（与 2.4.1 交付版一致）")
    else:
        ok(False, "清单读取失败：%s" % err)

    prev = os.path.join(os.path.dirname(exe), "鼠标点击计数V2.4.1.exe")
    if os.path.exists(prev):
        delta = os.path.getsize(exe) - os.path.getsize(prev)
        ok(abs(delta) < 400000, "体积与上一版相差 %+d B（同数量级，无异常膨胀）" % delta)

    print("\n" + ("FROZEN_CHECK_PASS" if not fails
                 else "FROZEN_CHECK_FAIL（%d 项）" % len(fails)))
    for f in fails:
        print("   - " + f)
    print("提醒：本脚本只证明产物是完整正确的 PE，**不代表运行时无恙**；"
          "钩子、托盘、--dump 需要用户亲自启动一次。")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

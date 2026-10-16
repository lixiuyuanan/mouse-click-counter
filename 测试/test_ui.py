# -*- coding: utf-8 -*-
"""界面层回归测试：调色板对齐、主题重建、表格/热力/KPI 刷新口径、poll 分发。

跑法：python -X utf8 测试/run_tests.py ui
全程 headless：root.withdraw() + silent=True + env.silence_backend()，
不进 mainloop、不装钩子、不建托盘、不碰注册表（env 里已把危险调用换成会
直接 AssertionError 的桩）。
"""
import contextlib
import datetime
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk

import run_tests

ok, eq, near, raises, nothing = (run_tests.ok, run_tests.eq, run_tests.near,
                                 run_tests.raises, run_tests.nothing)


@contextlib.contextmanager
def ui(mc, env, raw=None, theme="light"):
    """在隔离目录里起一个不显示的 App；退出时必拆 root。"""
    import tkinter as tk
    env.silence_backend(mc)
    base = raw if raw is not None else {"version": 3, "settings": {}}
    if theme is not None:                      # None = 让 raw 里的 theme 原样生效
        base.setdefault("settings", {})["theme"] = theme
    stats = env.new_stats(mc, base)
    root = tk.Tk()
    root.withdraw()
    try:
        app = mc.App(root, stats, queue.Queue(), silent=True)
        yield app
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def full_row(mc, **kw):
    """造一行完整计数（refresh 读的是固定 6 键）。"""
    base = {k: 0 for k in mc.KEYS}
    base.update(kw)
    return base


# ---------------------------------------------------------------- 主题与调色板

def test_双调色板键值对齐(mc, env, g):
    ok, eq, near = run_tests.ok, run_tests.eq, run_tests.near
    light, dark = mc.App.THEME_LIGHT, mc.App.THEME_DARK
    eq(sorted(light), sorted(dark), "两套调色板的键必须一一对应，漏一个就 AttributeError")
    ok(len(light) >= 20, "调色板至少 20 色，实际 %d" % len(light))
    for name, c in list(light.items()) + list(dark.items()):
        ok(isinstance(c, str) and len(c) == 7 and c[0] == "#"
           and all(ch in "0123456789abcdef" for ch in c[1:]),
           "%s 必须是 #rrggbb：实际 %r" % (name, c))
    eq(light["CARD"], "#ffffff", "浅色卡片白")
    eq(dark["BG"], "#15171c", "深色背景")


def test_palette_挂到实例属性(mc, env, g):
    with ui(mc, env, theme="light") as app:
        eq(app.BG, mc.App.THEME_LIGHT["BG"], "浅色：self.BG 来自 LIGHT")
        eq(app.dark, False, "light 不判成深色")
    with ui(mc, env, theme="dark") as app:
        eq(app.BG, mc.App.THEME_DARK["BG"], "深色：self.BG 来自 DARK")
        eq(app.dark, True, "dark 生效")


def test_切换主题保留当前页(mc, env, g):
    """切主题 = 整窗重建，但重建完必须回到用户原来那一页。

    实测 v2.4.1：在设置页改「界面模式」会被踢回首页，改完还得再点一次回去。
    """
    with ui(mc, env) as app:
        app.show_page("settings")
        eq(app.page, "settings", "先停在设置页")
        app.switch_theme("dark")
        eq(app.theme_mode, "dark", "主题已切换")
        eq(app.page, "settings", "切主题后仍在设置页")
        eq(app.pages["settings"].winfo_manager(), "pack", "设置页确实显示着")
        eq(app.stats.get_setting("theme"), "dark", "选择已持久化")


def test_切换主题不泄漏控件(mc, env, g):
    with ui(mc, env) as app:
        before = len(app.root.winfo_children())
        cells_before = len(app.cells)
        eq(len(app.pages), 4, "四个页面都建了")
        eq(cells_before, len(app.table_rows) * 3, "明细表数字单元格＝行×3 列")
        app.switch_theme("dark")
        eq(len(app.root.winfo_children()), before, "重建后 root 子控件数不变（无残留）")
        eq(len(app.pages), 4, "重建后仍是四页")
        eq(len(app.cells), cells_before, "cells 数量与重建前一致（旧的已丢弃）")
        ok(all(w.winfo_exists() for w in app.cells.values()), "cells 指向的都是活控件")
        app.switch_theme("light")
        eq(len(app.root.winfo_children()), before, "切回来同样干净")


def test_theme_非法值回落auto(mc, env, g):
    with ui(mc, env, raw={"version": 3, "settings": {"theme": "purple"}},
           theme=None) as app:
        eq(app.theme_mode, "auto", "写坏的 theme 值不能崩，回落 auto")
        ok(app.BG in (mc.App.THEME_LIGHT["BG"], mc.App.THEME_DARK["BG"]),
           "auto 也要落到某一套调色板")


# ---------------------------------------------------------------- 明细表

def test_表格列与口径(mc, env, g):
    with ui(mc, env) as app:
        eq(app.table_headers, ["按键", "寿命", "今日", "近7天日均", "总计"], "表头顺序")
        eq([c for (_k, c) in app.cells], [2, 3, 4] * len(app.table_rows),
           "每行只有今日/日均/总计三个数字列")
        pid = mc.current_profile(app.stats)["id"]
        t = mc.today()
        app.stats.profile_days[pid] = {
            t.isoformat(): {"left": 7, "right": 3, "middle": 0, "x1": 0, "x2": 0,
                            "wheel": 5},
            (t - datetime.timedelta(days=6)).isoformat():
                {"left": 70, "right": 0, "middle": 0, "x1": 0, "x2": 0, "wheel": 0}}
        app.stats.select_profile(pid)
        app.refresh(force=True, life_now=True)
        eq(app.cells[("left", 2)]["text"], "7", "今日左键＝整数")
        eq(app.cells[("left", 3)]["text"], "11.0", "近7天日均＝71/7 带一位小数")
        eq(app.cells[("left", 4)]["text"], "77", "总计")
        eq(app.cells[("wheel", 2)]["text"], "5", "滚轮单独成行")
        eq(app.cells[("__total__", 2)]["text"], "10", "合计＝可见点击键相加，不含滚轮")
        eq(app.kpi["today"]["text"], "10", "KPI 今日同样是点击口径（不含滚轮 5）")


def test_隐藏侧键只影响显示不影响计数(mc, env, g):
    with ui(mc, env) as app:
        pid = mc.current_profile(app.stats)["id"]
        t = mc.today().isoformat()
        app.stats.profile_days[pid] = {t: full_row(mc, left=5, x1=7)}
        app.stats.select_profile(pid)
        app.refresh(force=True)
        eq(app.cells[("__total__", 2)]["text"], "12", "默认全显示：合计含侧键")
        mc.current_profile(app.stats)["hidden"] = ["x1"]
        app.refresh(force=True)
        for w in app.table_row_widgets["x1"]:
            eq(w.grid_info(), {}, "grid_remove 后不再参与布局")
        for w in app.table_row_widgets["left"]:
            ok(w.grid_info() != {}, "未隐藏的行仍在布局里")
        eq(app.cells[("__total__", 2)]["text"], "5", "隐藏侧键不计入合计行")
        eq(app.kpi["total"]["text"], "12", "KPI 总点击仍是真实总数")
        eq(app.stats.day_row(mc.today())["x1"], 7, "隐藏只是不显示，照常计数")
        mc.current_profile(app.stats)["hidden"] = []
        app.refresh(force=True)
        ok(app.table_row_widgets["x1"][0].grid_info() != {}, "取消隐藏后回到表里")


def test_寿命列中性色_圆点表状态(mc, env, g):
    """v2.4.1 定稿：数字用中性色，状态只靠前面那个小圆点的颜色。"""
    with ui(mc, env) as app:
        pid = mc.current_profile(app.stats)["id"]
        app.stats.profile_days[pid] = {mc.today().isoformat(): full_row(mc, left=1000000)}
        app.stats.select_profile(pid)
        app.stats.settings["profiles"][0]["life"] = dict(mc.DEFAULT_LIFE)
        app.refresh(force=True, life_now=True)
        eq(app.life_cells["left"]["text"], "5.00%", "寿命两位小数")
        eq(app.life_cells["left"].cget("fg"), app.FG, "数字颜色＝中性前景色")
        eq(app.life_dots["left"].cget("fg"), mc.life_color(5.0), "圆点随寿命渐变")
        eq(app.life_cells["right"]["text"], "0.00%", "0% 也要显示而不是划掉")
        eq(app.life_cells["middle"]["text"], "0.00%", "未使用键显示 0%")
        app.stats.settings["profiles"][0]["life"]["x1"] = 0
        app.refresh(force=True, life_now=True)
        eq(app.life_cells["x1"]["text"], "—", "寿命上限 0 → 不统计")
        eq(app.life_cells["x1"].cget("fg"), app.MUTED, "— 用弱化色")
        eq(app.life_dots["x1"].cget("fg"), app.life_dots["x1"].cget("bg"),
           "无数据时圆点隐藏（与底色同色）")


# ---------------------------------------------------------------- 热力日历

def test_热力网格_35格周一起始(mc, env, g):
    with ui(mc, env) as app:
        t = mc.today()
        app.refresh(force=True)
        grid = app.heat_grid
        eq(len(grid), 35, "5 行 × 7 列")
        eq(grid[0].weekday(), 0, "周一起始")
        eq(grid[-1].weekday(), 6, "整周收尾")
        eq(grid, sorted(grid), "按日期递增")
        eq([d for i, d in enumerate(grid) if i % 7 == 0],
           [grid[0] + datetime.timedelta(days=7 * i) for i in range(5)], "每行都从周一开始")
        ok(t in grid, "今天必须在网格里")
        ok(grid[0] <= t - datetime.timedelta(days=27),
           "起点必须覆盖到「今天往前 28 天」的第一天")
        eq(app.heat_month, t.month, "高亮月份＝本月")
        ok(len({d.month for d in grid}) <= 3, "最多跨三个月（两侧各补一周）")


def test_热力画布空格画空字(mc, env, g):
    with ui(mc, env) as app:
        pid = mc.current_profile(app.stats)["id"]
        t = mc.today()
        app.stats.profile_days[pid] = {t.isoformat(): full_row(mc, left=8000)}
        app.stats.select_profile(pid)
        app.refresh(force=True)
        texts = [app.canvas.itemcget(i, "text") for i in app.canvas.find_all()
                 if app.canvas.type(i) == "text"]
        future = sum(1 for d in app.heat_grid if d > t)
        ok(future > 0, "补齐到整周必然会把还没到的日子画进来")
        eq(texts.count("空"), 34 - future, "除今天外，真正无记录的日子画「空」")
        eq(texts.count("—"), future, "还没到的日子画横线，不能谎称「那天没点」")
        ok("8,000" in texts, "今天那格显示千分位次数")
        eq(len(app._heat_cells), 35, "35 格全部登记，供悬停查询")
        eq(app._heat_cells[app.heat_grid.index(t)][1], 8000, "今天的次数已入格")
        eq(sum(1 for i in app.canvas.find_all()
               if app.canvas.type(i) == "oval"), 1, "只有有记录的那格画圆点")


def test_热力悬停_未来日不谎称无记录(mc, env, g):
    with ui(mc, env) as app:
        app.refresh(force=True)
        t = mc.today()
        future_idx = [i for i, d in enumerate(app.heat_grid) if d > t]
        ok(future_idx, "网格里有还没到的日子")
        width, gap, head_h, cell_h, cell_w = app._heat_layout
        idx = future_idx[0]
        r, c = divmod(idx, 7)
        ev = type("E", (), {})()
        ev.x, ev.y = c * (cell_w + gap) + cell_w // 2, head_h + r * cell_h + cell_h // 2
        app.heat_hover(ev)
        ok("还没到" in app.week_note["text"],
           "悬停未来格应说明还没到，实际：%s" % app.week_note["text"])
        ok("无记录" not in app.week_note["text"], "不该说成无记录")


def test_热力悬停与移出(mc, env, g):
    with ui(mc, env) as app:
        app.refresh(force=True)
        width, gap, head_h, cell_h, cell_w = app._heat_layout
        ev = type("E", (), {})()
        ev.x, ev.y = cell_w // 2, head_h + cell_h // 2
        app.heat_hover(ev)
        note = app.week_note["text"]
        ok(note.startswith(app.heat_grid[0].isoformat()),
           "注释以左上角那格的日期开头：%s" % note)
        ok("周" in note, "注释里带星期：%s" % note)
        ev2 = type("E", (), {})()
        ev2.x, ev2.y = -50, -50
        app.heat_hover(ev2)
        eq(app.week_note["text"], app._heat_default_note, "移出网格区回到默认注释")
        app.heat_leave()
        eq(app.week_note["text"], app._heat_default_note, "Leave 也复位")


# ---------------------------------------------------------------- KPI

def test_kpi环比箭头规则(mc, env, g):
    with ui(mc, env) as app:
        pid = mc.current_profile(app.stats)["id"]
        t = mc.today()
        y = (t - datetime.timedelta(days=1)).isoformat()
        app.stats.profile_days[pid] = {
            y: full_row(mc, left=100),
            t.isoformat(): full_row(mc, left=150)}
        app.stats.select_profile(pid)
        app.refresh(force=True)
        eq(app.kpi_delta["today"]["text"], "▲ +50.0%", "今日涨")
        eq(app.kpi_delta["today"].cget("fg"), app.UP, "涨用涨色")
        app.stats.profile_days[pid][t.isoformat()]["left"] = 50
        app.stats.select_profile(pid)
        app.refresh(force=True)
        eq(app.kpi_delta["today"]["text"], "▼ -50.0%", "跌")
        eq(app.kpi_delta["today"].cget("fg"), app.DOWN, "跌用跌色")
        app.stats.profile_days[pid] = {t.isoformat(): full_row(mc, left=9)}
        app.stats.select_profile(pid)
        app.refresh(force=True)
        eq(app.kpi_delta["today"]["text"], "—", "昨日为 0 不算百分比")
        eq(app.kpi_delta["total"]["text"], "—", "总点击没有环比口径")


def test_kpi悬停看精确值不该全量刷新(mc, env, g):
    """移出时只要把文本换回缩写；调 refresh(force=True) 会重绘整页（含图表）。"""
    with ui(mc, env) as app:
        pid = mc.current_profile(app.stats)["id"]
        app.stats.profile_days[pid] = {mc.today().isoformat(): full_row(mc, left=12345678)}
        app.stats.select_profile(pid)
        app.refresh(force=True)
        short = app.kpi["today"].cget("text")
        eq(short, "1234.6 万", "缩写形式（千万级走「万」且不加千分位）")
        calls = []
        real = app.refresh
        app.refresh = lambda **kw: calls.append(kw) or real(**kw)
        app._kpi_hover("today", True)
        eq(app.kpi["today"].cget("text"), app.kpi_full["today"], "悬停显示精确全值")
        ok(app.kpi["today"].cget("text") != short, "全值与缩写不同才有意义")
        app._kpi_hover("today", False)
        eq(app.kpi["today"].cget("text"), short, "移出回到缩写")
        eq(calls, [], "悬停进出不该触发任何全量刷新")


def test_kpi缩写与悬停一致(mc, env, g):
    with ui(mc, env) as app:
        pid = mc.current_profile(app.stats)["id"]
        big = 123456789
        app.stats.profile_days[pid] = {mc.today().isoformat(): full_row(mc, left=big)}
        app.stats.select_profile(pid)
        app.refresh(force=True)
        eq(app.kpi["total"]["text"], "1.23 亿", "亿级缩写")
        eq(app.kpi_full["total"], "123,456,789 次", "精确值带千分位")


# ---------------------------------------------------------------- poll / 状态栏

def test_poll_分发字符串与指令(mc, env, g):
    with ui(mc, env) as app:
        app.last_save = app.last_tip = app.last_status = app.last_health = 0.0
        app.sink.put("left")
        app.sink.put("wheel")
        app.sink.put(("ready", None))
        app.sink.put(("未知指令", 1))
        app.poll()
        eq(app.stats.day_row(mc.today())["left"], 1, "点击字符串 → 计数")
        eq(app.stats.day_row(mc.today())["wheel"], 1, "滚轮同样入账")
        ok("运行中" in app.status["text"], "ready 指令写状态栏")
        eq(app.hook_warning, False, "收到真实点击不该报钩子异常")


def test_poll_滚轮按格累积(mc, env, g):
    """钩子线程发来的是 delta 幅度，不是一条消息一格。"""
    with ui(mc, env) as app:
        for mag in (30, 30, 30, 30, 240):
            app.sink.put(("wheel-notches", mag))
        app.poll()
        eq(app.stats.day_row(mc.today())["wheel"], 3, "120 凑 1 格 + 240 = 2 格")
        eq(app.stats.wheel_residual, 0, "正好整格，无残量")
        app.sink.put(("wheel-notches", 40))
        app.poll()
        eq(app.stats.day_row(mc.today())["wheel"], 3, "40 delta 还不足一格")
        eq(app.stats.wheel_residual, 40, "残量留到下一条消息")


def test_poll_切换档案指令(mc, env, g):
    raw = {"version": 3, "settings": {"profile_id": "p1", "profiles": [
        {"id": "p1", "name": "A"}, {"id": "p2", "name": "B"}]},
        "profile_days": {"p1": {}, "p2": {mc.today().isoformat(): {"left": 42}}}}
    with ui(mc, env, raw=raw) as app:
        eq(app.stats.total["left"], 0, "初始在 A")
        app.sink.put(("switch-profile", "p2"))
        app.poll()
        eq(app.stats.settings["profile_id"], "p2", "托盘菜单切档案走同一条路")
        eq(app.date_label["text"].count("B"), 1, "标题行跟着换")


def test_状态栏含版本与权限(mc, env, g):
    with ui(mc, env) as app:
        app.update_status()
        txt = app.status["text"]
        ok(mc.APP_VERSION in txt, "状态栏必须能看到版本号：%s" % txt)
        ok("权限" in txt, "状态栏报权限状态")


def test_关闭按钮是隐藏不是退出(mc, env, g):
    with ui(mc, env) as app:
        destroyed = []
        app.root.bind("<<ForceQuit>>", lambda _e: destroyed.append(1))
        app.on_close()
        eq(app.root.state(), "withdrawn", "点 ✕ 后窗口收起，进程继续记录")


def test_页面切换不改变窗口尺寸(mc, env, g):
    with ui(mc, env) as app:
        app.root.update_idletasks()
        h0 = app._fixed_h
        for page in app.PAGES:
            app.show_page(page)
            eq(app._fixed_h, h0, "%s 页不该改冻结高度" % page)


# ---------------------------------------------------------------- 源码不变式

def test_Win32调用必须声明签名(mc, env, g):
    """踩坑 15 的机器化版本：ctypes 不声明 argtypes/restype，64 位句柄会被按
    C int 传递 → OverflowError，而且只在打包版触发（源码跑不到那条分支）。
    凡是经 u./s./k./g./ctypes.windll.x./shcore. 调用的 Win32 函数，都必须有显式签名。"""
    import re
    src = open(os.path.join(os.path.dirname(run_tests.__file__), os.pardir,
                            "mouse_counter.py"), encoding="utf-8").read()
    declared = set(re.findall(r"\.\b([A-Z][A-Za-z0-9_]+)\.(?:argtypes|restype)\b", src))
    used = {}
    pat = re.compile(r"(?<![\w.])(?:[uskg]|shcore)\.([A-Z][A-Za-z0-9_]+)\("
                     r"|ctypes\.windll\.\w+\.([A-Z][A-Za-z0-9_]+)\(")
    for m in pat.finditer(src):
        name = m.group(1) or m.group(2)
        used.setdefault(name, []).append(src[:m.start()].count("\n") + 1)
    undeclared = {n: v for n, v in used.items() if n not in declared}
    eq(undeclared, {}, "这些 Win32 调用没有 restype/argtypes：%s"
       % {n: "行%s" % v for n, v in sorted(undeclared.items())})
    ok(len(used) > 20, "扫描确实覆盖到了（找到 %d 个调用点）" % len(used))


def test_去系统标题栏可反复调用(mc, env, g):
    """Tk 每次映射都会加回 WS_CAPTION，所以这段逻辑必须幂等且不抛。"""
    with ui(mc, env) as app:
        nothing(lambda: app._strip_native_caption(), "第一次调用不抛")
        nothing(lambda: app._strip_native_caption(), "重复调用不抛（幂等）")


# ---------------------------------------------------------------- 托盘看门狗

def test_看门狗_两者都健在只做钩子阻塞检查(mc, env, g):
    with ui(mc, env) as app:
        calls = []
        mc.is_tray_window_alive = lambda: True
        mc.request_tray_rebuild = lambda: calls.append("rebuild") or True
        mc.start_worker = lambda sink: calls.append("start_worker")
        app.check_hook_blocked = lambda: calls.append("blocked")
        app.health_check()
        eq(calls, ["blocked"], "一切正常时不该有任何重启动作")


def test_看门狗_线程健在但托盘窗口丢失时请求重建(mc, env, g):
    """v2.4.1 的死角落：钩子线程还活着（计数照走），窗口却因异常被销毁，
    而 start_worker 见线程活着会直接 return —— 看门狗等于每 3 秒空转一次，
    托盘图标再也回不来，只能重启程序。"""
    with ui(mc, env) as app:
        calls = []
        mc.is_tray_window_alive = lambda: False
        mc.TRAY["hwnd"] = 12345
        mc.request_tray_rebuild = lambda: calls.append("rebuild") or True
        mc.start_worker = lambda sink: calls.append("start_worker")
        app.check_hook_blocked = lambda: calls.append("blocked")
        app.health_check()
        eq(calls, ["rebuild"], "只能投递重建请求，不能再起线程也不能做阻塞检查")
        eq(mc.TRAY["hwnd"], None, "死句柄要先清掉，否则 tray_modify 会拿它去 NIM_MODIFY")


def test_看门狗_线程真死了才重启线程(mc, env, g):
    with ui(mc, env) as app:
        calls = []

        class Dead(object):
            def is_alive(self):
                return False

        mc.WORKER["thread"] = Dead()
        mc.is_tray_window_alive = lambda: False
        mc.request_tray_rebuild = lambda: calls.append("rebuild") or True
        mc.start_worker = lambda sink: calls.append("start_worker")
        app.check_hook_blocked = lambda: calls.append("blocked")
        app.health_check()
        eq(calls, ["start_worker"], "线程没了仍走老路径：整线程连同托盘一起拉起")


def test_request_tray_rebuild_发给钩子线程(mc, env, g):
    seen = []

    class Fake(object):
        def PostThreadMessageW(self, tid, msg, wparam, lparam):
            seen.append((tid, msg))
            return 1

    orig_api, orig_tid = mc.user32_api, mc.HOOK_STATE["thread_id"]
    mc.user32_api = lambda: Fake()
    try:
        mc.HOOK_STATE["thread_id"] = 4321
        ok(mc.request_tray_rebuild(), "有线程 id 时投递成功")
        eq(seen, [(4321, mc.WM_TRAY_REBUILD)], "必须发给钩子线程，消息号是 WM_TRAY_REBUILD")
        mc.HOOK_STATE["thread_id"] = 0
        eq(mc.request_tray_rebuild(), False, "没有线程 id 返回 False，交给下一轮看门狗")
    finally:
        mc.user32_api, mc.HOOK_STATE["thread_id"] = orig_api, orig_tid


def test_线程消息不撞号(mc, env, g):
    eq(len({mc.WM_TRAY, mc.WM_HOOK_REINSTALL, mc.WM_TRAY_REBUILD}), 3,
       "三个消息号必须互不相同")
    ok(mc.WM_TRAY_REBUILD > mc.WM_APP and mc.WM_TRAY_REBUILD < 0xBFFF,
       "落在 WM_APP 私有区间内")


def test_托盘窗口类只注册一次(mc, env, g):
    """旧写法每建一个窗口就 append 一个 WNDPROC 并再注册一次同名类
    （RegisterClassW 返回错误 1410，等于白做），长跑只会涨不会降。"""
    saved_proc, saved_sink = mc._TRAY_WNDPROC, mc.TRAY.get("sink")
    calls = []

    class Fake(object):
        def RegisterClassW(self, _byref):
            calls.append("register")
            return 0xC001

        def CreateWindowExW(self, *a):
            calls.append("create")
            return 0x1234

    try:
        sink = queue.Queue()
        eq(mc.create_tray_window(Fake(), sink), 0x1234, "窗口句柄要返回给调用方")
        eq(mc.TRAY["sink"], sink, "sink 记在 TRAY 里，回调运行时才取")
        eq(calls, ["register", "create"], "第一次：注册 + 建窗")
        mc.create_tray_window(Fake(), queue.Queue())
        eq(calls, ["register", "create", "create"], "第二次：复用同一个窗口类")
        ok(mc._TRAY_WNDPROC is not None, "回调对象必须留引用，否则被 GC 后窗口过程变成野指针")
    finally:
        mc._TRAY_WNDPROC, mc.TRAY["sink"] = saved_proc, saved_sink


def test_ensure_tray_window_幂等(mc, env, g):
    done = []
    orig = (mc.is_tray_window_alive, mc.create_tray_window, mc.add_tray_icon)
    try:
        mc.is_tray_window_alive = lambda: False
        mc.create_tray_window = lambda u, sink: done.append("create") or 0x99
        mc.add_tray_icon = lambda u, hwnd: done.append("icon")
        eq(mc.ensure_tray_window(None, queue.Queue()), 0x99, "窗口丢了就重建并补图标")
        eq(done, ["create", "icon"], "顺序：先有窗口再挂图标")
        mc.is_tray_window_alive = lambda: True
        mc.TRAY["hwnd"] = 0x99
        eq(mc.ensure_tray_window(None, queue.Queue()), 0x99, "还在就原样返回")
        eq(done, ["create", "icon"], "不重复建窗口，TaskbarCreated 之外也敢放心调")
    finally:
        (mc.is_tray_window_alive, mc.create_tray_window,
         mc.add_tray_icon) = orig
        mc.TRAY["hwnd"] = None


# ---------------------------------------------------------------- 视觉精修 v2.4.2

def test_标题栏用卡片色且不加分隔线(mc, env, g):
    """底色走 CARD，与正文的 BG 差一档，窗口就有外壳感。
    分隔线 v2.4.2 加过又被用户否掉：统一风格优先，不加解释性的线。"""
    with ui(mc, env) as app:
        kids = app.root.winfo_children()
        eq(kids[0], app.title_bar, "标题栏是 root 的第一个孩子")
        eq(app.title_bar.cget("bg"), app.CARD, "标题栏底色＝CARD")
        ok(not hasattr(app, "title_sep"), "标题栏下面不再压分隔线")
        ok(app.title_bar.cget("bg") != app.BG, "CARD 与正文 BG 必须有色差，否则整窗糊成一片")
        app.switch_theme("dark")
        eq(app.title_bar.cget("bg"), app.CARD, "深色主题同样跟着 CARD 走")
        ok(not hasattr(app, "title_sep"), "换主题后也不该冒出分隔线")


class _FakeCaption(object):
    """假 user32/dwmapi：记录调用，不碰真窗口。"""

    def __init__(self, hwnd=0x1234, presses=10 ** 9, cursor=(500, 400), alive=True):
        self.calls = []
        self.hwnd = hwnd
        self.dbl = 500
        self.presses = presses          # 左键「仍按住」的帧数，之后自动算抬起
        self.cursor = cursor
        self.alive = alive

    def GetAncestor(self, _child, _flag):
        return self.hwnd

    def GetDoubleClickTime(self):
        return self.dbl

    def GetAsyncKeyState(self, vk):
        self.calls.append(("GetAsyncKeyState", vk))
        left = self.presses
        self.presses = left - 1 if left > 0 else 0
        return -32766 if left > 0 else 0    # 0x8000 位表示按下；抬起只留 1 位

    def IsWindow(self, hwnd):
        self.calls.append(("IsWindow", hwnd))
        return bool(self.alive)

    def GetCursorPos(self, p):
        self.calls.append(("GetCursorPos",))
        p.contents.x, p.contents.y = self.cursor
        return 1

    def SetWindowPos(self, hwnd, after, x, y, cx, cy, flags):
        self.calls.append(("SetWindowPos", hwnd, x, y, flags))
        return 1


def _use_caption(mc, fake):
    real = mc._caption_api
    mc._caption_api = lambda: (fake, fake)
    return real


def _evt(t=1000, xr=300, yr=200):
    """只要 .time/.x_root/.y_root 三个字段，够喂 _title_press。"""
    return type("E", (), {"time": t, "x_root": xr, "y_root": yr})()


def _no_thread(app):
    """拦住真起线程：只记下参数，测试里同步跑 _drag_loop 才可控。"""
    started = []
    real = threading.Thread

    class _Rec(object):
        def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
            self.args = args
            self.target = target
            self.name = name

        def start(self):
            started.append(self.args)

        def is_alive(self):
            return False

    threading.Thread = _Rec
    return real, started


def test_标题栏按下起后台线程搬窗口不碰Tcl(mc, env, g):
    """v2.4.3 在 Tk 回调里跑系统模态移动循环，sent message 重入派发给 Tcl →
    abort() 闪退（ucrtbase 0xc0000409）。移动必须离开 Tcl：起线程 + SetWindowPos。"""
    with ui(mc, env) as app:
        fake = _FakeCaption()
        real = _use_caption(mc, fake)
        real_thr, started = _no_thread(app)
        try:
            app._title_press(_evt(t=1000, xr=300, yr=200))
        finally:
            mc._caption_api = real
            threading.Thread = real_thr
        eq(len(started), 1, "按下起一个拖拽线程")
        hwnd, off, stop = started[0]
        eq(hwnd, fake.hwnd, "搬的是根窗口，不是 Tk 的内容窗")
        ok(isinstance(off, tuple) and len(off) == 2, "记下按下点与窗口原点的偏移")
        ok(stop.is_set() is False, "刚起的线程不该带着「停止」信号")
        ok(app._drag_off is None, "走线程路径就不该再留 Tk 兜底偏移量")
        eq([c for c in fake.calls if c[0] == "SetWindowPos"], [],
           "按下本身不搬窗口，搬是线程的事")


def test_拖拽线程按光标偏移搬窗口(mc, env, g):
    """线程每帧读 GetCursorPos 再 SetWindowPos：位置＝光标-偏移，松手即停。"""
    with ui(mc, env) as app:
        fake = _FakeCaption(presses=3, cursor=(500, 400))
        real = _use_caption(mc, fake)
        stop = threading.Event()
        try:
            app._drag_loop(fake.hwnd, (20, 30), stop)
        finally:
            mc._caption_api = real
        moves = [c for c in fake.calls if c[0] == "SetWindowPos"]
        eq(len(moves), 3, "按住三帧就搬三次")
        eq(moves[0][2], 480, "x = 光标x - 偏移x")
        eq(moves[0][3], 370, "y = 光标y - 偏移y")
        eq(moves[0][4], mc.SWP_NOSIZE | mc.SWP_NOZORDER | mc.SWP_NOACTIVATE,
           "只挪位置：不改尺寸、不改层序、不抢焦点")
        ok(stop.is_set(), "循环自己收尾，不留悬挂标志")


def test_抬起丢失或窗口消失时线程自己收工(mc, env, g):
    """抬起事件如果没送到 Tk，线程靠 GetAsyncKeyState 自检；窗口被销毁则立刻退出，
    免得对废句柄 SetWindowPos。"""
    with ui(mc, env) as app:
        fake = _FakeCaption(presses=0)
        real = _use_caption(mc, fake)
        stop = threading.Event()
        try:
            app._drag_loop(0x1234, (0, 0), stop)
        finally:
            mc._caption_api = real
        ok(stop.is_set(), "一帧都没按住就该收工")
        eq([c for c in fake.calls if c[0] == "SetWindowPos"], [], "抬起已丢失就不搬窗口")

        fake2 = _FakeCaption(presses=5, alive=False)
        real = _use_caption(mc, fake2)
        stop2 = threading.Event()
        try:
            app._drag_loop(0x1234, (0, 0), stop2)
        finally:
            mc._caption_api = real
        eq([c for c in fake2.calls if c[0] == "SetWindowPos"], [],
           "句柄失效（窗口已销毁）时不碰 SetWindowPos")


def test_标题栏双击是最小化不重复起线程(mc, env, g):
    """拖拽离开 Tk 之后 <Double-Button-1> 本就不可靠，判定继续自己做：
    比对相邻两次按下的 event.time 与系统双击间隔。"""
    with ui(mc, env) as app:
        fake = _FakeCaption()
        real = _use_caption(mc, fake)
        real_thr, started = _no_thread(app)
        iconified = []
        app.root.iconify = lambda: iconified.append(1)
        try:
            app._title_press(_evt(t=1000))                    # 单击：拖
            app._title_press(_evt(t=1000 + app._dbl_ms - 50))  # 间隔内：双击＝最小化
            app._title_press(_evt(t=1000 + app._dbl_ms * 3))   # 很久之后：又是单击
        finally:
            mc._caption_api = real
            threading.Thread = real_thr
        eq(len(iconified), 1, "双击触发一次最小化")
        eq(len(started), 2, "只有两次按下起了拖拽线程，双击那下不重复拖")


def test_拿不到根句柄时退回Tk移动(mc, env, g):
    """兜底路径：连窗口句柄都取不到时，窗口仍要能动，宁可慢也不能动不了。"""
    with ui(mc, env) as app:
        real = _caption_none(mc, app)
        try:
            app._title_press(_evt(t=1000, xr=300, yr=200))
        finally:
            app._root_hwnd = real
        off = app._drag_off
        ok(off is not None, "取不到句柄时记下偏移量")
        got = []
        real_geo = app.root.geometry
        app.root.geometry = lambda spec: got.append(spec)     # root 是 withdraw 的，只能看请求
        try:
            app._drag_move(_evt(t=1100, xr=400, yr=300))
            eq(got, ["+%d+%d" % (400 - off[0], 300 - off[1])], "兜底路径按偏移量搬窗口")
            app._drag_end()
            app._drag_move(_evt(t=1200, xr=900, yr=900))
            eq(len(got), 1, "抬起之后再拖不该继续拽窗口")
        finally:
            app.root.geometry = real_geo


def _caption_none(mc, app):
    """临时让 _root_hwnd 返回 None，模拟句柄取不到。"""
    real = app._root_hwnd
    app._root_hwnd = lambda: None
    return real


def test_导航页签悬停有反馈(mc, env, g):
    """激活页签靠底色区分，非激活页签以前悬停毫无反应，看不出能点。"""
    with ui(mc, env) as app:
        ok(app.nav["settings"].bind("<Enter>"), "绑了 <Enter>")
        ok(app.nav["settings"].bind("<Leave>"), "绑了 <Leave>")
        app._nav_hover("settings", True)
        eq(app.nav["settings"].cget("bg"), app.BTN_HOVER, "悬停给出底色")
        app._nav_hover("settings", False)
        eq(app.nav["settings"].cget("bg"), app.BG, "移出还原")
        app.show_page("settings")
        app._nav_hover("settings", True)
        eq(app.nav["settings"].cget("bg"), app.CARD, "激活页签不被悬停色覆盖")
        eq(app.nav["home"].cget("bg"), app.BG, "让位后的页签回到常态")


def test_明细表整行悬停高亮与还原(mc, env, g):
    """六行表最容易串行看错，整行高亮是最低成本的解法；
    但还原必须精确回原来的斑马纹/合计底色，不能刷成一色。"""
    with ui(mc, env) as app:
        row = app.table_row_widgets["left"]
        base = row[0].cget("bg")
        eq(base, app.CARD, "第一行是白底（无斑马）")
        app._row_hover("left", True)
        eq(set(w.cget("bg") for w in row), {app.BTN_HOVER}, "整行四个单元格一起亮")
        life_cell = app.table_row_widgets["left"][1]
        eq(set(w.cget("bg") for w in life_cell.winfo_children()), {app.BTN_HOVER},
           "寿命列里的圆点和数字也要一起亮，否则留出花斑")
        app._row_hover("left", False)
        eq([w.cget("bg") for w in row], [base] * len(row), "还原成原底色")
        eq(app.table_row_widgets["right"][0].cget("bg"), app.STRIPE, "第二行仍是斑马纹")
        app._row_hover("right", True)
        eq(app.table_row_widgets["right"][0].cget("bg"), app.BTN_HOVER, "悬停照样生效")
        app._row_hover("right", False)
        total = app.table_row_widgets["__total__"]
        eq(total[0].cget("bg"), app.ROW_TOTAL, "合计行蓝底")
        app._row_hover("__total__", True)
        eq(total[0].cget("bg"), app.ROW_TOTAL_HOVER, "合计行用加深一档的悬停色，不被洗白")
        app._row_hover("__total__", False)
        eq(total[0].cget("bg"), app.ROW_TOTAL, "还原")


def test_隐藏行悬停不炸(mc, env, g):
    """侧键被隐藏后 grid_remove 的控件仍可能收到事件，还原逻辑不能抛。"""
    with ui(mc, env) as app:
        prof = mc.current_profile(app.stats)
        prof["hidden"] = ["x1"]
        app.refresh(force=True)
        nothing(lambda: app._row_hover("x1", True), "隐藏行悬停不抛")
        nothing(lambda: app._row_hover("不存在", True), "未知 key 悬停不抛")


def test_热力图例说明量级(mc, env, g):
    """一排深浅圆点没有图例就是天书：圆点=当日点击量级，空心=0 次，「空」=无记录。"""
    with ui(mc, env) as app:
        kids = app.heat_legend.winfo_children()
        dots = [w for w in kids if w.cget("text") == "●"]
        eq(len(dots), 5, "五档色点")
        cols = [w.cget("fg") for w in dots]
        eq(cols, [mc.heat_color(n, app.HEAT_EMPTY)
                  for n in (200, 2000, 4000, 6400, 8000)],
           "色点必须和格子里用的是同一个映射")
        eq(len(set(cols)), 5, "五档要能看出差别（1280 次以下全是同一个绿，取低了会重色）")
        eq(cols[-1], "#c43232", "满档＝红")
        texts = [w.cget("text") for w in kids if w.cget("text") != "●"]
        ok(any("少" in x for x in texts) and any("多" in x for x in texts),
           "左右两端标了少/多")
        ok(not any("每日点击" in x for x in texts),
           "端点只写「少/多」，括号解释是噪音（用户要求简洁）")
        ok(any("空心圆" in x and "无记录" in x and "还没到" in x for x in texts),
           "解释了空心圆、「空」和「—」三种格子")


def test_按钮统一工厂带悬停(mc, env, g):
    """以前「以管理员身份重启」是手写的 tk.Button，样式和另外两个按钮各写一遍，
    改一处漏一处；统一走 _btn 后悬停反馈也一起有了。"""
    with ui(mc, env) as app:
        ok(app.btn_admin.bind("<Enter>"), "管理员按钮也有悬停绑定")
        ok(app.btn_admin.cget("cursor") == "hand2", "手型光标")
        for b in app.home_buttons_frame.winfo_children():
            if isinstance(b, tk.Button):
                ok(b.bind("<Enter>") and b.bind("<Leave>"), "首页按钮全部有悬停绑定")


def test_自绘复选框是实心蓝方块(mc, env, g):
    """tk.Checkbutton 的指示器画对钩、ttk 画叉，同一页两种风格；
    用户要求统一成实心蓝方块，所以指示器改成自绘 Label。"""
    with ui(mc, env) as app:
        hits = []
        var = tk.BooleanVar(value=True)
        row = app._check(app.pages["settings"], "测试", var, lambda: hits.append(1))
        box, lab = row.winfo_children()[:2]
        eq(box.cget("text"), "■", "选中＝实心方块")
        eq(box.cget("fg"), app.ACCENT, "实心方块＝品牌蓝")
        var.set(False)                            # trace 同步触发，不用等事件循环
        eq(box.cget("text"), "□", "未选＝空心方块")
        eq(box.cget("fg"), app.MUTED, "空心方块走中性灰，别抢视觉")
        ok(box.cget("cursor") == "hand2" and lab.cget("cursor") == "hand2", "手型光标")
        ok(bool(lab.bind("<Button-1>")), "点文字也能切换")
        row.toggle()
        eq(var.get(), True, "toggle 切换 BooleanVar")
        eq(hits, [1], "command 被调用一次")
        row.destroy()


def test_设置页复选框全部换成自绘(mc, env, g):
    """整页不能再留原生指示器，否则又是一页两种风格。"""
    with ui(mc, env) as app:
        app.show_page("settings")
        page = app.pages["settings"]
        found = []

        def walk(w):
            for c in w.winfo_children():
                if isinstance(c, (tk.Checkbutton, ttk.Checkbutton)):
                    found.append(c)
                walk(c)
        walk(page)
        eq(found, [], "设置页不该再有 tk/ttk 原生 Checkbutton")


def test_未勾选自启动时状态栏提示(mc, env, g):
    with ui(mc, env) as app:
        app.show_page("settings")
        app.autostart.set(False)
        ok("⚠" in app.autostart_warn.cget("text"), "未开启要有 ⚠ 提示")
        eq(app.autostart_warn.cget("fg"), app.WARN, "提示用警示色而不是红色")
        app.autostart.set(True)
        eq(app.autostart_warn.cget("text"), "", "开启后提示消失")


def test_普通权限时状态栏报风险(mc, env, g):
    """主页底部一栏要直接说清后果，而不是只写「普通权限」让人猜。"""
    with ui(mc, env) as app:
        real = mc.is_elevated
        mc.is_elevated = lambda: False
        try:
            app.update_status()
            txt = app.status["text"]
            ok("普通权限" in txt and "⚠" in txt and "管理员权限" in txt,
               "未提权时写明风险：%s" % txt)
            eq(app.status.cget("fg"), app.WARN, "整条状态栏转警示色")
        finally:
            mc.is_elevated = real
        mc.is_elevated = lambda: True
        try:
            app.update_status()
            txt = app.status["text"]
            ok("⚠" not in txt, "已提权就不要再吓唬人：%s" % txt)
            eq(app.status.cget("fg"), app.MUTED, "正常状态回到中性色")
        finally:
            mc.is_elevated = real


def test_备份摘要标签让错误一眼可见(mc, env, g):
    """备份目录里真出现过文件名正常、内容是合成数据的一份（还有过一次恢复错来源）。
    下拉框只给文件名的话，选错了要等恢复完才知道，所以标签里带点击总量和时间。"""
    with ui(mc, env) as app:
        d = app.stats.backup_dir()
        p = os.path.join(d, "auto_probe.json")
        app.stats.save(force=True)
        mc.shutil.copy2(app.stats.path, p)
        lab = app._backup_label(p)
        ok(lab.startswith("auto_probe.json"), "文件名在最前：%s" % lab)
        ok("点击" in lab and "次" in lab, "带点击总量")
        ok("2026-" in lab and "T" not in lab, "带可读时间戳（T 换成空格）")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{ 这不是 json")
        eq(app._backup_label(p), "auto_probe.json", "坏文件退回纯文件名而不是抛")
        eq(app._backup_label(os.path.join(d, "没有这个文件.json")),
           "没有这个文件.json", "缺失文件也不抛")


def test_恢复对话框列摘要并映射回文件名(mc, env, g):
    """下拉框显示的是摘要标签，真正传给 restore_backup 的必须是文件名。"""
    with ui(mc, env) as app:
        app.stats.save(force=True)
        name = os.path.basename(app.stats.manual_backup())   # manual_backup 返回全路径
        made = []
        real_top = mc.tk.Toplevel

        class Cap(real_top):
            def __init__(self, *a, **k):
                real_top.__init__(self, *a, **k)
                self.withdraw()            # 测试绝不往用户屏幕上弹窗口
                made.append(self)
        mc.tk.Toplevel = Cap
        try:
            app.restore_dialog()
        finally:
            mc.tk.Toplevel = real_top
        eq(len(made), 1, "弹出一个对话框")
        dlg = made[0]
        box = [w for w in dlg.winfo_children() if isinstance(w, ttk.Combobox)][0]
        vals = list(box.cget("values"))
        ok(all("点击" in v and "次" in v for v in vals),
           "每一项都带摘要，不再只给文件名：%s" % vals[:1])
        eq(box.get(), vals[0], "默认选中第一项")
        eq(vals[0], app._backup_label(os.path.join(app.stats.backup_dir(), name)),
           "最新那份排最前，标签与 _backup_label 一致")
        eq(vals[0].split("　")[0], name, "标签首段就是文件名，反查 index 成立")
        dlg.destroy()


def test_备份下拉框按标签实际宽度伸缩(mc, env, g):
    """ttk.Combobox 的 width 单位是「字符」，中文摘要按字符算会算少一半，
    最长那条必须靠像素度量决定框宽，否则尾部时间被裁掉。"""
    import json
    with ui(mc, env) as app:
        app.stats.save(force=True)
        d = app.stats.backup_dir()
        long_name = "手动备份_2026年10月14日 15点13分_" + "格" * 12 + ".json"
        with open(os.path.join(d, long_name), "w", encoding="utf-8") as f:
            json.dump({"version": 3, "days": {t: {"left": 12345} for t in
                                              [mc.today().isoformat()]}}, f)
        made = []
        real_top = mc.tk.Toplevel

        class Cap(real_top):
            def __init__(self, *a, **k):
                real_top.__init__(self, *a, **k)
                self.withdraw()
                made.append(self)
        mc.tk.Toplevel = Cap
        try:
            app.restore_dialog()
        finally:
            mc.tk.Toplevel = real_top
        dlg = made[0]
        box = [w for w in dlg.winfo_children() if isinstance(w, ttk.Combobox)][0]
        vals = list(box.cget("values"))
        fnt = mc.tkfont.Font(family=app.FONT, size=9, root=app.root)
        unit = max(1, fnt.measure("0"))
        need = max(fnt.measure(v) for v in vals)
        ok(box.cget("width") > 54, "长标签把框撑宽了（%d 字符）" % box.cget("width"))
        ok(box.cget("width") * unit >= need, "框宽 %d 字符=%d px ≥ 标签 %d px"
           % (box.cget("width"), box.cget("width") * unit, need))
        ok(box.cget("width") <= 90, "再长也封顶 90 字符，不把手伸到屏幕外")
        dlg.destroy()

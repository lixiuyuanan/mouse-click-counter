# -*- coding: utf-8 -*-
"""数据层回归测试：路径解析、迁移、计数、档案、备份、日志。

历史：v2.0.0~v2.3.0 期间这些用例由 测试\\test_v200.py / test_config_dir.py /
test_log_rotate.py 覆盖并全绿，后来随 测试\\ 目录整体删除（见 文档/项目说明.md）。
本文件按 .ai/verified_commands.md 的记录重建，并补上接手当天发现的缺口。
"""
import datetime
import json
import os

import run_tests

KEYS = ("left", "right", "middle", "x1", "x2", "wheel")
ok, eq, near, raises, nothing = (run_tests.ok, run_tests.eq, run_tests.near,
                                 run_tests.raises, run_tests.nothing)


def env_asserts(mc):
    """兼容旧写法：测试里 ok/eq/near 也可显式取一次。"""
    return run_tests.ok, run_tests.eq, run_tests.near


# ---------------------------------------------------------------- 数字与配色

def test_fmt_缩写(mc, env, g):
    ok, eq, near = env_asserts(mc)
    eq(mc.fmt(9999999), "9,999,999", "千万以下带千分位")
    eq(mc.fmt(10000000), "1000.0 万", "刚过千万用万")
    eq(mc.fmt(34567890), "3456.8 万", "千万级一位小数且不加千分位")
    eq(mc.fmt(99999999), "10000.0 万", "亿以下仍用万")
    eq(mc.fmt(100000000), "1.00 亿", "过亿用亿")
    eq(mc.fmt(123456789), "1.23 亿", "亿保留两位")
    eq(mc.fmt_full(1234567), "1,234,567", "精确值带千分位")


def test_identify_message(mc, env, g):
    ok, eq, near = env_asserts(mc)
    im = mc.identify_message
    eq(im(mc.WM_LBUTTONDOWN, 0), "left", "左键按下")
    eq(im(mc.WM_LBUTTONDBLCLK, 0), "left", "左键双击计 1（不能漏）")
    eq(im(mc.WM_LBUTTONUP, 0), None, "松开不计")
    eq(im(mc.WM_RBUTTONDOWN, 0), "right", "右键")
    eq(im(mc.WM_MBUTTONDBLCLK, 0), "middle", "中键双击")
    eq(im(mc.WM_XBUTTONDOWN, 1 << 16), "x1", "侧键1")
    eq(im(mc.WM_XBUTTONDBLCLK, 2 << 16), "x2", "侧键2")
    eq(im(mc.WM_XBUTTONDOWN, 3 << 16), None, "未知 XBUTTON 不硬塞")
    eq(im(mc.WM_MOUSEWHEEL, 120 << 16), "wheel", "向上滚")
    eq(im(mc.WM_MOUSEWHEEL, 30 << 16), "wheel", "高清滚轮的小 delta 也要识别出来")
    eq(im(mc.WM_MOUSEWHEEL, ((-120) & 0xFFFF) << 16), "wheel", "向下滚（负 delta）")
    eq(im(mc.WM_MOUSEWHEEL, 0), None, "delta 为 0 不计")
    eq(im(0x0200, 0), None, "鼠标移动不计数")
    for msg in (mc.WM_LBUTTONDOWN, mc.WM_LBUTTONDBLCLK, mc.WM_RBUTTONDOWN,
                mc.WM_MBUTTONDBLCLK, mc.WM_XBUTTONDOWN, mc.WM_XBUTTONDBLCLK,
                mc.WM_MOUSEWHEEL):
        ok(msg in mc.COUNT_MSG_IDS, "钩子必须放行消息 0x%04X" % msg)
    ok(mc.WM_LBUTTONUP not in mc.COUNT_MSG_IDS, "松开消息不该进回调")


def test_wheel_ticks_累积进位(mc, env, g):
    """口径修正：一格 = 120 delta，不足一格进位、多余量留到下条消息。"""
    ok, eq, near = env_asserts(mc)
    ok(hasattr(mc, "wheel_ticks"), "缺 wheel_ticks()：滚轮仍按每条消息计 1")
    wt = mc.wheel_ticks
    eq(wt(120, 0), (1, 0), "标准一格")
    eq(wt(240, 0), (2, 0), "快速滚两格只记 1 是旧 bug")
    eq(wt(480, 0), (4, 0), "惯性滚四格")
    eq(wt(30, 0), (0, 30), "高清滚轮不足一格先累积")
    eq(wt(30, 90), (1, 0), "累积满 120 进位")
    eq(wt(0, 50), (0, 50), "delta 0 不消耗残量")


def test_滚轮残量跨消息累积(mc, env, g):
    ok, eq, near = env_asserts(mc)
    s = env.new_stats(mc)
    s.set_setting("last_auto_backup", mc.today().isoformat())
    eq(s.add_wheel_delta(30), 0, "30 delta 不足一格，先不计数")
    eq(s.add_wheel_delta(40), 0, "30+40 还差 50")
    eq(s.add_wheel_delta(50), 1, "凑满 120 → 记 1 格")
    eq(s.add_wheel_delta(250), 2, "250 = 2 格，余 10 继续攒")
    eq(s.day_row(mc.today())["wheel"], 3, "今天累计 3 格")
    eq(s.wheel_residual, 10, "残量留在内存")
    s.save(force=True)
    again = mc.Stats(mc.DATA_FILE)
    eq(again.day_row(mc.today())["wheel"], 3, "落盘重读一致（残量不写盘）")
    eq(again.wheel_residual, 0, "重启从 0 开始攒，最多丢半格")


def test_life_color_渐变(mc, env, g):
    ok, eq, near = env_asserts(mc)
    eq(mc.life_color(0), "#22c55e", "0% 绿")
    eq(mc.life_color(40), "#22c55e", "40% 仍是纯绿（锚点）")
    eq(mc.life_color(70), "#eab308", "70% 纯黄")
    eq(mc.life_color(90), "#ea580c", "90% 纯橙")
    eq(mc.life_color(100), "#dc2626", "100% 红")
    eq(mc.life_color(300), "#dc2626", "超寿锁红")
    r1, r2 = mc.life_color(50), mc.life_color(60)
    ok(r1 != r2, "50% 与 60% 应有差别")
    ok(int(r2[1:3], 16) > int(r1[1:3], 16), "越接近黄 → 红分量递增")
    ok(int(r2[3:5], 16) < int(r1[3:5], 16), "越接近黄 → 绿分量递减")
    ok(int(mc.life_color(80)[3:5], 16) < int(mc.life_color(75)[3:5], 16),
       "70~90 段往橙走 → 绿分量继续降")


def test_weighted_life_pct(mc, env, g):
    ok, eq, near = env_asserts(mc)
    near(mc.weighted_life_pct([(1000, 10.0), (500, 20.0), (100, 30.0)]),
         16.0, "TOP1 0.6 + TOP2 0.2 + TOP3 0.2")
    near(mc.weighted_life_pct([(1, 90.0)]), 90.0, "单项归一化")
    near(mc.weighted_life_pct([(9, 10.0), (1, 50.0)]), 20.0,
         "两项时权重归一（0.6×10+0.2×50）/0.8")
    eq(mc.weighted_life_pct([]), None, "空 → None")
    eq(mc.weighted_life_pct([(5, None)]), None, "无有效寿命 → None")
    near(mc.weighted_life_pct([(1, 10.0), (100, 20.0)]), 17.5,
         "权重跟用量走，不跟百分比走")


def test_heat_color(mc, env, g):
    ok, eq, near = env_asserts(mc)
    eq(mc.heat_color(0), "#eef1f5", "0 次用空色，不进渐变")
    eq(mc.heat_color(8000), "#c43232", "8000 → 降饱和 20% 的红")
    eq(mc.heat_color(99999), "#c43232", "超阈值锁红")
    near(mc.heat_x(8000), 100.0, "8000 满刻度")
    near(mc.heat_x(4000), 70.71, "sqrt 拉伸", tol=0.01)
    near(mc.heat_x(0), 0.0, "零 = 0")
    ok(mc.heat_color(1) != mc.heat_color(0), "1 次也该有颜色（低值要有层次）")
    ok(mc.heat_color(200) != mc.heat_color(1800), "低值段要能分出深浅")


def test_mix_desaturate(mc, env, g):
    ok, eq, near = env_asserts(mc)
    eq(mc.mix_color("#000000", "#ffffff", 0.5), "#808080", "对半混")
    eq(mc.mix_color("#ff0000", "#0000ff", 0.0), "#ff0000", "f=0 保持原色")
    eq(mc.mix_color("bad", "#000000", 0.5), "bad", "非法输入回退不崩")
    eq(mc.desaturate("#ff0000", 0.0), "#ff0000", "f=0 不降饱和")
    eq(mc.desaturate("#c43232", 1.0), "#636363", "f=1 变灰（灰度=通道均值取整）")
    eq(mc.desaturate("#dc2626", 0.2), "#c43232", "红降饱和 20% = 热力红（v2.4.1 锚点）")


def test_system_dark_mode_兜底(mc, env, g):
    ok, eq, near = env_asserts(mc)
    ok(mc.system_dark_mode() in (True, False), "返回布尔，注册表读不到也不抛")


# ---------------------------------------------------------------- 路径与迁移

def test_data_search_dirs_顺序与去重(mc, env, g):
    ok, eq, near = env_asserts(mc)
    real = g.saved["_data_search_dirs"]
    dirs = real()
    ok(len(dirs) >= 5, "至少 5 个候选：%d" % len(dirs))
    ok(len(dirs) == len(set(dirs)), "不应有重复目录")
    ok(any("鼠标点击次数记录" == os.path.basename(d) for d in dirs),
       "候选目录名应是「鼠标点击次数记录」")
    docs = mc._documents_dir()
    if docs:
        eq(dirs[0], os.path.join(docs, mc.DATA_FOLDER_NAME),
           "首位必须是用户文档（OneDrive 重定向也要跟上）")
    ok(all(os.path.isabs(d) for d in dirs), "全部是绝对路径")


def test_resolve_data_dir_逐级回退(mc, env, g):
    """文档不可写时必须退到用户目录，一个都不可写才用兜底值。"""
    ok, eq, near = env_asserts(mc)
    import tempfile
    a = os.path.realpath(tempfile.mkdtemp(prefix="mct_a_"))
    b = os.path.realpath(tempfile.mkdtemp(prefix="mct_b_"))
    orig = mc._dir_writable
    mc._data_search_dirs = lambda: [a, b]
    mc._dir_writable = lambda p: p == b
    try:
        eq(mc._resolve_data_dir("fallback"), b, "第一不可写就退到第二")
        mc._data_search_dirs = lambda: [a]
        eq(mc._resolve_data_dir(b), b, "全部不可写用兜底")
    finally:
        mc._dir_writable = orig
        import shutil
        shutil.rmtree(a, ignore_errors=True)
        shutil.rmtree(b, ignore_errors=True)


def test_documents_dir_是真实路径(mc, env, g):
    ok, eq, near = env_asserts(mc)
    docs = mc._documents_dir()
    ok(docs is None or os.path.isabs(docs), "SHGetKnownFolderPath 取到绝对路径或 None")
    if docs:
        ok(os.path.isdir(docs), "文档目录必须存在：%s" % docs)


def test_legacy_文件名自动迁移(mc, env, g):
    """旧名 data.json / 点击数据记录（勿删！！！）.json 要能被找到并搬走。"""
    ok, eq, near = env_asserts(mc)
    old_dir = os.path.join(g.tmp, "oldplace")
    os.makedirs(old_dir)
    mc._data_search_dirs = lambda: [old_dir]      # 覆盖 env 的 []，模拟真实旧位置
    for name in mc.LEGACY_DATA_NAMES:
        src = os.path.join(old_dir, name)
        with open(src, "w", encoding="utf-8") as f:
            json.dump({"version": 3, "settings": {}, "days": {"2026-10-01":
                      dict(zip(KEYS, [7, 0, 0, 0, 0, 0]))}}, f)
        s = mc.Stats(mc.DATA_FILE)
        eq(s.total["left"], 7, "从旧文件 %s 读到数据" % name)
        ok(os.path.exists(mc.DATA_FILE), "已落到新位置")
        ok(not os.path.exists(src), "旧文件被搬走不留两份")
        os.remove(mc.DATA_FILE)
        s = mc.Stats(mc.DATA_FILE)
        eq(s.total["left"], 0, "清干净以备下一轮")


def test_迁移时跨目录不丢字段(mc, env, g):
    ok, eq, near = env_asserts(mc)
    old_dir = os.path.join(g.tmp, "legacy")
    os.makedirs(old_dir)
    raw = {"version": 2, "settings": {"profile_id": "p2", "autostart": True,
                                      "profiles": [{"id": "p2", "name": "旧鼠"}]},
           "days": {"2026-10-01": {"left": 300, "wheel_up": 40, "wheel_down": 15}}}
    with open(os.path.join(old_dir, "data.json"), "w", encoding="utf-8") as f:
        json.dump(raw, f)
    mc._data_search_dirs = lambda: [old_dir]
    s = mc.Stats(mc.DATA_FILE)
    eq(s.settings.get("profile_id"), "p2", "保留原档案选择")
    eq(s.total["wheel"], 55, "v1 分体滚轮合并进 wheel")
    eq(s.total["left"], 300, "点击数不丢")
    eq(s.payload()["version"], 3, "写出时升级为 version=3")


def test_v2_顶层_days_归入当前档案(mc, env, g):
    ok, eq, near = env_asserts(mc)
    raw = {"version": 2,
           "settings": {"profile_id": "p2",
                        "profiles": [{"id": "p1", "name": "A"}, {"id": "p2", "name": "B"}]},
           "days": {"2026-10-05": {"left": 11, "right": 2}}}
    s = env.new_stats(mc, raw)
    eq(s.settings["profile_id"], "p2", "仍在 p2")
    eq(s.total["left"], 11, "历史归入升级前的当前档案")
    eq(s.profile_days["p1"], {}, "其它档案从 0 开始，不做无法追溯的拆分")
    ok(s.dirty, "迁移后置脏，等待落盘升级版本")
    s.save(force=True)
    again = mc.Stats(mc.DATA_FILE)
    eq(again.total["left"], 11, "重读后数据一致")
    eq(again.payload()["version"], 3, "已升级 version=3")


def test_坏值不炸整个文件(mc, env, g):
    """合法 JSON、个别值类型错：坏值记 0，其它数据必须保住。

    接手当天实测：apply_payload 在 Stats._load 的 try 之外，一个 "abc"
    就让 ValueError 直穿 main()，打包版 --noconsole 无声退出，用户以为软件坏了。
    """
    ok, eq, near = env_asserts(mc)
    raw = {"version": 3, "settings": {"profile_id": "p1",
                                      "profiles": [{"id": "p1", "name": "M"}]},
           "profile_days": {"p1": {
               "2026-10-01": {"left": "abc", "right": 42},
               "2026-10-02": {"left": 8, "right": None},
               "2026-10-03": "整行都是垃圾",
               "2026-10-04": {"left": 5.9, "right": "12"}}}}
    s = env.new_stats(mc, raw)
    eq(s.days["2026-10-01"]["right"], 42, "好数据要保住")
    eq(s.days["2026-10-01"]["left"], 0, "坏值按 0")
    eq(s.days["2026-10-04"]["left"], 5, "浮点向下取整")
    eq(s.days["2026-10-04"]["right"], 12, "数字字符串要能用")
    eq(s.days["2026-10-02"]["right"], 0, "None 按 0")
    ok("2026-10-03" not in s.days, "整行垃圾丢弃")
    ok(not os.path.exists(mc.DATA_FILE + ".bak"), "能局部补救就不该隔离整个文件")


def test_坏文件隔离为_bak(mc, env, g):
    ok, eq, near = env_asserts(mc)
    with open(mc.DATA_FILE, "w", encoding="utf-8") as f:
        f.write('{"version": 3, "settings": {"profiles"')      # 截断的 JSON
    s = env.new_stats(mc)
    eq(s.total["left"], 0, "读不到就当空，程序照常起")
    ok(os.path.exists(mc.DATA_FILE + ".bak"), "坏文件已改名保留，不静默丢弃")
    ok(os.path.exists(mc.LOG_FILE), "写了 error.log")
    s.add("left")
    s.save(force=True)
    ok(os.path.exists(mc.DATA_FILE), "坏文件隔离后仍能写新数据")


def test_结构异常文件不炸(mc, env, g):
    ok, eq, near = env_asserts(mc)
    cases = [
        {"version": 3, "settings": {"profiles": "不是列表"}},
        {"version": 3, "settings": {"profiles": [{"id": 1, "name": None}]}},
        {"version": 3, "settings": {"profiles": [None, 5, "x"], "profile_id": 7}},
        {"version": 3, "settings": None, "profile_days": None},
        {"version": 3, "settings": [], "days": []},
        {"version": 3, "profile_days": {"p1": {"2026-10-01": [1, 2, 3]}}},
        {"version": 3, "profile_days": [1, 2]},
        {},
        [],
    ]
    for raw in cases:
        with open(mc.DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(raw, f)
        s = env.new_stats(mc)
        eq(s.click_sum(s.day_row(mc.today())), 0, "结构异常 %s 应回退到空数据" % str(raw)[:36])


# ---------------------------------------------------------------- 计数与档案

def test_基本计数与原子落盘(mc, env, g):
    ok, eq, near = env_asserts(mc)
    s = env.new_stats(mc)
    s.set_setting("last_auto_backup", mc.today().isoformat())   # 自动备份会再置脏，先屏蔽
    for _ in range(3):
        s.add("left")
    s.add("wheel")
    t = mc.today().isoformat()
    eq(s.days[t]["left"], 3, "今日左键")
    eq(s.days[t]["wheel"], 1, "今日滚轮")
    eq(s.total["left"], 3, "总计同步")
    ok(s.dirty, "改动置脏")
    s.save(force=True)
    eq(os.path.exists(s.path + ".tmp"), False, "不留 .tmp 残file")
    ok(s.dirty is False, "落盘后清脏")
    again = mc.Stats(mc.DATA_FILE)
    eq(again.days[t]["left"], 3, "重读一致")
    eq(again.payload()["app"], mc.APP_TITLE, "文件里带程序名")


def test_不脏就不写盘(mc, env, g):
    ok, eq, near = env_asserts(mc)
    s = env.new_stats(mc)
    s.set_setting("last_auto_backup", mc.today().isoformat())   # 避开自动备份再置脏
    s.add("left")
    s.save()
    ok(not s.dirty, "落盘后应为干净")
    m1 = os.path.getmtime(s.path)
    s.save()
    eq(os.path.getmtime(s.path), m1, "没改动就不重复写盘")
    s.set_setting("theme", "dark")
    ok(s.dirty, "改设置置脏")
    s.set_setting("theme", "dark")
    eq(s.get_setting("theme"), "dark", "重复设同值不再置脏")


def test_click_sum_口径与区间(mc, env, g):
    ok, eq, near = env_asserts(mc)
    t = mc.today()
    s = env.new_stats(mc)
    pid = mc.current_profile(s)["id"]
    s.profile_days[pid] = {
        (t - datetime.timedelta(days=1)).isoformat():
            {"left": 100, "right": 10, "middle": 1, "x1": 2, "x2": 3, "wheel": 9999},
        t.isoformat(): {"left": 5, "right": 1, "middle": 0, "x1": 0, "x2": 0, "wheel": 7}}
    s.select_profile(pid)
    eq(s.click_sum(s.day_row(t)), 6, "合计只算 5 个点击键，滚轮排除")
    eq(s.sum_range(t - datetime.timedelta(days=1), t)["wheel"], 10006, "滚轮单独求和")
    eq(s.click_sum(s.sum_range(t - datetime.timedelta(days=1), t)), 122, "两日点击合计")
    eq(s.recorded_days(), 2, "有点击的天数")
    eq(s.click_sum(s.sum_range(t - datetime.timedelta(days=9),
                               t - datetime.timedelta(days=2))), 0, "空区间为 0")
    s.profile_days[pid]["2026-9-30"] = {"left": 0, "right": 0, "wheel": 500}
    s.select_profile(pid)
    eq(s.recorded_days(), 2, "只有滚轮的一天不算记录日")


def test_档案独立计数隔离(mc, env, g):
    ok, eq, near = env_asserts(mc)
    t = mc.today()
    s = env.new_stats(mc, {"version": 3, "settings": {
        "profile_id": "p1",
        "profiles": [{"id": "p1", "name": "A"}, {"id": "p2", "name": "B"}]},
        "profile_days": {"p1": {t.isoformat(): {"left": 7}},
                         "p2": {t.isoformat(): {"left": 40}}}})
    eq(s.total["left"], 7, "当前档案 A")
    p = mc.ensure_profiles(s)
    s.select_profile("p2")
    eq(s.total["left"], 40, "切到 B 后总计独立")
    eq(len(s.days), 1, "A 的历史不串到 B")
    s.add("left")
    eq(s.profile_days["p2"][t.isoformat()]["left"], 41, "写入落在当前档案")
    eq(len(s.profile_days["p1"][t.isoformat()]), len(KEYS),
       "另一档案未被污染（读取时补齐成 6 键，值仍是 7）")
    eq(s.profile_days["p1"][t.isoformat()]["left"], 7, "A 的左键没被 B 的写入改动")
    s.select_profile("p1")
    eq(s.total["left"], 7, "切回来仍是 A")


def test_档案列表写回_v2bug回归(mc, env, g):
    """曾因 ensure_profiles 过滤出副本未写回，新建档案永远不显示（用户实测）。"""
    ok, eq, near = env_asserts(mc)
    s = env.new_stats(mc, {"version": 3,
                          "settings": {"profiles": [{"id": "p1", "name": "A"},
                                                    {"id": "bad"}, {"name": "无ID"}]}})
    p = mc.ensure_profiles(s)
    eq(len(p), 1, "非法档案被过滤")
    ok(p is s.settings["profiles"], "返回的必须是 settings 里同一个对象")
    p.append({"id": "p9", "name": "新鼠", "switch": "", "added": "2026-10-14",
              "since": "2026-10-14", "life": {}, "labels": {}, "hidden": []})
    s.save(force=True)
    again = mc.Stats(mc.DATA_FILE)
    eq(len(mc.ensure_profiles(again)), 2, "重读后新档案还在")


def test_默认档案_since_取最早记录(mc, env, g):
    ok, eq, near = env_asserts(mc)
    s = env.new_stats(mc, {"version": 2, "settings": {},
                          "days": {"2026-09-28": {"left": 1},
                                   "2026-10-06": {"left": 2}}})
    p = mc.ensure_profiles(s)[0]
    eq(p["since"], "2026-09-28", "升级后寿命用量不从零开始")
    eq(p["name"], "默认鼠标", "自动建默认档案")
    eq(s.settings["profile_id"], p["id"], "profile_id 指向真实存在的档案")


def test_profile_id_失效自愈(mc, env, g):
    ok, eq, near = env_asserts(mc)
    s = env.new_stats(mc, {"version": 3, "settings": {
        "profile_id": "不存在",
        "profiles": [{"id": "p1", "name": "A"}, {"id": "p2", "name": "B"}]},
        "profile_days": {"p1": {}, "p2": {}}})
    eq(s.settings["profile_id"], "p1", "回落到第一个档案")
    eq(mc.current_profile(s)["id"], "p1", "current_profile 一致")


def test_显隐与重命名规则(mc, env, g):
    ok, eq, near = env_asserts(mc)
    prof = {"id": "p1", "name": "A", "labels": {"x1": "前进"}, "hidden": ["x1", "x2", "left"]}
    eq(mc.profile_hidden(prof, "left"), False, "左中右固定不可隐藏")
    eq(mc.profile_hidden(prof, "middle"), False, "中键不可隐藏")
    eq(mc.profile_hidden(prof, "x1"), True, "侧键可隐藏")
    eq(mc.profile_label(prof, "x1"), "前进", "用自定义名")
    eq(mc.profile_label(prof, "x2"), "侧键2", "没自定义就用默认")
    eq(mc.visible_click_keys(prof), ["left", "right", "middle"], "可见点击键")
    eq(mc.visible_keys(prof), ["left", "right", "middle", "wheel"], "滚轮恒可见")


# ---------------------------------------------------------------- 备份恢复

def test_自动备份每天一份留七份(mc, env, g):
    ok, eq, near = env_asserts(mc)
    s = env.new_stats(mc)
    bdir = s.backup_dir()
    ok(os.path.isdir(bdir), "备份目录已创建")
    for i in range(1, 9):
        with open(os.path.join(bdir, "auto_2026-09-%02d.json" % i), "w") as f:
            f.write("{}")
    s.set_setting("last_auto_backup", "2000-01-01")
    s.add("left")
    s.save()
    autos = sorted(x for x in os.listdir(bdir) if x.startswith("auto_"))
    eq(len(autos), 7, "只留最近 7 份")
    ok(("auto_%s.json" % mc.today().isoformat()) in autos, "今天的在里面")
    ok("auto_2026-09-01.json" not in autos and "auto_2026-09-02.json" not in autos,
       "删的是最旧的两份")
    eq(s.get_setting("last_auto_backup"), mc.today().isoformat(), "记下今天已备份")
    n = len(os.listdir(bdir))
    s.save(force=True)
    eq(len(os.listdir(bdir)), n, "同一天不再重复备份")


def test_备份列表按时间而非文件名(mc, env, g):
    """list_backups 原先按名字倒序：手动备份_* 永远排在 auto_* 前面，
    「从备份恢复」默认项因此不是最新那份。"""
    ok, eq, near = env_asserts(mc)
    bdir = s0 = env.new_stats(mc).backup_dir()
    old = os.path.join(bdir, "手动备份_20261004-034144.json")
    new = os.path.join(bdir, "auto_%s.json" % mc.today().isoformat())
    for p in (old, new):
        with open(p, "w", encoding="utf-8") as f:
            f.write("{}")
    os.utime(old, (1600000000, 1600000000))          # 2020-09，明显更旧
    s = mc.Stats(mc.DATA_FILE)
    d, names = s.list_backups()
    eq(d, bdir, "返回备份目录")
    eq(names[0], os.path.basename(new), "最新的必须排第一")
    eq(len(names), 2, "两份都在")


def test_恢复往返与前置备份(mc, env, g):
    """恢复要能回到旧数据，且恢复前那份保护性备份不能被覆盖。

    manual_backup 的文件名只精确到秒：同一秒内两次备份会写成同一个文件名，
    「恢复前自动备份」因此会抹掉用户刚点的那份手动备份 —— 必须去重。
    """
    ok, eq, near = env_asserts(mc)
    s = env.new_stats(mc)
    t = mc.today().isoformat()
    s.profile_days[mc.current_profile(s)["id"]] = {t: {"left": 500}}
    s.select_profile(mc.current_profile(s)["id"])
    s.set_setting("profile_id", mc.current_profile(s)["id"])
    s.save(force=True)
    dest = s.manual_backup()
    ok(os.path.exists(dest), "手动备份落盘")
    ok(os.path.basename(dest).startswith("手动备份_"), "文件名带时间戳")
    s.profile_days[mc.current_profile(s)["id"]][t] = {"left": 1}
    s.select_profile(mc.current_profile(s)["id"])
    s.dirty = True
    s.save(force=True)
    eq(s.total["left"], 1, "已改动")
    total = s.restore_backup(os.path.basename(dest))
    eq(total, 500, "恢复返回点击总计")
    eq(s.total["left"], 500, "数据回来了")
    names = os.listdir(s.backup_dir())
    ok(sum(1 for x in names if x.startswith("手动备份_")) >= 2,
       "恢复前自动再备份一次，出错还能回去")


def test_恢复拒绝坏备份(mc, env, g):
    ok, eq, near = env_asserts(mc)
    s = env.new_stats(mc)
    s.save(force=True)
    with open(os.path.join(s.backup_dir(), "坏备份.json"), "w", encoding="utf-8") as f:
        f.write('{"app": "x"}')
    raises(ValueError, lambda: s.restore_backup("坏备份.json"), "缺 days/profile_days 必须拒绝")
    with open(os.path.join(s.backup_dir(), "非json.json"), "w", encoding="utf-8") as f:
        f.write("not json at all")
    raises(Exception, lambda: s.restore_backup("非json.json"), "解析失败要抛")


def test_备份位置可改且不可写时回退(mc, env, g):
    ok, eq, near = env_asserts(mc)
    s = env.new_stats(mc)
    other = os.path.join(g.tmp, "mybackup")
    s.set_setting("backup_dir", other)
    eq(s.backup_dir(), other, "用自定义位置")
    s.set_setting("backup_dir", "Z:\\不存在\\的路径")
    ok(s.backup_dir().endswith(os.path.join("backups")) or os.path.isdir(s.backup_dir()),
       "自定义不可用时回退默认，不抛")
    s.set_setting("backup_dir", None)
    ok(s.backup_dir().startswith(g.tmp), "清空回到临时 APP_DIR\\backups")


# ---------------------------------------------------------------- 日志

def test_日志轮转保留尾部(mc, env, g):
    ok, eq, near = env_asserts(mc)
    p = os.path.join(g.tmp, "t.log")
    with open(p, "w", encoding="utf-8") as f:
        for i in range(500):
            f.write("line %d %s\n" % (i, "x" * 40))
    size = os.path.getsize(p)
    mc._rotate(p, size // 2, 2000)
    ok(os.path.getsize(p) < size, "已缩小")
    data = open(p, encoding="utf-8").read()
    ok("line 499" in data, "最近的行还在（最有价值的诊断信息）")
    ok("line 0 " not in data, "最早的已被裁掉")
    ok(data.startswith("[日志已轮转"), "带轮转标记")
    n = os.path.getsize(p)
    mc._rotate(p, n * 10, 100)
    eq(os.path.getsize(p), n, "没超限就不动")


def test_log_与_tray_log_可写(mc, env, g):
    ok, eq, near = env_asserts(mc)
    mc.log("一条错误")
    mc.tray_log("一条托盘")
    ok(open(mc.LOG_FILE, encoding="utf-8").read().strip().endswith("一条错误"),
       "error.log 内容正确")
    ok("一条托盘" in open(mc.TRAY_LOG, encoding="utf-8").read(), "tray.log 内容正确")
    ok(open(mc.LOG_FILE, encoding="utf-8").read().startswith("20"), "带时间戳")


def test_计划任务XML转义(mc, env, g):
    """路径里的 & 不转义会让 schtasks 报「文档打开失败」，自启动静默失效。"""
    ok, eq, near = env_asserts(mc)
    import xml.etree.ElementTree as ET
    eq(mc._xml_text("A&B<C>D"), "A&amp;B&lt;C&gt;D", "三个字符都要转义")
    eq(mc._xml_text(123), "123", "非字符串也能过")
    real = mc.task_run_command
    mc.task_run_command = lambda: (r"C:\R&D\鼠标点击计数V2.4.2.exe", r"C:\R&D")
    try:
        xml = mc.task_xml()
    finally:
        mc.task_run_command = real
    nothing(lambda: ET.fromstring(xml), "转义后的 XML 必须能被解析")
    ok("R&amp;D" in xml, "路径确实被转义了")
    ok("鼠标点击计数" in xml, "中文原样保留（UTF-16 写出，不需要转义）")


def test_life_percent_边界(mc, env, g):
    ok, eq, near = env_asserts(mc)
    eq(mc.life_percent(100, 0), None, "寿命上限 0 → 不统计")
    eq(mc.life_percent(100, -5), None, "负数上限 → 不统计")
    eq(mc.life_percent(100, None), None, "None 上限")
    eq(mc.life_percent(100, "x"), None, "非数字上限")
    near(mc.life_percent(100, 400), 25.0, "正常计算")
    near(mc.life_percent(100, "400"), 25.0, "数字字符串上限也能用")


def env_helpers_defaults(mc, env, g):
    """占位：确保默认寿命覆盖全部 6 键（不是 test_ 开头，runner 不跑）。"""
    pass


def test_默认寿命覆盖所有键(mc, env, g):
    ok, eq, near = env_asserts(mc)
    eq(sorted(mc.DEFAULT_LIFE), sorted(KEYS), "DEFAULT_LIFE 覆盖 5 键 + 滚轮")
    ok(all(v > 0 for v in mc.DEFAULT_LIFE.values()), "默认寿命都是正数")
    eq(sorted(mc.KEYS), sorted(list(KEYS)), "键集合固定")
    eq(mc.CLICK_KEYS, ["left", "right", "middle", "x1", "x2"], "点击键顺序即表格顺序")
    eq(mc.DATA_VERSION, 3, "数据版本")

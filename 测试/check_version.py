# -*- coding: utf-8 -*-
"""版本号一致性自检：源码 APP_VERSION 与 version_info.txt / spec 必须同步。

发版要改的地方散在 3 个文件里（历史 更新日志.md 记过一次「界面显示 v2.4.0、
文件属性还是 2.3.0」），这条脚本把它变成机器约束。

跑法：python -X utf8 测试/check_version.py
退出码 0＝一致，1＝有漏改。
注意：本次版本的 spec 入库在 `构建\\`，所以全新克隆也能过这道检查；
`_pkg_tmp\\` 里那份是 PyInstaller 每次重新生成的副本，命中它同样算通过。
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import mouse_counter as mc                                   # noqa: E402


def main():
    ver = mc.APP_VERSION
    problems = []
    vi = io.open(os.path.join(ROOT, "version_info.txt"), encoding="utf-8").read()
    for field in ("filevers", "prodvers"):
        m = re.search(r"%s=\((\d+), (\d+), (\d+), (\d+)\)" % field, vi)
        if not m:
            problems.append("version_info.txt 找不到 %s" % field)
            continue
        got = ".".join(m.group(i) for i in (1, 2, 3))
        if got != ver:
            problems.append("version_info.txt %s=%s ≠ APP_VERSION=%s" % (field, got, ver))
    for field in ("FileVersion", "ProductVersion"):
        m = re.search(r"StringStruct\('%s', '([^']*)'\)" % field, vi)
        if not m or m.group(1) != ver:
            problems.append("version_info.txt %s=%r ≠ %s" %
                            (field, m.group(1) if m else None, ver))
    # spec 历史上进过项目根目录、_pkg_tmp（构建产物）和 构建\（本次起入库的那份）
    spec = next((p for p in (os.path.join(ROOT, "构建", "鼠标点击计数V%s.spec" % ver),
                             os.path.join(ROOT, "_pkg_tmp",
                                          "鼠标点击计数V%s.spec" % ver),
                             os.path.join(ROOT, "鼠标点击计数V%s.spec" % ver))
                 if os.path.exists(p)), None)
    if spec is None:
        problems.append("缺少本次版本的 spec 文件：构建\\鼠标点击计数V%s.spec" % ver)
    else:
        s = io.open(spec, encoding="utf-8").read()
        if ver not in s:
            problems.append("spec 里没有 %s 字样（--name / version-file 可能没改）" % ver)
    for p in problems:
        print("  FAIL  %s" % p)
    if problems:
        print("\nVERSION MISMATCH (APP_VERSION=%s)" % ver)
        return 1
    print("  OK    APP_VERSION=%s，version_info.txt 四处与 spec 文件名一致" % ver)
    print("\nVERSION_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

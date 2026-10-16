# -*- coding: utf-8 -*-
"""极简测试运行器（纯标准库，不引入 pytest）。

用法：
    python -X utf8 测试/run_tests.py            # 全跑
    python -X utf8 测试/run_tests.py data       # 只跑 测试/test_data.py
    python -X utf8 测试/run_tests.py ui         # 只跑 测试/test_ui.py

约定：测试函数名以 test_ 开头；失败抛 AssertionError；
每个函数独立运行在 `env.isolate()` 提供的临时目录里。
"""
import importlib
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import env                                                   # noqa: E402
import mouse_counter as mc                                   # noqa: E402


class Fail(AssertionError):
    pass


def ok(cond, msg):
    if not cond:
        raise Fail(msg)


def eq(a, b, msg):
    if a != b:
        raise Fail("%s：期望 %r，实际 %r" % (msg, b, a))


def near(a, b, msg, tol=1e-6):
    if abs(a - b) > tol:
        raise Fail("%s：期望约 %r，实际 %r" % (msg, b, a))


def raises(exc, fn, msg):
    try:
        fn()
    except exc:
        return True
    except Exception as e:
        raise Fail("%s：应抛 %s，实际抛了 %r" % (msg, exc.__name__, e))
    raise Fail("%s：应抛 %s，实际没有抛异常" % (msg, exc.__name__))


def nothing(fn, msg):
    try:
        fn()
    except Exception as e:
        raise Fail("%s：不应抛异常，实际 %s: %r" % (msg, type(e).__name__, e))
    return True


MODULES = {"data": "test_data", "ui": "test_ui"}


def run(which=None):
    names = [MODULES[which]] if which else list(MODULES.values())
    passed = failed = 0
    problems = []
    for mod_name in names:
        mod = importlib.import_module(mod_name)
        fns = sorted(n for n in dir(mod) if n.startswith("test_"))
        print("\n=== %s（%d 项）===" % (mod_name, len(fns)))
        for fn_name in fns:
            fn = getattr(mod, fn_name)
            guard = env.isolate(mc)
            try:
                fn(mc, env, guard)
                print("  PASS  %s" % (fn.__doc__ or fn_name))
                passed += 1
            except Exception as exc:
                tb = traceback.format_exc().strip().splitlines()
                line = tb[-1] if not isinstance(exc, Fail) else str(exc)
                print("  FAIL  %s\n        %s" % (fn.__doc__ or fn_name, line))
                failed += 1
                problems.append((mod_name, fn_name, "\n".join(tb[-4:])))
            finally:
                guard.close()
    print("\n----------------------------------------")
    print("通过 %d，失败 %d" % (passed, failed))
    if problems:
        print("\n失败详情：")
        for mod_name, fn_name, tb in problems:
            print("\n### %s.%s\n%s" % (mod_name, fn_name, tb))
    return failed


if __name__ == "__main__":
    sys.exit(1 if run(sys.argv[1] if len(sys.argv) > 1 else None) else 0)

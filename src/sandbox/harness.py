"""TRUST-CODE sandbox harness.

This file is copied *into* every isolated workspace and executed there as a separate
process (locally or inside a Docker/gVisor container). It must stay self-contained:
standard library only (pytest is used when present, otherwise a built-in runner).

    python harness.py <mode> <input.json> <output.json>

Modes
    tests  — run the test suite, recording outcome + executed lines per test
    tasks  — evaluate requirement checks, bounded proofs and behaviour probes
"""
import ast
import importlib
import itertools
import json
import os
import random
import sys
import threading
import time
import traceback
import types

ROOT = os.path.abspath(os.getcwd())
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.dont_write_bytecode = True


# ============================================================ line tracing
class LineTracer:
    def __init__(self, root):
        self.root = os.path.normcase(root) + os.sep
        self.cache = {}
        self.lines = {}

    def _rel(self, filename):
        r = self.cache.get(filename)
        if r is None:
            a = os.path.normcase(os.path.abspath(filename))
            if a.startswith(self.root) and (os.sep + ".trustcode" + os.sep) not in a:
                r = os.path.relpath(os.path.abspath(filename), ROOT).replace(os.sep, "/")
            else:
                r = ""
            self.cache[filename] = r
        return r

    def _global(self, frame, event, arg):
        rel = self._rel(frame.f_code.co_filename)
        if not rel:
            return None
        bucket = self.lines.setdefault(rel, set())
        bucket.add(frame.f_lineno)

        def local(f, ev, a):
            if ev == "line":
                bucket.add(f.f_lineno)
            return local

        return local

    def start(self):
        self.lines = {}
        sys.settrace(self._global)
        threading.settrace(self._global)

    def stop(self):
        sys.settrace(None)
        threading.settrace(None)
        return {k: sorted(v) for k, v in self.lines.items()}


# ============================================================ minimal pytest shim
class _Skip(Exception):
    pass


def install_pytest_shim():
    m = types.ModuleType("pytest")

    class _Raises:
        def __init__(self, exc, match=None):
            self.exc, self.match, self.value = exc, match, None

        def __enter__(self):
            return self

        def __exit__(self, et, ev, tb):
            if et is None:
                raise AssertionError("DID NOT RAISE %s" % (self.exc,))
            if not issubclass(et, self.exc):
                return False
            if self.match:
                import re

                if not re.search(self.match, str(ev)):
                    raise AssertionError("pattern %r not found in %r" % (self.match, str(ev)))
            self.value = ev
            return True

    def raises(exc, *args, **kwargs):
        match = kwargs.pop("match", None)
        if args:
            with _Raises(exc):
                args[0](*args[1:], **kwargs)
            return None
        return _Raises(exc, match)

    class _Approx:
        def __init__(self, expected, rel=1e-6, abs=1e-12):
            self.e, self.rel, self.abs = expected, rel, abs

        def __eq__(self, other):
            return abs(other - self.e) <= max(self.rel * abs(self.e), self.abs)

    class _Mark:
        def __getattr__(self, name):
            def deco_factory(*a, **k):
                if name == "parametrize" and len(a) >= 2:
                    def deco(fn):
                        fn._tc_params = (a[0], list(a[1]))
                        return fn
                    return deco
                if a and callable(a[0]) and not k:
                    return a[0]
                return lambda fn: fn
            return deco_factory

    def fixture(*a, **k):
        if a and callable(a[0]):
            return a[0]
        return lambda fn: fn

    def skip(msg=""):
        raise _Skip(msg)

    m.raises = raises
    m.approx = _Approx
    m.mark = _Mark()
    m.fixture = fixture
    m.skip = skip
    sys.modules["pytest"] = m


# ============================================================ test runners
def run_pytest(payload, tracer):
    import pytest

    results, collection_errors = {}, []

    class Plugin:
        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_protocol(self, item, nextitem):
            if payload.get("trace", True):
                tracer.start()
            yield
            lines = tracer.stop() if payload.get("trace", True) else {}
            results.setdefault(item.nodeid, {"test_id": item.nodeid, "outcome": "passed", "message": "", "duration": 0.0})["lines"] = lines

        def pytest_runtest_logreport(self, report):
            rec = results.setdefault(report.nodeid, {"test_id": report.nodeid, "outcome": "passed", "message": "", "duration": 0.0})
            if report.when == "call":
                rec["outcome"] = report.outcome
                rec["duration"] = round(report.duration, 4)
                if report.failed:
                    rec["message"] = str(report.longrepr)[-1600:]
            elif report.failed:
                rec["outcome"] = "error"
                rec["message"] = str(report.longrepr)[-1600:]
            elif report.skipped and report.when == "setup":
                rec["outcome"] = "skipped"

        def pytest_collectreport(self, report):
            if report.failed:
                collection_errors.append({"id": report.nodeid, "message": str(report.longrepr)[-1600:]})

    args = ["-q", "-p", "no:cacheprovider", "-p", "no:randomly", "--rootdir", ROOT, "-o", "console_output_style=classic"]
    if payload.get("fail_fast"):
        args.append("-x")
    if payload.get("junit"):
        args.append("--junitxml=" + payload["junit"])
    args.extend(payload.get("selection") or [ROOT])
    devnull = open(os.devnull, "w")
    old = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = devnull
    try:
        pytest.main(args, plugins=[Plugin()])
    finally:
        sys.stdout, sys.stderr = old
    return {"framework": "pytest " + pytest.__version__, "tests": list(results.values()), "collection_errors": collection_errors}


def _discover():
    found = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in ("__pycache__", "node_modules", "venv")]
        for f in filenames:
            if f.endswith(".py") and (f.startswith("test_") or f.endswith("_test.py")):
                found.append(os.path.join(dirpath, f))
    return sorted(found)


def run_builtin(payload, tracer):
    try:
        import pytest  # noqa: F401
    except Exception:
        install_pytest_shim()
    import importlib.util

    results, collection_errors = [], []
    for path in _discover():
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        name = "tc_test_" + rel.replace("/", "_").replace(".py", "")
        try:
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
        except Exception:
            collection_errors.append({"id": rel, "message": traceback.format_exc()[-1600:]})
            continue
        cases = []
        for attr, obj in list(vars(mod).items()):
            if attr.startswith("test") and callable(obj) and not isinstance(obj, type):
                cases.append((rel + "::" + attr, obj, None))
            elif attr.startswith("Test") and isinstance(obj, type):
                for m in dir(obj):
                    if m.startswith("test"):
                        cases.append((rel + "::" + attr + "::" + m, None, (obj, m)))
        for test_id, fn, meth in cases:
            params = getattr(fn, "_tc_params", None) if fn else None
            variants = [(test_id, ())]
            if params:
                variants = [("%s[%d]" % (test_id, i), v if isinstance(v, tuple) else (v,)) for i, v in enumerate(params[1])]
            for vid, argv in variants:
                rec = {"test_id": vid, "outcome": "passed", "message": "", "duration": 0.0}
                t0 = time.time()
                if payload.get("trace", True):
                    tracer.start()
                try:
                    if meth:
                        getattr(meth[0](), meth[1])()
                    else:
                        fn(*argv)
                except _Skip:
                    rec["outcome"] = "skipped"
                except AssertionError:
                    rec["outcome"] = "failed"
                    rec["message"] = traceback.format_exc()[-1600:]
                except Exception:
                    rec["outcome"] = "failed"
                    rec["message"] = traceback.format_exc()[-1600:]
                rec["lines"] = tracer.stop() if payload.get("trace", True) else {}
                rec["duration"] = round(time.time() - t0, 4)
                results.append(rec)
                if payload.get("fail_fast") and rec["outcome"] == "failed":
                    return {"framework": "trustcode-builtin", "tests": results, "collection_errors": collection_errors}
    return {"framework": "trustcode-builtin", "tests": results, "collection_errors": collection_errors}


def mode_tests(payload):
    tracer = LineTracer(ROOT)
    use_pytest = payload.get("prefer_pytest", True)
    if use_pytest:
        try:
            import pytest  # noqa: F401

            return run_pytest(payload, tracer)
        except ImportError:
            pass
    return run_builtin(payload, tracer)


# ============================================================ tasks
SAFE_BUILTINS = {k: getattr(__builtins__, k) if not isinstance(__builtins__, dict) else __builtins__[k] for k in (
    "range", "list", "tuple", "set", "dict", "int", "float", "str", "bool", "abs", "min", "max", "round", "len", "sum",
    "sorted", "any", "all", "zip", "enumerate", "isinstance", "repr", "True", "False", "None", "ValueError", "TypeError",
    "ZeroDivisionError", "KeyError", "IndexError", "Exception", "map", "filter", "reversed", "divmod", "pow")}


def raises(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc:
        return True
    except Exception:
        return False
    return False


def build_namespace(imports, setup):
    ns = {"raises": raises, "__builtins__": __builtins__}
    for target in imports or []:
        target = target.strip()
        if not target:
            continue
        parts = target.split(".")
        obj = None
        for cut in range(len(parts), 0, -1):
            try:
                obj = importlib.import_module(".".join(parts[:cut]))
            except Exception:
                continue
            for attr in parts[cut:]:
                obj = getattr(obj, attr)
            break
        if obj is None:
            raise ImportError("cannot import %s" % target)
        ns[parts[-1]] = obj
    if setup:
        exec(compile(setup.replace("; ", "\n"), "<setup>", "exec"), ns)
    return ns


def _short(v, n=160):
    try:
        s = repr(v)
    except Exception:
        s = "<unrepresentable>"
    return s if len(s) <= n else s[: n - 3] + "..."


import operator as _op

_CMP = {ast.Eq: _op.eq, ast.NotEq: _op.ne, ast.Lt: _op.lt, ast.LtE: _op.le, ast.Gt: _op.gt, ast.GtE: _op.ge,
        ast.Is: _op.is_, ast.IsNot: _op.is_not, ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}
_CMP_SYM = {ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=",
            ast.Is: "is", ast.IsNot: "is not", ast.In: "in", ast.NotIn: "not in"}


def task_check(task):
    ns = build_namespace(task.get("imports"), task.get("setup"))
    expr = task["expr"]
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        exec(compile(expr.replace("; ", "\n"), "<check>", "exec"), ns)
        return {"status": "pass", "detail": "statements executed without assertion error"}
    body = tree.body
    if isinstance(body, ast.Call) and isinstance(body.func, ast.Name) and body.func.id == "raises" and len(body.args) >= 2:
        exc = eval(compile(ast.Expression(body.args[0]), "<e>", "eval"), ns)
        fn = eval(compile(ast.Expression(body.args[1]), "<f>", "eval"), ns)
        args = [eval(compile(ast.Expression(a), "<a>", "eval"), ns) for a in body.args[2:]]
        call = "%s(%s)" % (getattr(fn, "__name__", "fn"), ", ".join(_short(a, 40) for a in args))
        try:
            got = fn(*args)
        except exc:
            return {"status": "pass", "detail": "%s raised %s as required" % (call, exc.__name__)}
        except Exception as other:
            return {"status": "fail", "detail": "%s raised %s instead of %s" % (call, type(other).__name__, exc.__name__)}
        return {"status": "fail", "detail": "%s returned %s instead of raising %s" % (call, _short(got, 60), exc.__name__)}
    if isinstance(body, ast.Compare) and len(body.ops) == 1 and type(body.ops[0]) in _CMP:
        left = eval(compile(ast.Expression(body.left), "<l>", "eval"), ns)
        right = eval(compile(ast.Expression(body.comparators[0]), "<r>", "eval"), ns)
        ok = _CMP[type(body.ops[0])](left, right)
        detail = "got %s, expected %s %s" % (_short(left), _CMP_SYM[type(body.ops[0])], _short(right))
        return {"status": "pass" if ok else "fail", "detail": detail}
    ok = eval(compile(tree, "<check>", "eval"), ns)
    return {"status": "pass" if ok else "fail", "detail": "evaluated to %s" % _short(ok)}


def _domain(spec):
    env = {"__builtins__": SAFE_BUILTINS}
    names, values = [], []
    for part in [p.strip() for p in spec.split(";") if p.strip()]:
        name, _, expr = part.partition("=")
        name = name.strip().split(" in ")[0].strip()
        if " in " in part and not expr:
            name, _, expr = part.partition(" in ")
        names.append(name.strip())
        values.append(list(eval(expr.strip(), env)))
    return names, values


def task_prove(task):
    ns = build_namespace(task.get("imports"), task.get("setup"))
    names, values = _domain(task.get("domain", ""))
    size = 1
    for v in values:
        size *= max(1, len(v))
    limit = int(task.get("exhaustive_limit", 20000))
    exhaustive = size <= limit
    if exhaustive:
        combos = itertools.product(*values)
    else:
        rng = random.Random(1337)
        sample = [tuple(v[0] for v in values), tuple(v[-1] for v in values)]
        sample += [tuple(rng.choice(v) for v in values) for _ in range(int(task.get("sample", 3000)))]
        combos = iter(sample)
    call = compile(task["expr"], "<call>", "eval")
    post = compile(task["post"], "<post>", "eval") if task.get("post") else None
    pre = compile(task["pre"], "<pre>", "eval") if task.get("pre") else None
    allow = tuple(task.get("allow") or ())
    checked = 0
    deadline = time.time() + float(task.get("budget_s", 20))
    for combo in combos:
        env = dict(ns)
        env.update(zip(names, combo))
        if pre is not None and not eval(pre, env):
            continue
        checked += 1
        try:
            result = eval(call, env)
        except Exception as exc:
            if type(exc).__name__ in allow:
                continue
            return {"status": "refuted", "cases": checked, "domain_size": size, "exhaustive": exhaustive,
                    "counterexample": {"inputs": {k: _short(v, 60) for k, v in zip(names, combo)}, "raised": "%s: %s" % (type(exc).__name__, exc)}}
        if post is not None:
            env["result"] = result
            try:
                ok = eval(post, env)
            except Exception as exc:
                ok = False
                result = "post-condition raised %s" % type(exc).__name__
        else:
            ok = bool(result)
        if not ok:
            return {"status": "refuted", "cases": checked, "domain_size": size, "exhaustive": exhaustive,
                    "counterexample": {"inputs": {k: _short(v, 60) for k, v in zip(names, combo)}, "result": _short(result, 80)}}
        if time.time() > deadline:
            return {"status": "inconclusive", "cases": checked, "domain_size": size, "exhaustive": False, "detail": "time budget exhausted"}
    return {"status": "proved" if exhaustive else "inconclusive", "cases": checked, "domain_size": size, "exhaustive": exhaustive}


def task_probe(task, call_timeout):
    ns = build_namespace([task["target"]], None)
    fn = ns[task["target"].split(".")[-1]]
    out = []
    for case in task["cases"]:
        box = {}

        def run():
            try:
                args = ast.literal_eval(case)
                if not isinstance(args, tuple):
                    args = (args,)
                res = fn(*args)
                box["ok"] = _short(res)
                if isinstance(res, (int, float)) and not isinstance(res, bool):
                    box["num"] = float(res)
            except BaseException as exc:  # noqa: B036 - we record everything
                box["exc"] = type(exc).__name__
                box["msg"] = str(exc)[:120]

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(call_timeout)
        if t.is_alive():
            out.append({"input": case, "timeout": True})
            break
        rec = {"input": case}
        rec.update(box)
        out.append(rec)
    return {"status": "ok", "results": out}


def mode_tasks(payload):
    results = {}
    call_timeout = float(payload.get("call_timeout", 1.5))
    for task in payload.get("tasks", []):
        tid = task["id"]
        box = {}

        def run(task=task, box=box):
            try:
                kind = task["kind"]
                if kind == "check":
                    box.update(task_check(task))
                elif kind == "prove":
                    box.update(task_prove(task))
                elif kind == "probe":
                    box.update(task_probe(task, call_timeout))
                else:
                    box.update({"status": "error", "detail": "unknown task kind"})
            except AssertionError as exc:
                box.update({"status": "fail", "detail": "AssertionError: %s" % exc})
            except BaseException as exc:  # noqa: B036
                box.update({"status": "error", "detail": "%s: %s" % (type(exc).__name__, str(exc)[:300])})

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(float(task.get("budget_s", 20)) + 5)
        results[tid] = box if not t.is_alive() else {"status": "error", "detail": "timeout (possible infinite loop)"}
    return {"results": results}


def main():
    mode, inp, out = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(inp, encoding="utf-8") as fh:
        payload = json.load(fh)
    try:
        res = mode_tests(payload) if mode == "tests" else mode_tasks(payload)
    except BaseException:  # noqa: B036
        res = {"fatal": traceback.format_exc()[-3000:]}
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(res, fh)
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()

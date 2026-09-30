"""Built-in deterministic SAST rule engine (``trustcode-sast``).

Runs on every workspace regardless of which external scanners are installed, so
Module 2 always yields at least one deterministic evidence source. Rules operate on
the Python AST with light intra-procedural data-flow (string provenance of local
variables); JavaScript/TypeScript get a small lexical rule set.
"""
from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from src.ingestion.ast_parser import dotted_name, language_of
from src.models.evidence import SOP_LEVEL, StaticFinding


@dataclass(frozen=True)
class Rule:
    rule_id: str
    title: str
    cwe: Optional[str]
    severity: str
    category: str
    security: bool


RULES: Dict[str, Rule] = {
    r.rule_id: r
    for r in [
        Rule("TC-SQLI", "SQL statement built from dynamic strings", "CWE-89", "HIGH", "Security", True),
        Rule("TC-CMDI", "Shell command built from dynamic input", "CWE-78", "HIGH", "Security", True),
        Rule("TC-EVAL", "Dynamic code evaluation", "CWE-95", "HIGH", "Security", True),
        Rule("TC-DESER", "Unsafe deserialization", "CWE-502", "HIGH", "Security", True),
        Rule("TC-SECRET", "Hard-coded secret", "CWE-798", "MEDIUM", "Security", True),
        Rule("TC-WEAKHASH", "Weak cryptographic hash", "CWE-328", "MEDIUM", "Security", True),
        Rule("TC-TLS", "TLS certificate verification disabled", "CWE-295", "HIGH", "Security", True),
        Rule("TC-RANDOM", "Insecure randomness for security value", "CWE-330", "MEDIUM", "Security", True),
        Rule("TC-AUTHBYPASS", "Success path returns before credential verification", "CWE-287", "HIGH", "Security", True),
        Rule("TC-DEBUG", "Debug mode enabled", "CWE-489", "MEDIUM", "Security", True),
        Rule("TC-ASSERT", "assert used for runtime validation", "CWE-703", "LOW", "Security", True),
        Rule("TC-DIVZERO", "Division by an unguarded parameter", "CWE-369", "LOW", "Bug", False),
        Rule("TC-BAREEXCEPT", "Exception swallowed", "CWE-396", "LOW", "Bug", False),
        Rule("TC-MUTDEFAULT", "Mutable default argument", None, "LOW", "Bug", False),
        Rule("TC-RESOURCE", "File handle opened without context manager", "CWE-772", "LOW", "Bug", False),
        Rule("TC-NESTEDLOOP", "Nested loop (quadratic complexity)", None, "INFO", "Performance", False),
        Rule("TC-JS-EVAL", "Dynamic code evaluation", "CWE-95", "HIGH", "Security", True),
        Rule("TC-JS-XSS", "Unescaped HTML sink", "CWE-79", "MEDIUM", "Security", True),
        Rule("TC-JS-CMDI", "child_process command built from dynamic input", "CWE-78", "HIGH", "Security", True),
    ]
}

SECRET_NAME = re.compile(r"(pass(word|wd)?|secret|token|api_?key|private_?key|pepper|credential|auth_?key)", re.I)
SECURITY_FUNC = re.compile(r"(token|password|secret|otp|salt|nonce|key)", re.I)
AUTH_FUNC = re.compile(r"(auth|login|signin|sign_in|verify|check_pass|authori[sz]e|is_admin|has_perm)", re.I)
VERIFY_CALL = re.compile(r"(verify|check|compare|hash|authenticat|validate_pass|bcrypt|argon|scrypt|pbkdf)", re.I)
SQL_SINKS = {"execute", "executemany", "executescript", "raw", "mogrify", "read_sql", "read_sql_query"}


def fingerprint(rule_id: str, file: str, code: str, function: Optional[str], occurrence: int) -> str:
    raw = f"{rule_id}|{file}|{' '.join(code.split())}|{function or ''}|{occurrence}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


class _FunctionContext:
    def __init__(self, node: Optional[ast.AST]) -> None:
        self.node = node
        self.assigns: Dict[str, List[ast.AST]] = {}
        self.params: Set[str] = set()
        if node is not None and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            self.params = {p.arg for p in a.posonlyargs + a.args + a.kwonlyargs}
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if isinstance(t, ast.Name):
                            self.assigns.setdefault(t.id, []).append(sub.value)
                elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name) and sub.value is not None:
                    self.assigns.setdefault(sub.target.id, []).append(sub.value)


def is_dynamic_string(node: ast.AST, ctx: _FunctionContext, depth: int = 0) -> bool:
    """True when ``node`` evaluates to a string assembled from non-literal parts."""
    if depth > 4:
        return False
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        sides = (node.left, node.right)
        has_str = any(isinstance(s, (ast.Constant, ast.JoinedStr)) and (not isinstance(s, ast.Constant) or isinstance(s.value, str)) for s in sides)
        has_dyn = any(not isinstance(s, ast.Constant) for s in sides)
        return (has_str and has_dyn) or any(is_dynamic_string(s, ctx, depth + 1) for s in sides)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return True
    if isinstance(node, ast.Name):
        return any(is_dynamic_string(v, ctx, depth + 1) for v in ctx.assigns.get(node.id, []))
    return False


class PythonRuleEngine:
    def __init__(self, file: str, source: str, tree: ast.Module) -> None:
        self.file, self.tree = file, tree
        self.lines = source.splitlines()
        self.findings: List[StaticFinding] = []
        self._occ: Dict[str, int] = {}
        self.is_test = "/tests/" in f"/{file}" or file.split("/")[-1].startswith("test_")

    # ------------------------------------------------------------------ helpers
    def _emit(self, rule_id: str, node: ast.AST, message: str, function: Optional[str], severity: Optional[str] = None) -> None:
        rule = RULES[rule_id]
        line = getattr(node, "lineno", 1)
        end = getattr(node, "end_lineno", line) or line
        code = self.lines[line - 1].strip() if 0 < line <= len(self.lines) else ""
        key = f"{rule_id}|{code}|{function}"
        occ = self._occ.get(key, 0)
        self._occ[key] = occ + 1
        sev = severity or rule.severity
        self.findings.append(
            StaticFinding(
                finding_id=f"trustcode:{rule_id}:{self.file}:{line}",
                tool="trustcode-sast",
                rule_id=rule_id,
                file=self.file,
                line=line,
                end_line=end,
                severity=sev,  # type: ignore[arg-type]
                sop_level=SOP_LEVEL[sev],  # type: ignore[arg-type]
                cwe=rule.cwe,
                category=rule.category,
                security=rule.security,
                message=f"{rule.title}: {message}",
                code=code,
                function=function,
                fingerprint=fingerprint(rule_id, self.file, code, function, occ),
            )
        )

    # ------------------------------------------------------------------ driver
    def run(self) -> List[StaticFinding]:
        self._scan_scope(self.tree, None, _FunctionContext(None))
        return self.findings

    def _scan_scope(self, scope: ast.AST, qual: Optional[str], ctx: _FunctionContext) -> None:
        for node in ast.iter_child_nodes(scope):
            self._scan_node(node, qual, ctx)

    def _scan_node(self, node: ast.AST, qual: Optional[str], ctx: _FunctionContext) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fq = f"{qual}.{node.name}" if qual else node.name
            fctx = _FunctionContext(node)
            self._function_rules(node, fq, fctx)
            for d in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
                if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                    self._emit("TC-MUTDEFAULT", d, f"`{node.name}` shares one mutable default across calls", fq)
            self._scan_scope(node, fq, fctx)
            return
        if isinstance(node, ast.ClassDef):
            self._scan_scope(node, f"{qual}.{node.name}" if qual else node.name, ctx)
            return
        self._node_rules(node, qual, ctx)
        self._scan_scope(node, qual, ctx)

    # ------------------------------------------------------------------ per-node rules
    def _node_rules(self, node: ast.AST, fn: Optional[str], ctx: _FunctionContext) -> None:
        if isinstance(node, ast.Call):
            self._call_rules(node, fn, ctx)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and not self.is_test:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str) and len(value.value) >= 4:
                for t in targets:
                    name = t.id if isinstance(t, ast.Name) else (t.attr if isinstance(t, ast.Attribute) else "")
                    if name and SECRET_NAME.search(name) and not value.value.startswith(("http", "/", "<")):
                        self._emit("TC-SECRET", node, f"`{name}` is assigned a literal credential", fn)
        elif isinstance(node, ast.ExceptHandler):
            if node.type is None:
                self._emit("TC-BAREEXCEPT", node, "bare `except:` hides every error", fn)
            elif len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                self._emit("TC-BAREEXCEPT", node, "exception silently discarded with `pass`", fn)
        elif isinstance(node, ast.Assert) and not self.is_test:
            self._emit("TC-ASSERT", node, "asserts are stripped under `python -O`", fn)
        elif isinstance(node, (ast.For, ast.While)):
            for inner in ast.walk(node):
                if inner is not node and isinstance(inner, (ast.For, ast.While)):
                    self._emit("TC-NESTEDLOOP", node, "loop nested inside loop", fn)
                    break
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
            self._division_rule(node, fn, ctx)

    def _call_rules(self, node: ast.Call, fn: Optional[str], ctx: _FunctionContext) -> None:
        name = dotted_name(node.func) or ""
        last = name.split(".")[-1]
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        if last in SQL_SINKS and node.args and is_dynamic_string(node.args[0], ctx):
            self._emit("TC-SQLI", node, f"`{name}` receives SQL assembled from runtime values", fn)
        if name.startswith("subprocess.") and isinstance(kw.get("shell"), ast.Constant) and kw["shell"].value is True:
            self._emit("TC-CMDI", node, f"`{name}(..., shell=True)` passes input to a shell", fn)
        if name in ("os.system", "os.popen"):
            dyn = bool(node.args) and not isinstance(node.args[0], ast.Constant)
            self._emit("TC-CMDI", node, f"`{name}` executes a shell command", fn, None if dyn else "LOW")
        if name in ("eval", "exec"):
            dyn = bool(node.args) and not isinstance(node.args[0], ast.Constant)
            self._emit("TC-EVAL", node, f"`{name}` executes dynamic code", fn, None if dyn else "MEDIUM")
        if name in ("pickle.load", "pickle.loads", "marshal.load", "marshal.loads", "shelve.open", "dill.loads"):
            self._emit("TC-DESER", node, f"`{name}` can execute attacker-controlled payloads", fn)
        if name == "yaml.load" and "Loader" not in kw:
            self._emit("TC-DESER", node, "`yaml.load` without SafeLoader", fn)
        if name in ("hashlib.md5", "hashlib.sha1", "md5", "sha1") or (name == "hashlib.new" and node.args and isinstance(node.args[0], ast.Constant) and str(node.args[0].value).lower() in ("md5", "sha1")):
            used_for_security = kw.get("usedforsecurity")
            if not (isinstance(used_for_security, ast.Constant) and used_for_security.value is False):
                self._emit("TC-WEAKHASH", node, f"`{name}` is collision-prone", fn)
        if isinstance(kw.get("verify"), ast.Constant) and kw["verify"].value is False:
            self._emit("TC-TLS", node, f"`{name}(verify=False)` disables certificate checks", fn)
        if isinstance(kw.get("debug"), ast.Constant) and kw["debug"].value is True and last == "run":
            self._emit("TC-DEBUG", node, "application started with debug=True", fn)
        if name.startswith("random.") and fn and SECURITY_FUNC.search(fn.split(".")[-1]):
            self._emit("TC-RANDOM", node, f"`{name}` is predictable; use `secrets`", fn)
        if not self.is_test:
            for k, v in kw.items():
                if SECRET_NAME.search(k) and isinstance(v, ast.Constant) and isinstance(v.value, str) and len(v.value) >= 4:
                    self._emit("TC-SECRET", node, f"literal credential passed as `{k}=`", fn)

    def _division_rule(self, node: ast.BinOp, fn: Optional[str], ctx: _FunctionContext) -> None:
        if isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
            return  # %-formatting
        right = node.right
        if not (isinstance(right, ast.Name) and right.id in ctx.params) or ctx.node is None:
            return
        if not division_guarded(ctx.node, right.id):
            self._emit("TC-DIVZERO", node, f"divides by parameter `{right.id}` without a zero guard", fn)

    # ------------------------------------------------------------------ function-level rules
    def _function_rules(self, node: ast.AST, fq: str, ctx: _FunctionContext) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and AUTH_FUNC.search(node.name) and not self.is_test:
            bad = _early_success_return(node)
            if bad is not None:
                self._emit("TC-AUTHBYPASS", bad, f"`{node.name}` can return success before verifying credentials", fq)
        with_items = {id(i.context_expr) for n in ast.walk(node) if isinstance(n, (ast.With, ast.AsyncWith)) for i in n.items}
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and dotted_name(sub.func) == "open" and id(sub) not in with_items:
                self._emit("TC-RESOURCE", sub, "`open()` result is not closed deterministically", fq)


_FLIP = {ast.Lt: ast.Gt, ast.Gt: ast.Lt, ast.LtE: ast.GtE, ast.GtE: ast.LtE, ast.Eq: ast.Eq, ast.NotEq: ast.NotEq}


def _excludes_zero(op: type, c: float) -> bool:
    """Does a branch on ``name <op> c`` separate zero from the non-zero path?"""
    return (
        (op in (ast.Eq, ast.NotEq) and c == 0)
        or (op is ast.LtE and c >= 0)
        or (op is ast.Lt and c > 0)
        or (op is ast.Gt and c >= 0)
        or (op is ast.GtE and c > 0)
    )


def division_guarded(func: ast.AST, name: str) -> bool:
    for sub in ast.walk(func):
        if isinstance(sub, ast.Compare) and len(sub.ops) == 1:
            left, right, op = sub.left, sub.comparators[0], type(sub.ops[0])
            if isinstance(right, ast.Name) and isinstance(left, ast.Constant):
                left, right, op = right, left, _FLIP.get(op, op)
            if isinstance(left, ast.Name) and left.id == name and isinstance(right, ast.Constant) and isinstance(right.value, (int, float)) and _excludes_zero(op, right.value):
                return True
        if isinstance(sub, (ast.If, ast.While, ast.IfExp)) and isinstance(sub.test, ast.Name) and sub.test.id == name:
            return True
        if isinstance(sub, (ast.If, ast.IfExp)) and isinstance(sub.test, ast.UnaryOp) and isinstance(sub.test.op, ast.Not) and isinstance(sub.test.operand, ast.Name) and sub.test.operand.id == name:
            return True
        if isinstance(sub, ast.ExceptHandler) and sub.type is not None:
            if "ZeroDivisionError" in ast.unparse(sub.type) or ast.unparse(sub.type) in ("Exception", "ArithmeticError"):
                return True
    return False


def _early_success_return(func: ast.AST) -> Optional[ast.AST]:
    """First ``return <truthy>`` reached (in source order) before any verification call."""
    state = {"verified": False}

    def calls_verify(expr: Optional[ast.AST]) -> bool:
        if expr is None:
            return False
        for c in ast.walk(expr):
            if isinstance(c, ast.Call) and VERIFY_CALL.search(dotted_name(c.func) or ""):
                return True
        return False

    def falsy(v: Optional[ast.AST]) -> bool:
        return v is None or (isinstance(v, ast.Constant) and not v.value)

    def walk(stmts: List[ast.stmt]) -> Optional[ast.AST]:
        for s in stmts:
            if isinstance(s, ast.Return):
                if calls_verify(s.value):
                    state["verified"] = True
                    continue
                if not state["verified"] and not falsy(s.value):
                    return s
                continue
            if isinstance(s, (ast.If, ast.While)):
                if calls_verify(s.test):
                    state["verified"] = True
                hit = walk(s.body) or walk(s.orelse)
                if hit:
                    return hit
                continue
            if isinstance(s, (ast.For, ast.With, ast.AsyncFor, ast.AsyncWith)):
                hit = walk(s.body)
                if hit:
                    return hit
                continue
            if isinstance(s, ast.Try):
                hit = walk(s.body) or next((h for h in (walk(x.body) for x in s.handlers) if h), None) or walk(s.finalbody)
                if hit:
                    return hit
                continue
            if calls_verify(s):
                state["verified"] = True
        return None

    return walk(getattr(func, "body", []))


_JS_RULES = [
    ("TC-JS-EVAL", re.compile(r"\beval\s*\(|new\s+Function\s*\("), "dynamic evaluation"),
    ("TC-JS-XSS", re.compile(r"\.innerHTML\s*=|document\.write\s*\(|dangerouslySetInnerHTML"), "HTML sink receives unescaped content"),
    ("TC-JS-CMDI", re.compile(r"\b(exec|execSync)\s*\(\s*(`[^`]*\$\{|[^'\"`)]+\+)"), "command assembled at runtime"),
]


def scan_lexical(file: str, source: str) -> List[StaticFinding]:
    out: List[StaticFinding] = []
    for i, line in enumerate(source.splitlines(), start=1):
        for rule_id, pat, msg in _JS_RULES:
            if pat.search(line):
                rule = RULES[rule_id]
                out.append(
                    StaticFinding(
                        finding_id=f"trustcode:{rule_id}:{file}:{i}", tool="trustcode-sast", rule_id=rule_id, file=file, line=i, end_line=i,
                        severity=rule.severity, sop_level=SOP_LEVEL[rule.severity], cwe=rule.cwe, category=rule.category,  # type: ignore[arg-type]
                        security=rule.security, message=f"{rule.title}: {msg}", code=line.strip(), fingerprint=fingerprint(rule_id, file, line, None, 0),
                    )
                )
    return out


def scan_files(files: Dict[str, str], trees: Dict[str, ast.Module]) -> List[StaticFinding]:
    findings: List[StaticFinding] = []
    for path in sorted(files):
        lang = language_of(path)
        if lang == "python" and path in trees:
            findings.extend(PythonRuleEngine(path, files[path], trees[path]).run())
        elif lang in ("javascript", "typescript", "tsx"):
            findings.extend(scan_lexical(path, files[path]))
    return findings

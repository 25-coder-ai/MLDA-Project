"""Semantic predicate verification — catches *semantic hallucinations*.

A semantic hallucination cites real symbols but asserts something false about their
behaviour ("`total` is used before assignment", "`parse_quantity` never handles
ValueError"). Each defect assertion is compiled into a typed boolean predicate
``P(subject) = asserted`` and decided against AST facts, the call graph, or
deterministic rule evidence. The claim's polarity is detected so both negative
("is missing a None check") and positive ("handles None") assertions are judged.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from src.evidence.rules import SQL_SINKS, _FunctionContext, division_guarded, is_dynamic_string
from src.ingestion.ast_parser import SymbolIndex, dotted_name
from src.models.evidence import Symbol
from src.models.scoring import PredicateResult, SymbolResolution
from src.verification.call_graph import CallGraph

Fact = Tuple[Optional[bool], str, List[int]]


@dataclass
class FactContext:
    ix: SymbolIndex
    graph: CallGraph
    db: "object"  # GroundTruthDatabase
    file: str
    func: Optional[Symbol]
    var: Optional[str]
    node: Optional[ast.AST]


@dataclass
class PredicateSpec:
    kind: str
    label: str
    subject: str  # function | variable | scope
    pos: Sequence[str]
    neg: Sequence[str]
    fact: Callable[[FactContext], Fact]


# =========================================================================== AST facts
def _body_nodes(func: ast.AST):
    """Walk a function body without descending into nested defs/classes."""
    stack = list(getattr(func, "body", []))
    while stack:
        n = stack.pop()
        yield n
        for c in ast.iter_child_nodes(n):
            if not isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                stack.append(c)


def _loads(node: ast.AST, var: str) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id == var and isinstance(n.ctx, ast.Load):
            return True
    return False


def _stores(node: ast.AST, var: str) -> bool:
    targets: List[ast.AST] = []
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = [node.target] if not (isinstance(node, ast.AnnAssign) and node.value is None) else []
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        return any((a.asname or a.name.split(".")[0]) == var for a in node.names)
    return any(isinstance(n, ast.Name) and n.id == var for t in targets for n in ast.walk(t))


def _terminates(stmts: List[ast.stmt]) -> bool:
    if not stmts:
        return False
    last = stmts[-1]
    if isinstance(last, (ast.Return, ast.Raise)):
        return True
    if isinstance(last, ast.If):
        return _terminates(last.body) and _terminates(last.orelse)
    if isinstance(last, ast.Try):
        return (_terminates(last.body) or _terminates(last.orelse)) and all(_terminates(h.body) for h in last.handlers) or _terminates(last.finalbody)
    if isinstance(last, (ast.With, ast.AsyncWith)):
        return _terminates(last.body)
    if isinstance(last, ast.While) and isinstance(last.test, ast.Constant) and last.test.value is True:
        return not any(isinstance(n, ast.Break) for n in ast.walk(last))
    return False


def _definite_assignment(stmts: List[ast.stmt], var: str, assigned: bool) -> Tuple[bool, Optional[int]]:
    """Returns (assigned_after, first_unsafe_line)."""
    for s in stmts:
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            assigned = assigned or s.name == var
            continue
        if isinstance(s, ast.If):
            if not assigned and _loads(s.test, var):
                return assigned, s.lineno
            a1, bad = _definite_assignment(s.body, var, assigned)
            if bad:
                return a1, bad
            a2, bad = _definite_assignment(s.orelse, var, assigned)
            if bad:
                return a2, bad
            assigned = (a1 or _terminates(s.body)) and (a2 or _terminates(s.orelse))
            continue
        if isinstance(s, (ast.For, ast.AsyncFor, ast.While)):
            head = s.iter if isinstance(s, (ast.For, ast.AsyncFor)) else s.test
            if not assigned and _loads(head, var):
                return assigned, s.lineno
            inner = assigned or (isinstance(s, (ast.For, ast.AsyncFor)) and any(isinstance(n, ast.Name) and n.id == var for n in ast.walk(s.target)))
            _, bad = _definite_assignment(s.body, var, inner)
            if bad:
                return assigned, bad
            _, bad = _definite_assignment(s.orelse, var, assigned)
            if bad:
                return assigned, bad
            continue
        if isinstance(s, ast.Try):
            ab, bad = _definite_assignment(s.body, var, assigned)
            if bad:
                return ab, bad
            outs = [ab or _terminates(s.body)]
            for h in s.handlers:
                ah, bad = _definite_assignment(h.body, var, assigned or h.name == var)
                if bad:
                    return ah, bad
                outs.append(ah or _terminates(h.body))
            af, bad = _definite_assignment(s.finalbody, var, assigned)
            if bad:
                return af, bad
            assigned = all(outs) or af
            continue
        if isinstance(s, (ast.With, ast.AsyncWith)):
            for item in s.items:
                if not assigned and _loads(item.context_expr, var):
                    return assigned, s.lineno
                if item.optional_vars is not None and any(isinstance(n, ast.Name) and n.id == var for n in ast.walk(item.optional_vars)):
                    assigned = True
            assigned, bad = _definite_assignment(s.body, var, assigned)
            if bad:
                return assigned, bad
            continue
        if isinstance(s, ast.AugAssign) and isinstance(s.target, ast.Name) and s.target.id == var and not assigned:
            return assigned, s.lineno
        value_part = s.value if isinstance(s, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and getattr(s, "value", None) is not None else s
        if not assigned and _loads(value_part, var):
            return assigned, s.lineno
        if _stores(s, var):
            assigned = True
        if isinstance(s, (ast.Return, ast.Raise)):
            return True, None
    return assigned, None


def fact_initialized(c: FactContext) -> Fact:
    if c.node is None or not c.var:
        return None, "no function/variable subject", []
    params = {a.arg for a in ast.walk(c.node.args) if isinstance(a, ast.arg)}  # type: ignore[union-attr]
    if c.var in params:
        return True, f"`{c.var}` is a parameter of `{c.func.name}` and is bound on entry", [c.func.line]
    stores = [
        n.lineno
        for n in _body_nodes(c.node)
        if isinstance(n, ast.stmt)
        and (_stores(n, c.var) or (isinstance(n, (ast.For, ast.AsyncFor)) and any(isinstance(x, ast.Name) and x.id == c.var for x in ast.walk(n.target))))
    ]
    if not stores:
        return None, f"`{c.var}` is not assigned locally in `{c.func.name}`", []
    _, bad = _definite_assignment(c.node.body, c.var, False)  # type: ignore[union-attr]
    if bad:
        return False, f"`{c.var}` can be read at line {bad} on a path where it was never assigned", [bad]
    return True, f"`{c.var}` is definitely assigned (line {min(stores)}) on every path before its first use", sorted(stores)[:3]


def fact_called(c: FactContext) -> Fact:
    if c.func is None:
        return None, "no function subject", []
    callers = c.graph.callers(c.func.qualified_name)
    if callers:
        return True, f"`{c.func.name}` is called by {', '.join('`' + x.split('.')[-1] + '`' for x in callers[:4])}", []
    refs = [r for r in c.ix.references if r.name.split(".")[-1] == c.func.name and not (r.file == c.func.file and c.func.line <= r.line <= c.func.end_line)]
    if refs:
        return True, f"`{c.func.name}` is referenced at {refs[0].file}:{refs[0].line}", [refs[0].line]
    return False, f"no call site or reference to `{c.func.name}` exists", []


def fact_handles_exceptions(c: FactContext) -> Fact:
    if c.node is None:
        return None, "no function subject", []
    tries = [n for n in _body_nodes(c.node) if isinstance(n, ast.Try) and n.handlers]
    if tries:
        caught = sorted({ast.unparse(h.type) if h.type is not None else "everything" for t in tries for h in t.handlers})
        return True, f"`{c.func.name}` wraps risky calls in try/except ({', '.join(caught)}) at line {tries[0].lineno}", [t.lineno for t in tries]
    return False, f"`{c.func.name}` contains no try/except handler", []


def fact_checks_none(c: FactContext) -> Fact:
    if c.node is None:
        return None, "no function subject", []
    hits: List[int] = []
    for n in _body_nodes(c.node):
        if isinstance(n, ast.Compare) and any(isinstance(x, ast.Constant) and x.value is None for x in n.comparators):
            if c.var is None or _loads(n.left, c.var):
                hits.append(n.lineno)
        elif isinstance(n, (ast.If, ast.While, ast.IfExp)):
            t = n.test.operand if isinstance(n.test, ast.UnaryOp) and isinstance(n.test.op, ast.Not) else n.test
            if isinstance(t, ast.Name) and (c.var is None or t.id == c.var):
                hits.append(n.lineno)
    if hits:
        return True, f"`{c.func.name}` guards against None/falsy values at line(s) {', '.join(map(str, sorted(set(hits))[:4]))}", sorted(set(hits))
    return False, f"`{c.func.name}` has no None/falsy guard", []


def fact_sql_parameterized(c: FactContext) -> Fact:
    if c.node is None:
        return None, "no function subject", []
    ctx = _FunctionContext(c.node)
    sinks = [n for n in _body_nodes(c.node) if isinstance(n, ast.Call) and (dotted_name(n.func) or "").split(".")[-1] in SQL_SINKS and n.args]
    if not sinks:
        return None, f"`{c.func.name}` executes no SQL", []
    dyn = [n.lineno for n in sinks if is_dynamic_string(n.args[0], ctx)]
    if dyn:
        return False, f"SQL text is assembled from runtime values at line {dyn[0]}", dyn
    return True, f"all {len(sinks)} SQL call(s) in `{c.func.name}` use static text with bound parameters", [n.lineno for n in sinks]


def fact_recursive(c: FactContext) -> Fact:
    if c.func is None:
        return None, "no function subject", []
    rec, path = c.graph.is_recursive(c.func.qualified_name)
    if rec:
        return True, "call cycle: " + " → ".join(p.split(".")[-1] for p in path), []
    callees = c.graph.callees(c.func.qualified_name)
    return False, f"`{c.func.name}` has no call cycle (calls: {', '.join(x.split('.')[-1] for x in callees) or 'nothing internal'})", []


def fact_zero_division_guarded(c: FactContext) -> Fact:
    if c.node is None:
        return None, "no function subject", []
    params = {a.arg for a in ast.walk(c.node.args) if isinstance(a, ast.arg)}  # type: ignore[union-attr]
    divs = [n for n in _body_nodes(c.node) if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Div, ast.FloorDiv, ast.Mod)) and not (isinstance(n.left, ast.Constant) and isinstance(n.left.value, str))]
    risky = [n for n in divs if isinstance(n.right, ast.Name) and n.right.id in params and not division_guarded(c.node, n.right.id)]
    if risky:
        return False, f"line {risky[0].lineno} divides by parameter `{risky[0].right.id}` with no zero guard", [n.lineno for n in risky]  # type: ignore[attr-defined]
    if not divs:
        return True, f"`{c.func.name}` performs no division", []
    return True, f"every division in `{c.func.name}` is guarded or uses a non-zero constant divisor", [n.lineno for n in divs]


def fact_always_returns(c: FactContext) -> Fact:
    if c.node is None:
        return None, "no function subject", []
    ok = _terminates(c.node.body)  # type: ignore[union-attr]
    return ok, ("every control-flow path ends in return/raise" if ok else "some path falls off the end and returns None implicitly"), [c.func.end_line]


def fact_no_mutable_default(c: FactContext) -> Fact:
    if c.node is None:
        return None, "no function subject", []
    a = c.node.args  # type: ignore[union-attr]
    bad = [d for d in a.defaults + [d for d in a.kw_defaults if d is not None] if isinstance(d, (ast.List, ast.Dict, ast.Set))]
    return (not bad), ("mutable default at line %d" % bad[0].lineno if bad else "all defaults are immutable"), [d.lineno for d in bad]


def fact_nested_loops(c: FactContext) -> Fact:
    if c.node is None:
        return None, "no function subject", []
    for n in _body_nodes(c.node):
        if isinstance(n, (ast.For, ast.While)) and any(isinstance(x, (ast.For, ast.While, ast.comprehension)) for x in ast.walk(n) if x is not n):
            return True, f"nested iteration at line {n.lineno}", [n.lineno]
    return False, f"`{c.func.name}` has no nested iteration (linear in its input)", []


def fact_constant_time(c: FactContext) -> Fact:
    if c.node is None:
        return None, "no function subject", []
    for n in _body_nodes(c.node):
        if isinstance(n, ast.Call) and (dotted_name(n.func) or "").endswith("compare_digest"):
            return True, f"secrets are compared with `{dotted_name(n.func)}` (constant time) at line {n.lineno}", [n.lineno]
    for n in _body_nodes(c.node):
        if isinstance(n, ast.Compare) and any(isinstance(o, (ast.Eq, ast.NotEq)) for o in n.ops) and re.search(r"hash|digest|password|token|signature|secret", ast.unparse(n), re.I):
            return False, f"secret material compared with `==` at line {n.lineno}", [n.lineno]
    return None, "no secret comparison found", []


_KIND_OF_CALL = {"list": "list", "sorted": "list", "dict": "dict", "str": "str", "int": "number", "float": "number", "round": "number", "len": "number", "sum": "number", "abs": "number", "bool": "bool", "set": "set", "tuple": "tuple", "any": "bool", "all": "bool"}


def infer_return_kinds(func: ast.AST) -> Tuple[Set[str], bool]:
    kinds: Set[str] = set()
    complete = True
    rets = [n for n in _body_nodes(func) if isinstance(n, ast.Return)]
    for r in rets:
        v = r.value
        if v is None or (isinstance(v, ast.Constant) and v.value is None):
            kinds.add("none")
        elif isinstance(v, ast.Constant):
            kinds.add({bool: "bool", int: "number", float: "number", str: "str"}.get(type(v.value), "other"))
        elif isinstance(v, (ast.List, ast.ListComp)):
            kinds.add("list")
        elif isinstance(v, (ast.Dict, ast.DictComp)):
            kinds.add("dict")
        elif isinstance(v, (ast.Set, ast.SetComp)):
            kinds.add("set")
        elif isinstance(v, ast.Tuple):
            kinds.add("tuple")
        elif isinstance(v, (ast.JoinedStr,)):
            kinds.add("str")
        elif isinstance(v, (ast.Compare, ast.BoolOp)) or (isinstance(v, ast.UnaryOp) and isinstance(v.op, ast.Not)):
            kinds.add("bool")
        elif isinstance(v, ast.BinOp) and isinstance(v.op, (ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Pow)):
            kinds.add("number")
        elif isinstance(v, ast.Call) and dotted_name(v.func) in _KIND_OF_CALL:
            kinds.add(_KIND_OF_CALL[dotted_name(v.func)])  # type: ignore[index]
        else:
            complete = False
    if not _terminates(getattr(func, "body", [])):
        kinds.add("none")
    return kinds, complete and bool(rets)


_KIND_WORDS = {"list": "list", "array": "list", "dict": "dict", "dictionary": "dict", "mapping": "dict", "string": "str", "str": "str", "int": "number", "integer": "number", "float": "number", "number": "number", "numeric": "number", "bool": "bool", "boolean": "bool", "none": "none", "null": "none", "tuple": "tuple", "set": "set"}
_RETURNS = re.compile(r"\breturns?\s+(?:an?\s+|the\s+)?(list|array|dict|dictionary|mapping|string|str|int|integer|float|number|numeric|bool|boolean|none|null|tuple|set)\b", re.I)


def _rule_fact(rule_ids: Tuple[str, ...], good: str, bad: str) -> Callable[[FactContext], Fact]:
    def fn(c: FactContext) -> Fact:
        if c.func is not None:
            hits = [f for f in c.db.findings_in_function(c.func.qualified_name) if f.rule_id in rule_ids]
            where = f"`{c.func.name}`"
        else:
            hits = [f for f in c.db.findings if f.file == c.file and f.rule_id in rule_ids]
            where = f"`{c.file}`"
        if hits:
            return False, f"{bad} ({hits[0].rule_id} at {hits[0].file}:{hits[0].line})", [h.line for h in hits]
        return True, f"{good} in {where}", []
    return fn


# =========================================================================== predicate registry
PREDICATES: List[PredicateSpec] = [
    PredicateSpec("initialized", "Variable initialised before use", "variable",
                  [r"\bis (always |properly )?initiali[sz]ed\b"],
                  [r"used before (?:assignment|being assigned|initiali[sz]ation|it is assigned)", r"\buninitiali[sz]ed\b", r"\b(?:may|might|could|can) be unbound\b", r"\bnot (?:initiali[sz]ed|assigned)\b", r"unboundlocalerror", r"\breferenced before assignment\b"],
                  fact_initialized),
    PredicateSpec("called", "Function has call sites", "function",
                  [r"\bis (?:only )?called (?:by|from|in)\b", r"\bis invoked by\b"],
                  [r"\bnever (?:called|used|invoked|referenced)\b", r"\bdead code\b", r"\bunused (?:function|method|helper)\b", r"\bis unused\b", r"\bnothing calls\b"],
                  fact_called),
    PredicateSpec("handles_exceptions", "Exception handling present", "function",
                  [r"\b(?:properly |correctly |safely )?(?:handles|catches)\b[^.]{0,40}\b(?:exception|error|valueerror|typeerror|keyerror)s?\b"],
                  [r"\b(?:no|missing|lacks|lack of|without)\b[^.]{0,30}\b(?:error|exception) handling\b", r"\bunhandled\b[^.]{0,25}\b(?:exception|error)s?\b",
                   r"\b(?:doesn't|does not|never|fails to|won't)\s+(?:handle|catch)\w*\b[^.]{0,40}\b(?:exception|error|\w+error)s?\b", r"\b(?:exception|error)s? (?:is |are )?not (?:handled|caught)\b", r"\b(?:no|missing) try\b"],
                  fact_handles_exceptions),
    PredicateSpec("checks_none", "None / null guard present", "function",
                  [r"\b(?:checks|guards|validates)\b[^.]{0,20}\b(?:none|null)\b"],
                  [r"\b(?:doesn't|does not|never|fails to)\s+(?:check|validate|guard)s?\b[^.]{0,25}\b(?:none|null)\b", r"\b(?:no|missing)\s+(?:none|null)[ -]?check", r"\bnonetype\b", r"\bnull (?:pointer|dereference)\b", r"\bcrash(?:es)? (?:on|with|when)\b[^.]{0,20}\bnone\b"],
                  fact_checks_none),
    PredicateSpec("sql_parameterized", "SQL uses bound parameters", "function",
                  [r"\buses? (?:parameteri[sz]ed|bound|prepared) (?:queries|query|statements?|parameters)\b", r"\b(?:safe from|not vulnerable to|immune to) sql injection\b"],
                  [r"\bsql injection\b", r"\bsqli\b", r"\b(?:f-string|string formatting|concatenat\w+|interpolat\w+)\b[^.]{0,40}\b(?:sql|query)\b", r"\b(?:sql|query)\b[^.]{0,40}\b(?:f-string|string formatting|concatenat\w+|interpolat\w+)", r"\bnot parameteri[sz]ed\b", r"\bunsanitized (?:sql|query)\b"],
                  fact_sql_parameterized),
    PredicateSpec("recursive", "Function is recursive", "function",
                  [r"\bis recursive\b", r"\bcalls itself\b", r"\binfinite recursion\b", r"\brecursion depth\b", r"\boverflow the stack\b"],
                  [r"\bis not recursive\b", r"\bnon-recursive\b"],
                  fact_recursive),
    PredicateSpec("zero_division_guarded", "Divisions guarded against zero", "function",
                  [r"\bguards? against (?:zero|division by zero)\b", r"\bcannot divide by zero\b"],
                  [r"\bdivi(?:sion|de) by zero\b", r"\bzerodivisionerror\b", r"\bdivides? by (?:a )?zero\b"],
                  fact_zero_division_guarded),
    PredicateSpec("always_returns", "Explicit return on every path", "function",
                  [r"\balways returns a value\b"],
                  [r"\bmissing return\b", r"\bimplicitly returns none\b", r"\bmay return none\b", r"\bfalls? off the end\b"],
                  fact_always_returns),
    PredicateSpec("no_mutable_default", "No mutable default arguments", "function",
                  [], [r"\bmutable default\b"], fact_no_mutable_default),
    PredicateSpec("nested_loops", "Quadratic nested iteration", "function",
                  [r"\bo\(n\^?2\)|\bo\(n²\)|\bquadratic\b|\bnested loops?\b"], [r"\blinear time\b|\bo\(n\)"],
                  fact_nested_loops),
    PredicateSpec("constant_time_compare", "Constant-time secret comparison", "function",
                  [r"\bconstant[- ]time\b", r"\bcompare_digest\b"],
                  [r"\btiming (?:attack|side[- ]channel|leak)", r"\bnon-constant[- ]time\b"],
                  fact_constant_time),
    PredicateSpec("no_hardcoded_secret", "No hard-coded secrets", "scope",
                  [], [r"\bhard[- ]?coded\b[^.]{0,25}\b(?:secret|password|key|token|credential|pepper)", r"\b(?:secret|password|key|token|credential)s?\b[^.]{0,25}\b(?:committed|in source|hard[- ]?coded)"],
                  _rule_fact(("TC-SECRET", "B105", "B106", "B107"), "no hard-coded credential", "hard-coded credential found")),
    PredicateSpec("no_command_injection", "No shell/command injection", "function",
                  [], [r"\bcommand injection\b", r"\bshell=true\b", r"\bos\.system\b", r"\bshell injection\b"],
                  _rule_fact(("TC-CMDI", "B602", "B605", "B607"), "no shell execution", "shell execution with dynamic input")),
    PredicateSpec("no_eval", "No dynamic code evaluation", "function",
                  [], [r"\beval\(|\bexec\(|\bcode injection\b|\buses eval\b"],
                  _rule_fact(("TC-EVAL", "B307", "B102"), "no eval/exec", "dynamic code evaluation")),
    PredicateSpec("no_unsafe_deserialization", "No unsafe deserialization", "function",
                  [], [r"\bpickle\b|\bunsafe deseriali[sz]ation\b|\byaml\.load\b"],
                  _rule_fact(("TC-DESER", "B301", "B506"), "no unsafe deserialization", "unsafe deserialization")),
    PredicateSpec("strong_hash", "Strong hash algorithm", "function",
                  [r"\buses? (?:a )?strong hash"], [r"\bweak (?:hash|hashing|digest|cryptographic)", r"\bmd5\b", r"\bsha-?1\b"],
                  _rule_fact(("TC-WEAKHASH", "B303", "B324"), "no MD5/SHA-1 usage", "weak hash algorithm")),
    PredicateSpec("auth_enforced", "Credentials verified before success", "function",
                  [r"\balways verifies\b"], [r"\bauthentication bypass\b", r"\bauth(?:entication)? can be bypassed\b", r"\bskips? (?:the )?(?:password|credential|auth\w*) (?:check|verification)\b"],
                  _rule_fact(("TC-AUTHBYPASS",), "success is only returned after credential verification", "success returned before verification")),
    PredicateSpec("no_resource_leak", "Resources closed deterministically", "function",
                  [], [r"\bresource leak\b", r"\b(?:file|handle|connection|socket)s? (?:is |are )?(?:never |not )(?:closed|released)\b", r"\bleaks? (?:a |the )?(?:file|handle|connection|socket)"],
                  _rule_fact(("TC-RESOURCE",), "no unmanaged file handles", "file opened without context manager")),
]

IMPACT = re.compile(r"(?:chang\w+|modify\w*|edit\w*|refactor\w*|updat\w+|renam\w+)\s+`?([A-Za-z_][\w.]*)`?[^.]{0,60}?\b(?:break|affect|impact)s?\s+`?([A-Za-z_][\w.]*)`?", re.I)
CORRECTNESS_POS = [r"\b(?:is|are|looks) (?:fully |completely |provably )?(?:correct|bug[- ]free|safe to merge|fine|robust)\b", r"\bprovabl[ey]\b", r"\bno bugs\b", r"\bhandles all\b", r"\balways (?:works|returns (?:the )?(?:right|correct|valid))\b", r"\bguaranteed\b", r"\blgtm\b", r"\bverified correct\b"]
CORRECTNESS_NEG = [r"\b(?:is|are) (?:incorrect|wrong|buggy|broken)\b", r"\bproduces wrong\b"]
REQ_POS = [r"\b(?:satisfies|satisfy|complies with|comply with|meets|conforms to|correctly implements|fulfils|fulfills|in line with|compliant with)\b"]
REQ_NEG = [r"\b(?:violates|violate|breaks|does not (?:satisfy|meet|comply|implement|conform)|doesn't (?:satisfy|meet|comply|implement)|fails to (?:meet|satisfy|implement|comply)|non-compliant|not compliant|diverges from|contradicts)\b"]
UNIVERSAL = re.compile(r"\b(?:every|all|any|always|provabl[ey]|for all|guaranteed|never fails)\b", re.I)


_NEGATOR = re.compile(r"\b(?:never|not|no|doesn't|does not|don't|do not|fails? to|isn't|is not|without|cannot|can't|won't)\s+(?:\w+\s+){0,1}$", re.I)


def detect_polarity(text: str, pos: Sequence[str], neg: Sequence[str]) -> Tuple[Optional[bool], str]:
    for p in pos:
        m = re.search(p, text, re.I)
        if m:
            if _NEGATOR.search(text[max(0, m.start() - 24) : m.start()]):
                return False, text[max(0, m.start() - 24) : m.end()].split(" ", 1)[-1] if m.start() > 24 else text[: m.end()]
            return True, m.group(0)
    for p in neg:
        m = re.search(p, text, re.I)
        if m:
            return False, m.group(0)
    return None, ""


def _pick_function(resolutions: List[SymbolResolution], ix: SymbolIndex, file: str, start: int, end: int, text: str, phrase: str) -> Optional[Symbol]:
    cands: List[Tuple[int, Symbol]] = []
    anchor = text.lower().find(phrase.lower()) if phrase else -1
    for r in resolutions:
        for m in r.matches:
            if m.kind in ("function", "method"):
                sym = ix.get(m.qualified_name)
                if sym:
                    pos = text.find(r.token)
                    dist = abs(anchor - pos) if anchor >= 0 and pos >= 0 else 999
                    cands.append((dist + (0 if sym.file == file else 500), sym))
    if cands:
        return sorted(cands, key=lambda t: t[0])[0][1]
    if file and start:
        return ix.enclosing(file, start) or ix.enclosing(file, end)
    return None


def _pick_variable(resolutions: List[SymbolResolution], func: Optional[Symbol], text: str, phrase: str) -> Optional[str]:
    anchor = text.lower().find(phrase.lower()) if phrase else len(text)
    best: Tuple[int, Optional[str]] = (10**6, None)
    for r in resolutions:
        if not any(m.kind in ("variable", "parameter", "global") for m in r.matches):
            continue
        if func and not any(m.qualified_name.startswith(func.qualified_name + ".") for m in r.matches):
            continue
        pos = text.find(r.token)
        d = anchor - pos if pos <= anchor else 10**5 + pos
        if 0 <= d < best[0]:
            best = (d, r.token)
    return best[1]


def evaluate_predicates(text: str, resolutions: List[SymbolResolution], ix: SymbolIndex, graph: CallGraph, db, file: str, start: int, end: int) -> List[PredicateResult]:
    results: List[PredicateResult] = []
    for spec in PREDICATES:
        asserted, phrase = detect_polarity(text, spec.pos, spec.neg)
        if asserted is None:
            continue
        func = _pick_function(resolutions, ix, file, start, end, text, phrase)
        if spec.subject == "scope" and func is not None and not any(r.matches and r.matches[0].kind in ("function", "method") for r in resolutions):
            func = None
        var = _pick_variable(resolutions, func, text, phrase) if spec.subject == "variable" else None
        node = ix.nodes.get(func.qualified_name) if func else None
        if spec.subject == "variable" and func is None and var is None:
            continue
        ctx = FactContext(ix=ix, graph=graph, db=db, file=(func.file if func else file), func=func, var=var, node=node)
        actual, evidence, lines = spec.fact(ctx)
        outcome = "unknown" if actual is None else ("corroborated" if actual == asserted else "refuted")
        subject = f"{func.name}.{var}" if var and func else (func.name if func else (var or file))
        results.append(PredicateResult(kind=spec.kind, label=spec.label, subject=subject, asserted=asserted, actual=actual, outcome=outcome, evidence=evidence, lines=lines, phrase=phrase))

    # return-type assertions
    m = _RETURNS.search(text)
    if m:
        func = _pick_function(resolutions, ix, file, start, end, text, m.group(0))
        node = ix.nodes.get(func.qualified_name) if func else None
        if func and node is not None:
            want = _KIND_WORDS[m.group(1).lower()]
            kinds, complete = infer_return_kinds(node)
            if want in kinds:
                outcome, actual = "corroborated", True
            elif complete:
                outcome, actual = "refuted", False
            else:
                outcome, actual = "unknown", None
            results.append(PredicateResult(kind="returns_kind", label=f"Returns {want}", subject=func.name, asserted=True, actual=actual, outcome=outcome, evidence=f"inferred return kinds of `{func.name}`: {', '.join(sorted(kinds)) or 'unknown'}", phrase=m.group(0)))

    # impact claims (SOP 3.3): "changing A breaks C" requires a call path C -> ... -> A
    im = IMPACT.search(text)
    if im:
        a_syms = graph.resolve_name(im.group(1))
        c_syms = graph.resolve_name(im.group(2))
        if a_syms and c_syms:
            path = next((p for a in a_syms for cc in c_syms for p in [graph.path(cc, a)] if p), None)
            if path:
                results.append(PredicateResult(kind="impact", label="Impact path exists", subject=f"{im.group(1)} → {im.group(2)}", asserted=True, actual=True, outcome="corroborated", evidence="dependency path: " + " → ".join(p.split(".")[-1] for p in path), phrase=im.group(0)))
            else:
                results.append(PredicateResult(kind="impact", label="Unsupported Impact Claim", subject=f"{im.group(1)} → {im.group(2)}", asserted=True, actual=False, outcome="unsupported_impact", evidence=f"no call path from `{im.group(2)}` to `{im.group(1)}` exists in the call graph", phrase=im.group(0)))
    return results

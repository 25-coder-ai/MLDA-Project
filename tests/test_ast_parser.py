from src.ingestion.ast_parser import build_symbol_index, module_name
from src.verification.call_graph import CallGraph
from src.verification.symbol_resolver import SymbolResolver


def test_module_name():
    assert module_name("shop/pricing.py") == "shop.pricing"
    assert module_name("shop/__init__.py") == "shop"


def test_python_symbols_scopes_and_params(files):
    ix = build_symbol_index(files)
    f = ix.get("shop.pricing.split_bill")
    assert f and f.kind == "function" and f.line == 27 and f.end_line == 30
    assert ix.get("shop.pricing.split_bill.people").kind == "parameter"
    assert ix.get("shop.pricing.compute_total.total").kind == "variable"
    assert ix.get("shop.auth.PASSWORD_PEPPER").kind == "global"
    assert ix.imports["shop/auth.py"]["find_user"] == "shop.db.find_user"
    assert ix.enclosing("shop/pricing.py", 29).qualified_name == "shop.pricing.split_bill"


def test_syntax_errors_are_recorded():
    ix = build_symbol_index({"bad.py": "def broken(:\n"})
    assert "bad.py" in ix.parse_errors


def test_polyglot_lexical_fallback():
    ix = build_symbol_index({"web/app.js": "export function login(user, pass) {\n  return check(user)\n}\nclass Cart {\n  total(items) {\n    return 1\n  }\n}\n"})
    assert ix.get("web.app.login").kind == "function"
    assert ix.get("web.app.Cart.total").kind == "method"
    assert ix.get("web.app.login.user").kind == "parameter"


def test_call_graph_paths_and_recursion(files):
    g = CallGraph(build_symbol_index(files))
    assert "shop.auth.verify_password" in g.callers("shop.auth.hash_password")
    assert g.path("shop.pricing.bulk_price", "shop.pricing.apply_discount")
    assert g.path("shop.pricing.bulk_price", "shop.pricing.line_total") is None
    assert g.is_recursive("shop.pricing.bulk_price")[0] is False
    rec = CallGraph(build_symbol_index({"m.py": "def f(n):\n    return f(n - 1) if n else 0\n"}))
    assert rec.is_recursive("m.f")[0] is True


def test_symbol_resolver_catches_hallucinations(files):
    r = SymbolResolver(build_symbol_index(files))
    assert r.resolve("validate_user", "shop/auth.py").status == "hallucinated"
    assert "find_user" in r.resolve("validate_user", "shop/auth.py").suggestions
    assert r.resolve("pricing.round_currency", "shop/pricing.py").status == "hallucinated"
    assert r.resolve("hashlib.sha3_1024", "shop/auth.py").status == "hallucinated"
    assert r.resolve("hashlib.sha256", "shop/auth.py").status == "builtin"
    assert r.resolve("find_user", "shop/auth.py").status == "resolved"
    assert r.resolve("quantity_requested", "shop/inventory.py", role="parameter", scope_hint="shop.inventory.reserve").status == "hallucinated"
    loc = r.check_location("shop/db.py", 410, 415, [])
    assert loc.file_exists and not loc.lines_valid
    assert not r.check_location("shop/payments.py", 1, 2, []).file_exists

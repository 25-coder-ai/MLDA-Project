"""Built-in demonstration scenarios.

Every scenario runs against the real ``examples/shopcart`` codebase with the kind of
review text, patches and LLM-judge transcripts that AI assistants actually produce.
Line numbers are exact, so every verdict is reproducible.
"""
from __future__ import annotations

from typing import Dict, List

# --------------------------------------------------------------------------- patches
P_HALLUCINATED_CONTEXT = {
    "patch_id": "P-CTX",
    "target_file": "shop/pricing.py",
    "original_code_snippet": "pct = clamp_percentage(pct)",
    "intended_fix_description": "Add currency-aware rounding to apply_discount.",
    "suggested_diff": """--- a/shop/pricing.py
+++ b/shop/pricing.py
@@ -7,6 +7,6 @@
-def apply_discount(price, pct, currency="USD"):
+def apply_discount(price, pct, currency="USD", rounding=2):
     \"\"\"Return the price after a percentage discount, capped at MAX_DISCOUNT_PCT.\"\"\"
     if price < 0:
         raise ValueError("price must be non-negative")
-    pct = clamp_percentage(pct)
-    return round(price * (100 - pct) / 100, 2)
+    pct = clamp_percentage(pct, MAX_DISCOUNT_PCT)
+    return round(price * (100 - pct) / 100, rounding)
""",
}

P_PHANTOM_HELPER = {
    "patch_id": "P-HELPER",
    "target_file": "shop/db.py",
    "original_code_snippet": "query = f\"SELECT * FROM users WHERE username = '{username}'\"",
    "intended_fix_description": "Fix the SQL injection in find_user by sanitising the username.",
    "requirement_ids": ["REQ-002"],
    "suggested_diff": """--- a/shop/db.py
+++ b/shop/db.py
@@ -20,4 +20,4 @@
 def find_user(conn, username):
-    query = f"SELECT * FROM users WHERE username = '{username}'"
+    query = f"SELECT * FROM users WHERE username = '{sanitize_sql(username)}'"
     row = conn.execute(query).fetchone()
     return dict(row) if row else None
""",
}

P_FLOOR_REGRESSION = {
    "patch_id": "P-FLOOR",
    "target_file": "shop/pricing.py",
    "intended_fix_description": "Prevent ZeroDivisionError in split_bill and use integer arithmetic for money.",
    "fixes_tests": ["tests/test_pricing.py::test_split_bill_zero_people_raises"],
    "requirement_ids": ["REQ-003"],
    "suggested_diff": """--- a/shop/pricing.py
+++ b/shop/pricing.py
@@ -27,4 +27,6 @@
 def split_bill(amount, people):
     \"\"\"Split an amount evenly, returning each person's share.\"\"\"
-    share = amount / people
+    if people <= 0:
+        raise ValueError("people must be positive")
+    share = amount // people
     return round(share, 2)
""",
}

P_WEAK_HASH = {
    "patch_id": "P-MD5",
    "target_file": "shop/auth.py",
    "original_code_snippet": 'PASSWORD_PEPPER = "s3cr3t-pepper-2024"',
    "intended_fix_description": "Remove the hard-coded pepper (load it from the environment) and speed up hashing.",
    "requirement_ids": ["REQ-006"],
    "suggested_diff": """--- a/shop/auth.py
+++ b/shop/auth.py
@@ -1,14 +1,15 @@
 \"\"\"Password hashing and login.\"\"\"
 import hashlib
 import hmac
+import os

 from shop.db import find_user

-PASSWORD_PEPPER = "s3cr3t-pepper-2024"
+PASSWORD_PEPPER = os.environ.get("SHOP_PEPPER", "")


 def hash_password(password, salt):
-    return hashlib.sha256((salt + password + PASSWORD_PEPPER).encode()).hexdigest()
+    return hashlib.md5((salt + password + PASSWORD_PEPPER).encode()).hexdigest()
 
 
 def verify_password(password, salt, expected_hash):
""",
}

P_AUTH_BYPASS = {
    "patch_id": "P-ADMIN",
    "target_file": "shop/auth.py",
    "intended_fix_description": "Speed up admin logins by skipping the expensive hash for trusted admin accounts. All tests pass.",
    "suggested_diff": """--- a/shop/auth.py
+++ b/shop/auth.py
@@ -22,6 +22,8 @@
     user = find_user(conn, username)
     if user is None:
         return None
+    if user.get("role") == "admin":
+        return user
     if verify_password(password, user["salt"], user["password_hash"]):
         return user
     return None
""",
}

P_SQL_GOOD = {
    "patch_id": "P-SQL",
    "target_file": "shop/db.py",
    "original_code_snippet": "row = conn.execute(query).fetchone()",
    "intended_fix_description": "Use a parameterised query in find_user (fixes the SQL injection, REQ-002).",
    "requirement_ids": ["REQ-002"],
    "suggested_diff": """--- a/shop/db.py
+++ b/shop/db.py
@@ -20,4 +20,3 @@
 def find_user(conn, username):
-    query = f"SELECT * FROM users WHERE username = '{username}'"
-    row = conn.execute(query).fetchone()
+    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
     return dict(row) if row else None
""",
}

P_RESERVE_PARTIAL = {
    "patch_id": "P-RES-PARTIAL",
    "target_file": "shop/inventory.py",
    "intended_fix_description": "Implement REQ-007: reject invalid reservation quantities.",
    "requirement_ids": ["REQ-007"],
    "suggested_diff": """--- a/shop/inventory.py
+++ b/shop/inventory.py
@@ -24,7 +24,9 @@
 def reserve(stock, sku, qty):
     \"\"\"Reserve qty units of sku and return the remaining count.\"\"\"
+    if qty < 0:
+        raise ValueError("quantity must be positive")
     available = stock.get(sku, 0)
     if qty > available:
         raise ValueError("insufficient stock")
     stock[sku] = available - qty
     return stock[sku]
""",
}

P_SPLIT_PARTIAL = {
    "patch_id": "P-SPLIT-PARTIAL",
    "target_file": "shop/pricing.py",
    "intended_fix_description": "Fix test_split_bill_zero_people_raises (REQ-003).",
    "fixes_tests": ["tests/test_pricing.py::test_split_bill_zero_people_raises"],
    "requirement_ids": ["REQ-003"],
    "suggested_diff": """--- a/shop/pricing.py
+++ b/shop/pricing.py
@@ -27,4 +27,6 @@
 def split_bill(amount, people):
     \"\"\"Split an amount evenly, returning each person's share.\"\"\"
+    if people < 0:
+        raise ValueError("people must be positive")
     share = amount / people
     return round(share, 2)
""",
}

P_RESERVE_GOOD = {
    "patch_id": "P-RES",
    "target_file": "shop/inventory.py",
    "intended_fix_description": "Reject non-positive reservation quantities (REQ-007).",
    "requirement_ids": ["REQ-007"],
    "suggested_diff": """--- a/shop/inventory.py
+++ b/shop/inventory.py
@@ -24,7 +24,9 @@
 def reserve(stock, sku, qty):
     \"\"\"Reserve qty units of sku and return the remaining count.\"\"\"
+    if qty <= 0:
+        raise ValueError("quantity must be positive")
     available = stock.get(sku, 0)
     if qty > available:
         raise ValueError("insufficient stock")
     stock[sku] = available - qty
     return stock[sku]
""",
}

P_TOTAL_GUARD = {
    "patch_id": "P-TOTAL",
    "target_file": "shop/pricing.py",
    "intended_fix_description": "Reject negative quantities in compute_total. Existing tests pass.",
    "suggested_diff": """--- a/shop/pricing.py
+++ b/shop/pricing.py
@@ -19,6 +19,8 @@
 def compute_total(items, tax_rate=TAX_RATE):
     \"\"\"Sum all line totals and add tax.\"\"\"
     total = 0
     for item in items:
+        if item["qty"] < 0:
+            raise ValueError("quantity cannot be negative")
         total += line_total(item)
     return round(total * (1 + tax_rate), 2)
""",
}

P_CENTS_NOVEL = {
    "patch_id": "P-CENTS",
    "target_file": "shop/pricing.py",
    "intended_fix_description": "Fix the ZeroDivisionError in split_bill (REQ-003) and switch to exact integer-cent arithmetic. All tests pass, including the previously failing one.",
    "fixes_tests": ["tests/test_pricing.py::test_split_bill_zero_people_raises"],
    "requirement_ids": ["REQ-003"],
    "suggested_diff": """--- a/shop/pricing.py
+++ b/shop/pricing.py
@@ -27,7 +27,9 @@
 def split_bill(amount, people):
     \"\"\"Split an amount evenly, returning each person's share.\"\"\"
-    share = amount / people
-    return round(share, 2)
+    if people <= 0:
+        raise ValueError("people must be a positive integer")
+    cents = int(amount * 100)
+    return cents // people / 100
 
 
 def bulk_price(unit_price, qty):
""",
}

# --------------------------------------------------------------------------- scenarios
SCENARIOS: List[Dict] = [
    {
        "id": "hallucinated-references",
        "title": "Hallucinated references",
        "tagline": "Files, functions, parameters, APIs and requirement IDs that do not exist.",
        "modes": ["hallucinated_reference", "fake_symbol"],
        "review_text": """[C1] shop/auth.py:18-27 (Bug) `validate_user()` swallows the database exception, so `authenticate` returns None for every login during outages.
[C2] shop/payments.py:40-52 (Security) The `charge_card()` helper logs the full card number.
[C3] shop/pricing.py:7-12 (Bug) `apply_discount` calls `pricing.round_currency()`, which truncates instead of rounding.
[C4] shop/db.py:410-415 (Performance) `get_orders` loads the whole orders table into memory.
[C5] shop/inventory.py:24-30 (Bug) In `reserve`, the parameter `quantity_requested` is never validated against `stock_level`.
[C6] shop/auth.py:10-11 (Security) `hash_password` should call `hashlib.sha3_1024` instead of SHA-256.
[C7] shop/pricing.py:27-30 (Requirement) `split_bill` violates REQ-042 (currency rounding rules).""",
        "patches": [P_HALLUCINATED_CONTEXT],
        "verifier": [],
    },
    {
        "id": "unsupported-claims",
        "title": "Unsupported review claims",
        "tagline": "Tri-state judging: supported, contradicted, or simply unsupported.",
        "modes": ["unsupported_claim", "semantic_hallucination"],
        "review_text": """[C1] shop/db.py:20-23 (Security) `find_user` interpolates `username` into the SQL text with an f-string — classic SQL injection.
[C2] shop/pricing.py:19-24 (Performance) `compute_total` leaks memory because it keeps a reference to every `item`.
[C3] shop/db.py:26-28 (Security) `get_orders` is vulnerable to SQL injection through `username`.
[C4] shop/auth.py:14-15 (Security) `verify_password` compares hashes with `==`, enabling timing attacks.
[C5] shop/inventory.py:20-21 (Style) `low_stock` has a confusing name and should be refactored.
[C6] shop/auth.py:7 (Security) `PASSWORD_PEPPER` is a hardcoded secret committed to source control.
[C7] shop/pricing.py:27-30 (Bug) `split_bill` has a race condition and is not thread-safe.""",
        "patches": [],
        "verifier": [],
    },
    {
        "id": "requirement-mismatch",
        "title": "Code vs requirement mismatch",
        "tagline": "Executable acceptance clauses expose fixes that miss the spec — and reviews that misstate it.",
        "modes": ["requirement_mismatch"],
        "review_text": """[C1] shop/pricing.py:7-12 (Requirement) `apply_discount` violates REQ-001 because discounts above 50% are not capped.
[C2] shop/pricing.py:27-30 (Requirement) `split_bill` complies with REQ-003 and safely handles every party size.
[C3] shop/inventory.py:24-30 (Requirement) `reserve` satisfies REQ-007.""",
        "patches": [P_RESERVE_PARTIAL, P_SPLIT_PARTIAL],
        "verifier": [],
    },
    {
        "id": "bad-fixes",
        "title": "AI fix introduces another bug",
        "tagline": "Regressions, phantom helpers, weakened crypto and an auth bypass that passes every test.",
        "modes": ["bad_fix", "fake_symbol"],
        "review_text": "",
        "patches": [P_PHANTOM_HELPER, P_FLOOR_REGRESSION, P_WEAK_HASH, P_AUTH_BYPASS],
        "verifier": [],
    },
    {
        "id": "semantic-hallucinations",
        "title": "Semantic hallucinations",
        "tagline": "Real symbols, false statements about what they do.",
        "modes": ["semantic_hallucination", "unsupported_claim"],
        "review_text": """[C1] shop/pricing.py:19-24 (Bug) In `compute_total`, `total` is used before assignment when `items` is empty.
[C2] shop/inventory.py:4-12 (Bug) `parse_quantity` does not handle the ValueError raised by `int()` on malformed input.
[C3] shop/auth.py:10-11 (Style) `hash_password` is never called and should be deleted as dead code.
[C4] shop/pricing.py:33-39 (Bug) `bulk_price` is recursive and will overflow the stack for large orders.
[C5] shop/pricing.py:15-16 (Bug) `line_total` returns a dict instead of a number.
[C6] shop/auth.py:18-27 (Bug) `authenticate` never checks for None before indexing `user`.
[C7] shop/pricing.py:33-39 (Impact) Changing `line_total` will break `bulk_price`.
[C8] shop/db.py:5-8 (Bug) `find_user` leaks the file handle because the connection is never closed.
[C9] shop/pricing.py:27-30 (Bug) `split_bill` has a division by zero when `people` is 0.""",
        "patches": [],
        "verifier": [],
    },
    {
        "id": "tests-dont-cover",
        "title": "Tests don't cover the issue",
        "tagline": "Green test suites that never execute the fix — proven with coverage and surviving mutants.",
        "modes": ["inadequate_tests"],
        "review_text": "",
        "patches": [P_RESERVE_GOOD, P_TOTAL_GUARD],
        "verifier": [],
    },
    {
        "id": "verifier-same-mistake",
        "title": "LLM verifier makes the same mistake",
        "tagline": "An LLM judge rubber-stamps the generator's hallucinations — measured as correlated error.",
        "modes": ["verifier_same_mistake"],
        "review_text": """[C1] shop/auth.py:18-27 (Bug) `validate_user()` swallows the database exception, so `authenticate` returns None during outages.
[C2] shop/db.py:26-28 (Security) `get_orders` is vulnerable to SQL injection through `username`.
[C3] shop/db.py:20-23 (Security) `find_user` interpolates `username` into the SQL text with an f-string — classic SQL injection.
[C4] shop/auth.py:10-11 (Style) `hash_password` is never called and should be deleted as dead code.
[C5] shop/inventory.py:4-12 (Bug) `parse_quantity` does not handle the ValueError raised by `int()`.""",
        "patches": [],
        "verifier": [
            {"claim_id": "C1", "verdict": "confirmed", "model": "judge-llm", "rationale": "Confirmed. validate_user() catches the database exception and returns None, so authenticate returns None during outages."},
            {"claim_id": "C2", "verdict": "confirmed", "model": "judge-llm", "rationale": "Agreed, get_orders is vulnerable to SQL injection through username."},
            {"claim_id": "C3", "verdict": "confirmed", "model": "judge-llm", "rationale": "Correct: line 21 builds the query with an f-string and line 22 executes it; bandit/semgrep style rule for SQL injection applies."},
            {"claim_id": "C4", "verdict": "confirmed", "model": "judge-llm", "rationale": "Yes, hash_password is never called and is dead code that should be deleted."},
            {"claim_id": "C5", "verdict": "confirmed", "model": "judge-llm", "rationale": "Right, parse_quantity does not handle the ValueError raised by int()."},
        ],
    },
    {
        "id": "confirmation-bias",
        "title": "LLM confirmation bias",
        "tagline": "The judge agrees with a claim — and with its engine-generated negation.",
        "modes": ["confirmation_bias"],
        "review_text": """[C1] shop/pricing.py:27-30 (Bug) `split_bill` has a division by zero when `people` is 0.
[C2] shop/pricing.py:7-12 (Bug) `apply_discount` does not cap discounts above 50%.
[C3] shop/inventory.py:20-21 (Performance) `low_stock` is O(n^2) because of nested loops.
[C4] shop/pricing.py:19-24 (Bug) `compute_total` never handles an empty cart.""",
        "patches": [],
        "verifier": [
            {"claim_id": "C1", "verdict": "confirmed", "counterfactual_verdict": "rejected", "model": "judge-llm", "rationale": "Line 29 divides by people without a guard; test_split_bill_zero_people_raises fails with ZeroDivisionError."},
            {"claim_id": "C2", "verdict": "confirmed", "counterfactual_verdict": "confirmed", "model": "judge-llm", "rationale": "Yes, apply_discount does not cap discounts above 50%."},
            {"claim_id": "C3", "verdict": "confirmed", "counterfactual_verdict": "confirmed", "model": "judge-llm", "rationale": "Agreed: low_stock is O(n^2) because of nested loops."},
            {"claim_id": "C4", "verdict": "confirmed", "counterfactual_verdict": "confirmed", "model": "judge-llm", "rationale": "compute_total never handles an empty cart, as stated."},
        ],
    },
    {
        "id": "novel-bugs",
        "title": "Unknown / novel bugs",
        "tagline": "A fix that passes every test and requirement — and silently loses a cent.",
        "modes": ["novel_bug"],
        "review_text": "",
        "patches": [P_CENTS_NOVEL],
        "verifier": [],
    },
    {
        "id": "prove-correct",
        "title": "“Prove this code is correct”",
        "tagline": "Correctness claims become proof obligations: proved (bounded), refuted, or unproven.",
        "modes": ["unproven_correctness"],
        "review_text": """[C1] shop/pricing.py:33-39 (Correctness) `bulk_price` is correct: it handles all volume tiers and larger orders never cost less.
[C2] shop/pricing.py:7-12 (Correctness) `apply_discount` is provably correct for every price and percentage.
[C3] shop/inventory.py:24-30 (Correctness) `reserve` is bug-free.
[C4] shop/inventory.py:15-17 (Correctness) `restock` is correct.""",
        "patches": [],
        "verifier": [],
    },
    {
        "id": "full-audit",
        "title": "Full audit (everything)",
        "tagline": "A realistic AI review + four AI patches + an LLM judge, all at once.",
        "modes": ["hallucinated_reference", "fake_symbol", "unsupported_claim", "semantic_hallucination", "requirement_mismatch", "bad_fix", "inadequate_tests", "verifier_same_mistake", "confirmation_bias", "novel_bug", "unproven_correctness"],
        "review_text": """[C1] shop/db.py:20-23 (Security) `find_user` interpolates `username` into the SQL text with an f-string — classic SQL injection.
[C2] shop/auth.py:18-27 (Bug) `validate_user()` swallows the database exception, so `authenticate` returns None during outages.
[C3] shop/pricing.py:19-24 (Bug) In `compute_total`, `total` is used before assignment when `items` is empty.
[C4] shop/pricing.py:19-24 (Performance) `compute_total` leaks memory because it keeps a reference to every `item`.
[C5] shop/inventory.py:24-30 (Requirement) `reserve` satisfies REQ-007.
[C6] shop/pricing.py:33-39 (Correctness) `bulk_price` is correct and handles all volume tiers.
[C7] shop/auth.py:7 (Security) `PASSWORD_PEPPER` is a hardcoded secret committed to source control.
[C8] shop/payments.py:12-18 (Security) `charge_card()` writes full card numbers to the log.""",
        "patches": [P_SQL_GOOD, P_AUTH_BYPASS, P_RESERVE_GOOD, P_CENTS_NOVEL],
        "verifier": [
            {"claim_id": "C1", "verdict": "confirmed", "counterfactual_verdict": "rejected", "model": "judge-llm", "rationale": "Line 21 builds the query with an f-string; executed on line 22."},
            {"claim_id": "C2", "verdict": "confirmed", "counterfactual_verdict": "confirmed", "model": "judge-llm", "rationale": "Confirmed: validate_user() swallows the database exception so authenticate returns None."},
            {"claim_id": "C3", "verdict": "confirmed", "counterfactual_verdict": "confirmed", "model": "judge-llm", "rationale": "Yes, total is used before assignment when items is empty."},
            {"claim_id": "C4", "verdict": "confirmed", "model": "judge-llm", "rationale": "Agreed, compute_total leaks memory by keeping a reference to every item."},
            {"claim_id": "C6", "verdict": "confirmed", "counterfactual_verdict": "confirmed", "model": "judge-llm", "rationale": "bulk_price is correct and handles all volume tiers."},
            {"claim_id": "C7", "verdict": "confirmed", "counterfactual_verdict": "rejected", "model": "judge-llm", "rationale": "shop/auth.py line 7 assigns a literal pepper; bandit B105 flags it."},
        ],
    },
]

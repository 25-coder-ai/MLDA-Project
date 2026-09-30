# ShopCart Checkout — Software Requirements Specification

Executable acceptance clauses (`check:`, `forbid:`, `prove:` + `domain:`) turn each
requirement into a deterministic oracle for TRUST-CODE.

## REQ-001 · Discount cap
Discounts are capped at 50% of the list price. A discounted price must never be negative and never exceed the original price.
- symbols: shop.pricing.apply_discount
- check: apply_discount(100, 80) == 50
- check: apply_discount(100, 20) == 80
- prove: apply_discount(price, pct) => price * 0.5 <= result <= price
- domain: price = range(0, 501, 5); pct = range(-20, 151, 5)

## REQ-002 · Parameterised data access
All database queries must use parameterised statements; user input must never be interpolated into SQL text.
- symbols: shop.db.find_user, shop.db.get_orders
- setup: from shop import db; conn = db.connect(); db.init_schema(conn); db.add_user(conn, "alice", "s", "h")
- check: find_user(conn, "x' OR '1'='1") is None
- check: find_user(conn, "alice")["username"] == "alice"
- forbid: TC-SQLI

## REQ-003 · Bill splitting
Splitting a bill between zero or a negative number of people must raise ValueError. Individual shares must reconcile with the bill total to within one cent per person.
- symbols: shop.pricing.split_bill
- check: raises(ValueError, split_bill, 100, 0)
- check: raises(ValueError, split_bill, 100, -2)
- prove: split_bill(amount, people) => abs(result * people - amount) <= 0.01 * people
- domain: amount = range(0, 1000, 7); people = range(1, 13)

## REQ-004 · Authentication
Every login must verify the salted password hash; empty credentials are always rejected.
- symbols: shop.auth.authenticate
- setup: from shop import db; from shop.auth import hash_password; conn = db.connect(); db.init_schema(conn); db.add_user(conn, "alice", "NaCl", hash_password("pw", "NaCl"), "admin")
- check: authenticate(conn, "alice", "wrong") is None
- check: authenticate(conn, "alice", "") is None
- check: authenticate(conn, "alice", "pw")["username"] == "alice"
- forbid: TC-AUTHBYPASS

## REQ-005 · Volume pricing monotonicity
Buying more units must never cost less than buying fewer units (no volume-pricing cliffs).
- symbols: shop.pricing.bulk_price
- check: bulk_price(10, 10) == 90
- prove: bulk_price(unit, qty + 1) >= bulk_price(unit, qty)
- domain: unit = [1, 2, 5, 10, 25]; qty = range(0, 200)

## REQ-006 · Secret management
Secrets such as peppers, keys and passwords must be loaded from the environment, never hard-coded in source.
- symbols: shop.auth
- forbid: TC-SECRET

## REQ-007 · Inventory reservations
Reserving stock must reject non-positive quantities and must never drive stock negative.
- symbols: shop.inventory.reserve
- check: raises(ValueError, reserve, {"A": 5}, "A", 0)
- check: raises(ValueError, reserve, {"A": 5}, "A", -3)
- check: raises(ValueError, reserve, {"A": 5}, "A", 6)
- check: reserve({"A": 5}, "A", 2) == 3
- prove: reserve({"A": stock}, "A", qty) => result >= 0
- domain: stock = range(0, 30); qty = range(-10, 40)
- allow: ValueError

## Non-functional notes
Checkout latency should stay under 200 ms at p95. (Prose-only: not executable.)

import time

from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


def test_health_and_static():
    h = client.get("/api/v1/health").json()
    assert h["status"] == "ok" and len(h["failure_modes"]) == 11
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_scenarios_and_parse():
    ids = [s["id"] for s in client.get("/api/v1/scenarios").json()]
    assert "full-audit" in ids
    assert client.get("/api/v1/scenarios/nope").status_code == 404
    parsed = client.post("/api/v1/claims/parse", json={"review_text": "[C1] a/b.py:1-2 (Bug) `x` is recursive"}).json()
    assert parsed["claims"][0]["claim_id"] == "C1" and parsed["counterfactuals"]["C1"]


def test_prove_endpoint(files):
    body = {"workspace": {"files": files}, "function": "shop.pricing.bulk_price", "call": "bulk_price(u, q + 1) >= bulk_price(u, q)", "post": "", "domain": "u = [1, 5]; q = range(0, 120)"}
    cert = client.post("/api/v1/prove", json=body).json()
    assert cert["status"] == "REFUTED" and cert["counterexample"]["inputs"]["q"] == "99"
    body["function"] = "shop.pricing.round_currency"
    assert client.post("/api/v1/prove", json=body).status_code == 422


def test_job_lifecycle_and_markdown(files):
    payload = {"workspace": {"files": files}, "review_text": "[C1] shop/pricing.py:27-30 (Bug) `split_bill` has a division by zero when `people` is 0.", "options": {"run_mutation": False}}
    jid = client.post("/api/v1/jobs", json=payload).json()["job_id"]
    for _ in range(120):
        job = client.get(f"/api/v1/jobs/{jid}?include_report=false").json()
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.5)
    assert job["status"] == "done"
    report = client.get(f"/api/v1/jobs/{jid}").json()["report"]
    assert report["claims"][0]["state"] == "SUPPORTED"
    md = client.post("/api/v1/report/markdown", json=report).text
    assert "# TRUST-CODE report" in md


def test_payload_validation():
    assert client.post("/api/v1/verify", json={"workspace": {}}).status_code == 422
    assert client.post("/api/v1/verify", json={"workspace": {"files": {"../evil.py": "x"}}}).status_code == 422

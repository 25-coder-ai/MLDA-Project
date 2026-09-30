"""Streamlit internal / dev console (SOP Step 5.4).

    streamlit run src/ui/dashboard.py

Runs the engine in-process (no API server needed) and visualises the trust-score
breakdown, claim verdicts, patch validation, the evidence ledger and the call graph.
The richer public console is served by FastAPI at http://127.0.0.1:8000.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from src.models.ingestion import AIEvaluationPayload  # noqa: E402
from src.pipeline import TrustPipeline  # noqa: E402
from src.reporting import to_markdown  # noqa: E402
from src.scenarios import SCENARIOS, scenario_payload  # noqa: E402

st.set_page_config(page_title="TRUST-CODE dev console", page_icon="🛡️", layout="wide")
VERDICT_ICON = {"TRUSTED": "🟢", "UNCERTAIN": "🟡", "REJECTED": "🔴"}

st.title("🛡️ TRUST-CODE — dev console")
st.caption("Deterministic, evidence-based verification of AI code reviews and AI-generated patches.")

with st.sidebar:
    st.header("Scenario")
    sid = st.selectbox("Built-in scenario", [s["id"] for s in SCENARIOS], format_func=lambda i: next(s["title"] for s in SCENARIOS if s["id"] == i))
    payload = scenario_payload(sid)
    review = st.text_area("AI review", payload["review_text"], height=220)
    st.caption(f"{len(payload['patches'])} patch(es) · {len(payload['verifier'])} judge verdict(s)")
    col1, col2 = st.columns(2)
    run_mut = col1.checkbox("Mutation", True)
    run_probe = col2.checkbox("Probing", True)
    go = st.button("▶ Run verification", type="primary", width="stretch")

if go:
    payload["review_text"] = review
    payload["options"] = {"run_mutation": run_mut, "run_behavior_probe": run_probe}
    progress = st.progress(0, text="starting")
    report = TrustPipeline(on_event=lambda e: progress.progress(min(100, int(e["progress"])), text=f"{e['stage']}: {e['message']}")).run(AIEvaluationPayload(**payload))
    progress.empty()
    st.session_state["report"] = report

report = st.session_state.get("report")
if report is None:
    st.info("Choose a scenario and press **Run verification**.")
    st.stop()

s = report.summary
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Trust index", f"{s.trust_index:.1f}")
c2.metric("Trusted", s.trusted)
c3.metric("Uncertain", s.uncertain)
c4.metric("Rejected", s.rejected)
c5.metric("Hard gates", s.hard_gates)
st.write(s.headline)

st.subheader("Failure-mode detection")
st.dataframe(pd.DataFrame([{"failure mode": r.label, "detected": r.detected, "where": ", ".join(r.refs)} for r in report.failure_modes]), hide_index=True, width="stretch")

tab_c, tab_p, tab_j, tab_r, tab_l, tab_g = st.tabs(["Claims", "Patches", "LLM judge", "Requirements", "Ledger", "Call graph"])
with tab_c:
    for c in report.claims:
        with st.expander(f"{VERDICT_ICON[c.score.verdict]} {c.claim.claim_id} · {c.state} · {c.score.final:.0f} — {c.claim.claim_text[:110]}"):
            st.markdown("\n".join(f"- {e}" for e in c.explanations))
            st.bar_chart(pd.DataFrame({"delta": {x.key: x.delta for x in c.score.components}}))
            if c.predicates:
                st.dataframe(pd.DataFrame([p.model_dump(include={"label", "subject", "asserted", "actual", "outcome", "evidence"}) for p in c.predicates]), hide_index=True)
with tab_p:
    for p in report.patches:
        gates = [g.gate for g in p.score.gates if g.triggered]
        with st.expander(f"{VERDICT_ICON[p.score.verdict]} {p.patch.patch_id} · {p.score.final:.0f} · {', '.join(gates) or 'no gates'}"):
            st.markdown("\n".join(f"- {e}" for e in p.explanations))
            st.code(p.patch.suggested_diff, language="diff")
            if p.tests.matrix:
                st.dataframe(pd.DataFrame([m.model_dump(exclude={"message"}) for m in p.tests.matrix]), hide_index=True)
            if p.adequacy.mutants:
                st.dataframe(pd.DataFrame([m.model_dump() for m in p.adequacy.mutants]), hide_index=True)
with tab_j:
    if report.verifier_audit:
        st.write(report.verifier_audit.conclusion)
        st.json(report.verifier_audit.metrics.model_dump())
        st.dataframe(pd.DataFrame(report.verifier_audit.entries).drop(columns=["flags"]), hide_index=True)
    else:
        st.info("No LLM-judge transcript in this scenario.")
with tab_r:
    st.dataframe(pd.DataFrame([{"id": r.req_id, "title": r.title, "status": r.status, "clauses": len(r.results)} for r in report.requirements]), hide_index=True)
with tab_l:
    st.write(f"Seal `{report.ledger.seal_hash}` · chain {'intact ✅' if report.ledger.intact else 'BROKEN ❌'}")
    st.dataframe(pd.DataFrame([e.model_dump() for e in report.ledger.entries]), hide_index=True)
with tab_g:
    dot = ["digraph G { rankdir=LR; node [shape=box, style=rounded, fontsize=10];"]
    for n in report.call_graph.nodes:
        if not n.id.startswith("tests"):
            dot.append(f'"{n.id}" [label="{n.label}"];')
    for e in report.call_graph.edges:
        if not e.source.startswith("tests"):
            dot.append(f'"{e.source}" -> "{e.target}" [style={"dashed" if e.kind == "import" else "solid"}];')
    dot.append("}")
    st.graphviz_chart("\n".join(dot))

st.download_button("Download Markdown report", to_markdown(report), file_name=f"{report.report_id}.md")
st.download_button("Download JSON report", json.dumps(report.model_dump(mode="json"), indent=2), file_name=f"{report.report_id}.json")

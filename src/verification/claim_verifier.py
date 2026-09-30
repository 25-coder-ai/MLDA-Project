"""Module 3 — review & claims verification (Steps 3.1–3.4 orchestrated per claim).

Each AI review claim is judged only against the sealed ground truth:

1. entity deconstruction → 2. symbol & location resolution (zero tolerance)
→ 3. semantic predicates / call-graph flow → 4. static evidence within ±5 lines of
the cited range, scoped to the subject function and the claim's defect family
→ 5. requirement compliance & bounded proofs → tri-state classification + score.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

from config.settings import settings
from src.evidence.evidence_store import GroundTruthDatabase
from src.models.evidence import ProofCertificate, StaticFinding
from src.models.ingestion import AIReviewClaim
from src.models.scoring import ClaimVerdict, PredicateResult, RequirementLink, SymbolResolution
from src.scoring.score_engine import compute
from src.verification.call_graph import CallGraph
from src.verification.claim_extractor import counterfactual, extract_entities
from src.verification.semantic_checker import (
    CORRECTNESS_NEG,
    CORRECTNESS_POS,
    REQ_NEG,
    REQ_POS,
    UNIVERSAL,
    _pick_function,
    detect_polarity,
    evaluate_predicates,
)
from src.verification.symbol_resolver import SymbolResolver

# predicate kind -> static rule families that corroborate it
RULE_FAMILIES: Dict[str, Set[str]] = {
    "sql_parameterized": {"TC-SQLI", "B608"},
    "no_hardcoded_secret": {"TC-SECRET", "B105", "B106", "B107"},
    "no_command_injection": {"TC-CMDI", "B602", "B605", "B607", "B404"},
    "no_eval": {"TC-EVAL", "B307", "B102"},
    "no_unsafe_deserialization": {"TC-DESER", "B301", "B403", "B506"},
    "strong_hash": {"TC-WEAKHASH", "B303", "B324"},
    "auth_enforced": {"TC-AUTHBYPASS"},
    "no_resource_leak": {"TC-RESOURCE"},
    "zero_division_guarded": {"TC-DIVZERO"},
    "nested_loops": {"TC-NESTEDLOOP"},
    "handles_exceptions": {"TC-BAREEXCEPT", "B110"},
    "no_mutable_default": {"TC-MUTDEFAULT", "B006"},
}
_CATEGORY_OK = {
    "Security": lambda f: f.security,
    "Bug": lambda f: f.category in ("Bug", "Security"),
    "Performance": lambda f: f.category == "Performance",
    "Style": lambda f: False,
}


_TOPIC_STOP = frozenset("the and that this with from into have has for are was were will would should could not but its when where what which there their than then also only more most very line lines code function method value values call calls called using used uses because without never must always every each does doesn".split())


def _topic_words(text: str) -> Set[str]:
    text = re.sub(r"`[^`]*`", " ", text)
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in _TOPIC_STOP}


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: -len(suf)]
            break
    if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "aeiou":
        w = w[:-1]
    return w


def _stems(text: str, exclude: Set[str] = frozenset()) -> Set[str]:  # type: ignore[assignment]
    text = re.sub(r"`[^`]*`", " ", text).replace("_", " ")
    return {_stem(w) for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in _TOPIC_STOP and _stem(w) not in exclude}


class ClaimVerifier:
    def __init__(self, db: GroundTruthDatabase, graph: CallGraph) -> None:
        self.db = db
        self.graph = graph
        self.ix = db.index
        self.resolver = SymbolResolver(db.index)

    # ------------------------------------------------------------------ helpers
    def _infer_location(self, claim: AIReviewClaim, res: List[SymbolResolution]) -> None:
        if claim.target_file:
            return
        for r in res:
            for m in r.matches:
                sym = self.ix.get(m.qualified_name)
                if sym and sym.kind in ("function", "method", "class"):
                    claim.target_file, claim.target_line_start, claim.target_line_end = sym.file, sym.line, sym.end_line
                    return

    def _static_evidence(self, claim: AIReviewClaim, predicates: List[PredicateResult], func_q: Optional[str]) -> List[StaticFinding]:
        near = self.db.findings_near(claim.target_file, claim.target_line_start, claim.target_line_end, settings.evidence_line_window)
        if func_q:
            near = [f for f in near if f.function in (None, func_q) or (f.function or "").startswith(func_q + ".")]
        families: Set[str] = set()
        for p in predicates:
            if p.kind in RULE_FAMILIES and p.asserted is False:
                families |= RULE_FAMILIES[p.kind]
            if p.kind == "nested_loops" and p.asserted:
                families |= RULE_FAMILIES["nested_loops"]
        if families:
            return [f for f in near if f.rule_id in families]
        if predicates:
            return []  # the claim is about something no static rule speaks to
        ok = _CATEGORY_OK.get(claim.claimed_defect_category)
        if not ok:
            return []
        words = _topic_words(claim.claim_text)
        return [f for f in near if ok(f) and (not func_q or f.function == func_q) and words & _topic_words(f"{f.rule_id} {f.message} {f.cwe or ''}")]

    def _requirement_links(self, claim: AIReviewClaim, func_q: Optional[str], defect_claim: bool) -> Tuple[List[RequirementLink], Optional[bool], str]:
        text = claim.claim_text
        asserted, phrase = detect_polarity(text, REQ_POS, REQ_NEG)
        explicit = {r.upper() for r in re.findall(r"\bREQ-\d{1,4}\b", text, re.I)}
        links: Dict[str, RequirementLink] = {}
        for rid in explicit:
            st = self.db.requirement(rid)
            links[rid] = RequirementLink(req_id=rid, title=st.title if st else "(not found in specification)", similarity=1.0, explicit=True, status=st.status if st else "MISSING", asserted_satisfied=asserted)
        if not explicit and (asserted is not None or claim.claimed_defect_category == "Requirement" or defect_claim):
            for node, score in self.db.req_index.search(text, k=3):
                on_symbol = bool(func_q) and any(func_q == s or func_q.endswith("." + s.split(".")[-1]) for s in node.symbols)
                if score >= settings.requirement_link_threshold or on_symbol:
                    st = self.db.requirement(node.req_id)
                    links[node.req_id] = RequirementLink(req_id=node.req_id, title=node.title, similarity=round(score, 3), explicit=False, status=st.status if st else "UNVERIFIABLE", asserted_satisfied=asserted)
                    if on_symbol:
                        break
        return list(links.values()), asserted, phrase

    def _topical_evidence(self, text: str, func_q: str, links: List[RequirementLink], counts: Dict[str, float], kinds: List[str], explanations: List[str], modes: Set[str]) -> None:
        """Passing tests / passing acceptance clauses that exercise exactly the behaviour a defect claim is about."""
        sym = self.ix.get(func_q)
        if sym is None:
            return
        name_words = {_stem(w) for w in re.findall(r"[a-z]{3,}", sym.name.lower().replace("_", " "))}
        claim = _stems(text, name_words)
        # tests that execute the subject function and whose name speaks to the claimed behaviour
        for t in self.db.tests.tests:
            if not any(sym.line <= ln <= sym.end_line for ln in t.lines.get(sym.file, [])):
                continue
            tname = t.test_id.split("::")[-1]
            overlap = claim & _stems(tname.replace("test_", " "), name_words)
            if not overlap:
                continue
            if t.outcome == "passed":
                counts["contradicted"] += 1
                explanations.append(f"✗ Behavioural evidence contradicts the claim: `{tname}` executes `{sym.name}` on exactly this behaviour ({', '.join(sorted(overlap))}) and passes.")
                modes.add("semantic_hallucination")
            elif t.outcome in ("failed", "error"):
                kinds.append("dynamic-test")
                explanations.append(f"✓ Failing test `{tname}` exercises `{sym.name}` and demonstrates the defect.")
            return
        # passing requirement whose acceptance clauses cover the function and the same topic
        for st in self.db.requirements.values():
            if st.status != "PASS" or not any(s == func_q or s.split(".")[-1] == sym.name for s in st.symbols):
                continue
            overlap = claim & _stems(f"{st.title} {st.text}", name_words)
            if len(overlap) >= 2:
                counts["contradicted"] += 1
                explanations.append(f"✗ Requirement drift: {st.req_id} ({st.title}) specifies this behaviour ({', '.join(sorted(overlap))}) and all its executable clauses PASS.")
                modes.add("requirement_mismatch")
                return

    def _fails_in_scope(self, req_id: str, func_q: Optional[str]) -> bool:
        """Is a *failing* clause of the requirement attributable to the claim's subject function?"""
        st = self.db.requirement(req_id)
        if st is None:
            return False
        if not func_q:
            return st.status == "FAIL"
        short = func_q.split(".")[-1]
        sym = self.ix.get(func_q)
        for r in st.results:
            if r.status not in ("fail", "error"):
                continue
            if r.kind in ("check", "prove") and re.search(r"\b" + re.escape(short) + r"\s*\(|\b" + re.escape(short) + r"\b,", r.expr):
                return True
            if r.kind == "forbid" and sym is not None:
                m = re.search(r"at ([\w./-]+):(\d+)", r.detail)
                if m and m.group(1) == sym.file and sym.line <= int(m.group(2)) <= sym.end_line:
                    return True
        return False

    def _proofs_for(self, func_q: Optional[str]) -> Tuple[Optional[bool], List[ProofCertificate], str]:
        if not func_q:
            return None, [], "no function subject to prove"
        short = func_q.split(".")[-1]
        related = [st for st in self.db.requirements.values() if any(s == func_q or s.split(".")[-1] == short for s in st.symbols)]
        certs = [c for st in related for c in st.proofs]
        failing = [st for st in related if st.status == "FAIL"]
        if failing:
            st = failing[0]
            bad = next((r for r in st.results if r.status in ("fail", "error")), None)
            return False, certs, f"{st.req_id} fails: {bad.kind} `{bad.expr}` → {bad.detail}" if bad else f"{st.req_id} fails"
        proved = [c for c in certs if c.status == "PROVED_BOUNDED"]
        if proved:
            return True, certs, f"bounded proof over {proved[0].cases_checked:,} inputs ({proved[0].req_id})"
        if related:
            return None, certs, "requirement checks pass but no proof obligation was discharged"
        return None, certs, f"no proof obligation or executable requirement covers `{short}`"

    # ------------------------------------------------------------------ main
    def verify(self, claim: AIReviewClaim) -> ClaimVerdict:
        text = claim.claim_text
        ent = extract_entities(text)
        tokens: List[str] = []
        for t in list(claim.referenced_symbols) + ent.tokens:
            t = t.strip().rstrip("()")
            if t and t not in tokens and t not in ent.parameters:
                tokens.append(t)
        resolutions = [self.resolver.resolve(t, claim.target_file) for t in tokens]
        self._infer_location(claim, resolutions)
        # re-resolve against the inferred file
        if claim.source == "inferred":
            resolutions = [self.resolver.resolve(t, claim.target_file) for t in tokens]
        func = _pick_function(resolutions, self.ix, claim.target_file, claim.target_line_start, claim.target_line_end, text, "")
        for p in ent.parameters:
            resolutions.append(self.resolver.resolve(p, claim.target_file, role="parameter", scope_hint=func.qualified_name if func else None))
        location = self.resolver.check_location(claim.target_file, claim.target_line_start, claim.target_line_end, resolutions)

        explanations: List[str] = []
        modes: Set[str] = set()
        counts = {"supported": 0.0, "unsupported": 0.0, "contradicted": 0.0, "hallucinated": 0.0}
        kinds: List[str] = []
        fatal: List[str] = []

        # -------- hallucinations
        if not location.file_exists:
            counts["hallucinated"] += 1
            fatal.append(location.note)
            explanations.append(f"✗ Hallucinated file: {location.note}.")
            modes.add("hallucinated_reference")
        elif not location.lines_valid:
            counts["hallucinated"] += 1
            explanations.append(f"✗ Hallucinated location: {location.note}.")
            modes.add("hallucinated_reference")
        primary = next((r for r in resolutions if r.status != "builtin" and r.role == "symbol"), None)
        for r in resolutions:
            if r.status == "hallucinated":
                counts["hallucinated"] += 1
                hint = f" Did you mean {', '.join('`' + s + '`' for s in r.suggestions)}?" if r.suggestions else ""
                explanations.append(f"✗ {'Parameter' if r.role == 'parameter' else 'Symbol'} `{r.token}` does not exist — {r.note}.{hint}")
                modes.add("fake_symbol")
                if "has no member" in r.note or "standard-library" in r.note:
                    modes.add("hallucinated_reference")
                if r is primary:
                    fatal.append(f"primary symbol `{r.token}` not found in AST")
        if not tokens and not ent.parameters and location.file_exists:
            explanations.append("• The claim cites no concrete code symbol; only location-level checks were possible.")

        # -------- semantic predicates & flow
        predicates: List[PredicateResult] = []
        certs: List[ProofCertificate] = []
        links: List[RequirementLink] = []
        statics: List[StaticFinding] = []
        if location.file_exists:
            predicates = evaluate_predicates(text, [r for r in resolutions if r.status != "hallucinated"], self.ix, self.graph, self.db, claim.target_file, claim.target_line_start, claim.target_line_end)
            func = func or _pick_function(resolutions, self.ix, claim.target_file, claim.target_line_start, claim.target_line_end, text, "")
            func_q = func.qualified_name if func else None

            for r in resolutions:
                if r.status == "resolved_elsewhere" and any(m.kind in ("function", "method", "class") for m in r.matches):
                    counts["contradicted"] += 1
                    explanations.append(f"✗ Misattributed symbol: `{r.token}` {r.note}.")
                    modes.add("semantic_hallucination")
            if location.drift_lines and location.drift_lines > 6:
                counts["contradicted"] += 1
                explanations.append(f"✗ Location drift: {location.note}.")
                modes.add("semantic_hallucination")

            any_ast = False
            for p in predicates:
                if p.kind == "impact":
                    if p.outcome == "corroborated":
                        kinds.append("call-graph")
                        explanations.append(f"✓ Impact verified — {p.evidence}.")
                    else:
                        counts["unsupported"] += 1
                        explanations.append(f"△ Unsupported Impact Claim — {p.evidence}.")
                        modes.add("unsupported_claim")
                    continue
                if p.outcome == "corroborated":
                    any_ast = True
                    explanations.append(f"✓ AST confirms “{p.phrase}” — {p.evidence}.")
                elif p.outcome == "refuted":
                    counts["contradicted"] += 1
                    explanations.append(f"✗ Claim says “{p.phrase}” but {p.evidence}.")
                    modes.add("semantic_hallucination")
                else:
                    explanations.append(f"• {p.label}: undecidable statically ({p.evidence}).")
            if any_ast:
                kinds.append("ast-predicate")

            # -------- static corroboration (±window, scoped)
            statics = self._static_evidence(claim, predicates, func_q)
            tools = sorted({f.tool for f in statics})
            for t in tools:
                hits = [f for f in statics if f.tool == t]
                kinds.append(f"sast:{t}")
                explanations.append(f"✓ {t} corroborates: {hits[0].rule_id} {hits[0].cwe or ''} at line {hits[0].line} — {hits[0].message}.")

            # -------- requirements
            defect_claim = any(p.asserted is False for p in predicates if p.kind != "impact") or claim.claimed_defect_category in ("Security", "Bug")
            links, req_asserted, req_phrase = self._requirement_links(claim, func_q, defect_claim)
            for link in links:
                if link.status == "MISSING":
                    counts["hallucinated"] += 1
                    explanations.append(f"✗ Cites requirement {link.req_id}, which does not exist in the specification.")
                    modes.add("hallucinated_reference")
                    continue
                actual = {"PASS": True, "FAIL": False}.get(link.status)
                if req_asserted is not None:
                    if actual is None:
                        explanations.append(f"• {link.req_id} has no executable acceptance clause; compliance cannot be decided.")
                    elif actual == req_asserted:
                        kinds.append("requirement")
                        explanations.append(f"✓ {link.req_id} executable checks confirm the claim ({link.status}).")
                    else:
                        counts["contradicted"] += 1
                        explanations.append(f"✗ Requirement drift: claim says “{req_phrase}” {link.req_id}, but its executable checks {'PASS' if actual else 'FAIL'}.")
                        modes.add("requirement_mismatch")
                elif (
                    actual is False
                    and defect_claim
                    and (link.explicit or link.similarity >= settings.requirement_link_threshold)
                    and not any(p.outcome == "refuted" for p in predicates)
                    and (any_ast or link.explicit or _topic_words(text) & _topic_words(f"{link.title} {self.db.requirement(link.req_id).text}"))
                    and self._fails_in_scope(link.req_id, func_q)
                ):
                    kinds.append("requirement")
                    explanations.append(f"✓ {link.req_id} ({link.title}) is violated by the current code, consistent with the claim.")
                    modes.add("requirement_mismatch")

            # -------- topical behavioural evidence for claims no predicate could decide
            decided = any(p.outcome in ("corroborated", "refuted") for p in predicates)
            if func_q and not decided and defect_claim:
                self._topical_evidence(text, func_q, links, counts, kinds, explanations, modes)

            # -------- correctness / proof obligations
            corr_asserted, corr_phrase = detect_polarity(text, CORRECTNESS_POS, CORRECTNESS_NEG)
            if corr_asserted is not None:
                actual, certs, detail = self._proofs_for(func_q)
                if actual is None:
                    counts["unsupported"] += 1
                    explanations.append(f"△ Correctness asserted (“{corr_phrase}”) without proof: {detail}.")
                    modes.add("unproven_correctness")
                elif actual == corr_asserted:
                    kinds.append("proof")
                    explanations.append(f"✓ Correctness evidence: {detail}.")
                    if corr_asserted and UNIVERSAL.search(text):
                        counts["unsupported"] += 1
                        explanations.append("△ Scope gap: the claim quantifies over all inputs, but the certificate is bounded to a finite domain.")
                        modes.add("unproven_correctness")
                else:
                    counts["contradicted"] += 1
                    explanations.append(f"✗ Correctness claim refuted — {detail}.")
                    modes.add("unproven_correctness")

        # -------- classification
        counts["supported"] = float(len(set(kinds)))
        if counts["hallucinated"] > 0:
            state = "HALLUCINATED"
        elif counts["contradicted"] > 0:
            state = "CONTRADICTED"
        elif counts["supported"] > 0:
            state = "SUPPORTED"
        else:
            state = "UNSUPPORTED"
            if counts["unsupported"] == 0:
                counts["unsupported"] = 1.0
                explanations.append("△ No deterministic evidence (AST fact, static finding, test, requirement or proof) corroborates this claim.")
                modes.add("unsupported_claim")
        if state == "SUPPORTED" and counts["unsupported"] == 0 and counts["supported"] == 1:
            explanations.append("• Single evidence source: corroborated but below the TRUSTED threshold; senior review advised.")

        if counts["hallucinated"] > 0 and not fatal:
            fatal.append("claim cites code that does not exist (zero-tolerance AST mapping)")
        gates = [("Fatal Hallucination", bool(fatal), "; ".join(fatal))]
        score = compute(counts, gates)
        return ClaimVerdict(
            claim=claim, state=state, symbols=resolutions, location=location, predicates=predicates,  # type: ignore[arg-type]
            static_evidence=statics, requirement_links=links, proofs=certs, evidence_counts=counts,
            evidence_kinds=sorted(set(kinds)), score=score, explanations=explanations, failure_modes=sorted(modes),
            counterfactual_probe=counterfactual(text),
        )

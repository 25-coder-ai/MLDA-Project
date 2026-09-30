# TRUST-CODE — Invention Disclosure (draft)

> Technical disclosure to support a patentability assessment. It is **not** legal advice and not a
> freedom-to-operate opinion; a prior-art search and review by a registered patent practitioner
> are required before filing.

## Problem

AI assistants now review code and write fixes. Their outputs contain hallucinated references,
unsupported and semantically false claims, fixes that silently regress behaviour, and "correctness"
assertions with no proof. The prevailing mitigation, *LLM-as-a-judge*, uses a second model with
the same failure modes, so its errors are correlated with the generator's.

## What is commonly done (for differentiation)

Static analysers (Semgrep, Bandit, SonarQube), test runners, mutation-testing frameworks
(mutmut, PIT), property-based testing (Hypothesis), bounded model checkers (CBMC), and LLM-judge
evaluation harnesses each exist separately. None of them takes **natural-language claims made by
an AI about code** as input and adjudicates each claim against sealed, heterogeneous evidence,
nor do they audit an LLM judge's error correlation with the generator.

## Novel elements (candidate claims)

1. **Claim-blind sealed evidence ledger.** A method in which all ground-truth artifacts (symbol
   table, static findings, call graph, baseline tests, requirement results) are appended to a
   hash chain and *sealed before* AI-generated claims are parsed; claim verdicts are appended after
   the seal, providing third-party-verifiable proof that evidence was not selected in response to
   the claims. (`src/evidence/ledger.py`, browser re-verification in `src/web/app.js`.)

2. **Polarity-aware compilation of review prose into typed code predicates.** Converting each AI
   review sentence into `P(subject) = asserted` with detected polarity (including negated positive
   phrases), subject binding by textual proximity to resolved AST symbols, and deciding `P` with
   AST facts (definite-assignment analysis, exception-handler presence, SQL-sink provenance, call-
   cycle detection, return-kind inference…) to classify claims as corroborated / refuted / unknown.
   (`src/verification/semantic_checker.py`.)

3. **Zero-tolerance multi-granular grounding.** Resolving every file, line range, symbol,
   *parameter of the subject function*, requirement ID, and standard-library attribute (by runtime
   introspection) cited by an AI, and applying the same resolution *post-patch* to detect helpers,
   APIs and keyword arguments invented by an AI fix. (`symbol_resolver.py`, `name_resolution.py`.)

4. **Executable requirement oracles bound to claims and patches.** Requirement documents carrying
   `check` / `forbid` / `prove` clauses evaluated in a sandbox, used both to adjudicate compliance
   claims ("satisfies REQ-7") and to classify patch outcomes as fixed / still-failing / regression.
   (`spec_indexer.py`, `requirement_verifier.py`.)

5. **Counterfactual polarity probing and error-correlation scoring of LLM judges.** Automatically
   generating the negation of each claim, detecting judges that confirm both a claim and its
   negation, and computing the φ-coefficient between generator-error and judge-error indicators
   against deterministic ground truth to produce an independence index. (`claim_extractor.counterfactual`,
   `verifier_audit.py`.)

6. **Diff-scoped mutation witness.** Restricting mutation operators to AST nodes on lines added by
   an AI patch and executing only the tests whose traced lines cover them, to decide whether passing
   tests constitute evidence for the fix. (`mutation.py`, per-test tracing in `sandbox/harness.py`.)

7. **Differential behavioural probing with intent classification.** Synthesising inputs from a
   changed function's own annotations, defaults, parameter-name semantics, body literals and
   dictionary keys (including keys used by callees), executing pre/post versions, and separating
   deliberate validation (exceptions explicitly raised by the new code) from novel crashes, hangs
   and silent value changes. (`behavior_probe.py`.)

8. **Scope-honest proof certificates for correctness claims.** Turning "this code is correct" into a
   proof obligation discharged exhaustively over a finite domain, emitting a hashed certificate
   whose scope is explicit, refusing obligations on hallucinated functions, and capping universal
   claims supported only by bounded proofs below the auto-merge threshold. (`prover.py`,
   `claim_verifier.py`.)

9. **Evidence-kind counting inside the SOP trust score.** Counting *distinct* corroborating evidence
   modalities (not raw findings) as `C_supported`, combined with zero-tolerance hallucination gates,
   so that a single analyser cannot push a claim to TRUSTED alone. (`score_engine.py`.)

## Reduction to practice

Implemented and tested in this repository: 11 reproducible scenarios on `examples/shopcart`
(one per failure mode plus a full audit) pass end-to-end in `tests/test_scenarios.py`.

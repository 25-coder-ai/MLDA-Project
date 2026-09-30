"""Command-line interface for CI pipelines.

    python -m src.cli scenario full-audit --md report.md
    python -m src.cli verify --repo examples/shopcart --review review.txt --patch fix.diff --target shop/db.py
    python -m src.cli scenarios

Exit code is 1 when any output is REJECTED (so CI can block the merge).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.models.ingestion import AIEvaluationPayload, AIPatchPayload, RequirementDoc, WorkspaceSpec
from src.pipeline import TrustPipeline
from src.reporting import to_markdown
from src.scenarios import SCENARIOS, scenario_payload


def _emit(e: dict) -> None:
    if e["status"] == "done":
        print(f"  ✓ {e['stage']:<9} {e['message']}", file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="trustcode", description="TRUST-CODE LLM Trust Engine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scenarios", help="list built-in scenarios")
    sc = sub.add_parser("scenario", help="run a built-in scenario")
    sc.add_argument("scenario_id")
    v = sub.add_parser("verify", help="verify an AI review and/or patch against a repository")
    v.add_argument("--repo", required=True)
    v.add_argument("--review", help="file with AI review text")
    v.add_argument("--patch", action="append", default=[], help="unified diff file (repeatable)")
    v.add_argument("--target", help="target file for patches")
    v.add_argument("--spec", action="append", default=[], help="requirements markdown (repeatable)")
    for p in (sc, v):
        p.add_argument("--json", help="write the full JSON report here")
        p.add_argument("--md", help="write a Markdown report here")
    args = ap.parse_args(argv)

    if args.cmd == "scenarios":
        for s in SCENARIOS:
            print(f"{s['id']:<26} {s['title']}")
        return 0
    if args.cmd == "scenario":
        payload = AIEvaluationPayload(**scenario_payload(args.scenario_id))
    else:
        patches = [AIPatchPayload(target_file=args.target or "", suggested_diff=Path(p).read_text(encoding="utf-8"), intended_fix_description=Path(p).name) for p in args.patch]
        payload = AIEvaluationPayload(
            workspace=WorkspaceSpec(repo_path=args.repo),
            requirements=[RequirementDoc(name=Path(s).name, content=Path(s).read_text(encoding="utf-8")) for s in args.spec],
            review_text=Path(args.review).read_text(encoding="utf-8") if args.review else "",
            patches=patches,
        )
    report = TrustPipeline(on_event=_emit).run(payload)
    if args.json:
        Path(args.json).write_text(json.dumps(report.model_dump(mode="json"), indent=2), encoding="utf-8")
    md = to_markdown(report)
    if args.md:
        Path(args.md).write_text(md, encoding="utf-8")
    print(report.summary.headline)
    for c in report.claims:
        print(f"  {c.score.verdict:<9} {c.score.final:5.1f}  {c.claim.claim_id:<6} {c.state}")
    for p in report.patches:
        print(f"  {p.score.verdict:<9} {p.score.final:5.1f}  {p.patch.patch_id}")
    return 1 if report.summary.rejected else 0


if __name__ == "__main__":
    sys.exit(main())

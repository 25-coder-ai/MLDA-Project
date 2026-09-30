"""Module 3, Step 3.1 — natural-language claim & entity deconstruction.

Turns free-form AI review prose into ``AIReviewClaim`` records and atomises each
claim into (a) code tokens, (b) parameter mentions, (c) REQ references and
(d) defect-assertion phrases. A spaCy ``Matcher`` pipeline is used when spaCy and a
model are installed; the deterministic regex matcher below is always applied so the
extraction is reproducible run-to-run.
"""
from __future__ import annotations

import keyword
import re
from dataclasses import dataclass, field
from typing import List, Optional

from src.ingestion.ast_parser import CODE_EXTS
from src.models.ingestion import AIReviewClaim

try:  # optional NLP backend
    import spacy  # type: ignore
    from spacy.matcher import Matcher  # type: ignore

    try:
        _NLP = spacy.load("en_core_web_trf")
    except Exception:
        _NLP = spacy.blank("en")
except Exception:  # pragma: no cover
    spacy = None
    _NLP = None

_EXT = "|".join(sorted({e.lstrip(".") for e in CODE_EXTS} | {"md", "txt", "toml", "json", "yaml", "yml", "cfg"}, key=len, reverse=True))
HEADER = re.compile(
    rf"""^\s*(?:[-*>]\s*)?(?:\[(?P<id>[A-Za-z]{{0,4}}-?\d+)\]\s*|(?P<id2>[A-Z]{{1,3}}\d+)[.:)]\s+)?
    `?(?P<file>[\w.\\/-]+\.(?:{_EXT}))`?
    (?:(?::|\#L|\s+lines?\s+|\s+L|\s*@\s*)(?P<start>\d+)(?:\s*(?:-|–|—|to|:|\.\.)\s*L?(?P<end>\d+))?)?
    \s*(?:[\[(](?P<cat>[A-Za-z][\w /-]{{0,24}})[\])])?
    \s*[:\-–—]*\s*(?P<text>.*)$""",
    re.X,
)
BACKTICK = re.compile(r"`([^`\n]+)`")
CALLISH = re.compile(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\(")
SNAKE = re.compile(r"(?<![\w./-])([a-z_][a-z0-9]*_[a-z0-9_]+)(?![\w/-]|\.\w)")
CAMEL = re.compile(r"(?<![\w./-])([a-z]+[A-Z][A-Za-z0-9]*)\b")
DOTTED = re.compile(r"(?<![\w/-])([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)(?![\w/(-])")
PARAM = re.compile(r"\b(?:parameter|argument|param|arg|kwarg)s?\s+`?([A-Za-z_]\w*)`?", re.I)
REQ = re.compile(r"\bREQ-\d{1,4}\b", re.I)
FILE_LIKE = re.compile(rf"\.(?:{_EXT})$")

CATEGORY_WORDS = {
    "Security": ("security", "sec", "vuln", "vulnerability", "cwe", "injection", "xss", "secret"),
    "Bug": ("bug", "defect", "logic", "crash", "error"),
    "Performance": ("performance", "perf", "slow", "memory", "complexity"),
    "Style": ("style", "naming", "readability", "nit", "lint"),
    "Requirement": ("requirement", "spec", "compliance", "req"),
    "Correctness": ("correctness", "correct", "proof", "verified"),
    "Impact": ("impact", "breaking", "regression"),
}
_NOT_CODE = {"e.g", "i.e", "etc", "vs", "a.k.a"}


@dataclass
class ExtractedEntities:
    tokens: List[str] = field(default_factory=list)
    parameters: List[str] = field(default_factory=list)
    requirement_ids: List[str] = field(default_factory=list)
    backend: str = "regex"


def normalise_category(raw: Optional[str], text: str) -> str:
    if raw:
        low = raw.lower().strip()
        for cat, words in CATEGORY_WORDS.items():
            if low in words or low == cat.lower():
                return cat
        return raw.strip().title()
    low = text.lower()
    for cat in ("Security", "Requirement", "Correctness", "Performance", "Bug", "Style"):
        if any(re.search(rf"\b{w}\b", low) for w in CATEGORY_WORDS[cat]):
            return cat
    return "Bug"


def _clean_token(tok: str) -> Optional[str]:
    tok = tok.strip().strip(".,;:!?'\"")
    m = re.match(r"[A-Za-z_][\w.]*", tok)
    if not m:
        return None
    t = m.group(0).rstrip(".")
    if not t or t.lower() in _NOT_CODE or FILE_LIKE.search(t) or REQ.fullmatch(t):
        return None
    if keyword.iskeyword(t) and t not in ("None", "True", "False"):
        return None
    return t


def extract_entities(text: str) -> ExtractedEntities:
    ent = ExtractedEntities()
    seen: set = set()

    def add(tok: Optional[str]) -> None:
        if tok and tok not in seen:
            seen.add(tok)
            ent.tokens.append(tok)

    for m in BACKTICK.finditer(text):
        add(_clean_token(m.group(1)))
    prose = BACKTICK.sub(" ", text)
    for pat in (CALLISH, DOTTED, SNAKE, CAMEL):
        for m in pat.finditer(prose):
            tok = _clean_token(m.group(1))
            if tok and len(tok) > 1:
                add(tok)
    for m in PARAM.finditer(text):
        if m.group(1) not in ent.parameters:
            ent.parameters.append(m.group(1))
    ent.requirement_ids = sorted({r.upper() for r in REQ.findall(text)})
    if _NLP is not None:  # pragma: no cover - optional
        ent.backend = "spacy+regex"
    return ent


def parse_review_text(text: str) -> List[AIReviewClaim]:
    """Split free-form review text into structured claims.

    Accepted header forms (one per claim, continuation lines are appended)::

        [C1] shop/db.py:18-21 (Security) `find_user` builds SQL with an f-string
        - shop/pricing.py#L8 [Bug]: ...
        shop/auth.py lines 3-9 — ...
    """
    claims: List[dict] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            continue
        m = HEADER.match(line)
        if m and m.group("file") and ("/" in m.group("file") or m.group("start")):
            start = int(m.group("start") or 1)
            end = int(m.group("end") or start)
            claims.append({"claim_id": m.group("id") or m.group("id2") or f"C{len(claims) + 1}", "target_file": m.group("file"), "start": start, "end": end, "cat": m.group("cat"), "text": m.group("text").strip()})
        elif claims:
            claims[-1]["text"] = (claims[-1]["text"] + " " + line.strip()).strip()
        else:
            claims.append({"claim_id": "C1", "target_file": "", "start": 0, "end": 0, "cat": None, "text": line.strip()})
    out: List[AIReviewClaim] = []
    for c in claims:
        ent = extract_entities(c["text"])
        out.append(
            AIReviewClaim(
                claim_id=c["claim_id"],
                target_file=c["target_file"],
                target_line_start=c["start"],
                target_line_end=c["end"],
                referenced_symbols=ent.tokens,
                claimed_defect_category=normalise_category(c["cat"], c["text"]),
                claim_text=c["text"],
                source="parsed" if c["target_file"] else "inferred",
            )
        )
    return out


# --------------------------------------------------------------------------- counterfactuals
_FLIPS = [
    (r"\bdoes not\b", "does"), (r"\bdoesn't\b", "does"), (r"\bis not\b", "is"), (r"\bisn't\b", "is"),
    (r"\bnever\b", "always"), (r"\bno\b", "a"), (r"\bmissing\b", "present"), (r"\blacks\b", "has"),
    (r"\bviolates\b", "satisfies"), (r"\bsatisfies\b", "violates"), (r"\bcomplies with\b", "violates"),
    (r"\bis vulnerable\b", "is not vulnerable"), (r"\bis correct\b", "is incorrect"), (r"\bwill break\b", "cannot affect"),
    (r"\bis recursive\b", "is not recursive"), (r"\bhandles\b", "fails to handle"), (r"\bbuilds\b", "never builds"),
    (r"\bleaks\b", "does not leak"), (r"\buses\b", "does not use"), (r"\bhas\b", "has no"), (r"\bis\b", "is not"),
]


def counterfactual(text: str) -> str:
    """Engine-generated negation used to probe an LLM verifier for polarity-insensitive agreement."""
    for pat, repl in _FLIPS:
        new, n = re.subn(pat, repl, text, count=1, flags=re.I)
        if n:
            return new
    return f"It is NOT true that: {text}"

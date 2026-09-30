"""Module 1, Step 1.3 — requirement & specification ingestion.

Two products are built from PRD/SRS documents:

* **Requirement nodes** — ``REQ-###`` sections, optionally carrying *executable
  acceptance clauses* (``check:``, ``forbid:``, ``prove:`` + ``domain:``). These turn
  prose requirements into deterministic oracles for Modules 3 and 4.
* **A retrieval index** — documents are chunked (500 tokens / 50 overlap, TikToken
  when available, LangChain's splitter when installed) and vectorised with TF-IDF;
  FAISS is used as the vector store when installed, otherwise an exact NumPy
  inner-product search is used.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

from config.settings import settings
from src.models.evidence import ProofSpec, RequirementNode, SpecChunk
from src.models.ingestion import RequirementDoc

try:
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover
    _ENC = None

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
except Exception:  # pragma: no cover
    TfidfVectorizer = None

try:
    import faiss  # type: ignore
except Exception:  # pragma: no cover
    faiss = None



def _langchain_splitter():
    """LangChain's splitter is opt-in: importing it pulls in Hugging Face transformers (slow, noisy)."""
    if not settings.use_langchain_splitter:
        return None
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter  # type: ignore
    except Exception:  # pragma: no cover
        return None
    return RecursiveCharacterTextSplitter

import numpy as np

REQ_ID = re.compile(r"\bREQ-\d{1,4}\b", re.I)
_REQ_START = re.compile(r"^\s*(?:#{1,6}\s*|[-*]\s*)?(REQ-\d{1,4})\b\s*[·:\-–—.)]*\s*(.*)$", re.I)
_KV = re.compile(r"^\s*[-*]?\s*(symbols?|setup|check|forbid|prove|domain|requires|pre|allow)\s*:\s*(.+?)\s*$", re.I)
_HEADING = re.compile(r"^\s*#{1,6}\s+")


def count_tokens(text: str) -> int:
    return len(_ENC.encode(text)) if _ENC is not None else len(text.split())


# --------------------------------------------------------------------------- requirement nodes
def parse_requirements(docs: List[RequirementDoc]) -> List[RequirementNode]:
    nodes: List[RequirementNode] = []
    for doc in docs:
        current: Optional[RequirementNode] = None
        desc: List[str] = []

        def flush() -> None:
            if current is not None:
                current.text = " ".join(" ".join(desc).split())
                nodes.append(current)

        for lineno, raw in enumerate(doc.content.splitlines(), start=1):
            start = _REQ_START.match(raw)
            if start:
                flush()
                desc = []
                current = RequirementNode(req_id=start.group(1).upper(), title=start.group(2).strip() or start.group(1).upper(), text="", source_doc=doc.name, line=lineno)
                continue
            if current is None:
                continue
            if _HEADING.match(raw):
                flush()
                current, desc = None, []
                continue
            kv = _KV.match(raw)
            if not kv:
                if raw.strip():
                    desc.append(raw.strip().lstrip("-* "))
                continue
            key, value = kv.group(1).lower(), kv.group(2).strip().strip("`")
            if key.startswith("symbol"):
                current.symbols.extend(s.strip().strip("`") for s in value.split(",") if s.strip())
            elif key == "setup":
                current.setup = f"{current.setup}\n{value}" if current.setup else value
            elif key == "check":
                current.checks.append(value)
            elif key == "forbid":
                current.forbids.extend(r.strip() for r in value.split(",") if r.strip())
            elif key == "prove":
                expr, _, post = value.partition("=>")
                current.proofs.append(ProofSpec(expr=expr.strip(), post=post.strip() or None))
            elif current.proofs and key == "domain":
                current.proofs[-1].domain = value
            elif current.proofs and key in ("requires", "pre"):
                current.proofs[-1].pre = value
            elif current.proofs and key == "allow":
                current.proofs[-1].allow.extend(a.strip() for a in value.split(",") if a.strip())
        flush()
    # de-duplicate by id (first definition wins)
    seen, unique = set(), []
    for n in nodes:
        if n.req_id not in seen:
            seen.add(n.req_id)
            unique.append(n)
    return unique


# --------------------------------------------------------------------------- chunking
def chunk_documents(docs: List[RequirementDoc]) -> List[SpecChunk]:
    size, overlap = settings.spec_chunk_tokens, settings.spec_chunk_overlap
    chunks: List[SpecChunk] = []
    for doc in docs:
        pieces: List[str]
        RecursiveCharacterTextSplitter = _langchain_splitter()
        if RecursiveCharacterTextSplitter is not None and _ENC is not None:
            splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(encoding_name="cl100k_base", chunk_size=size, chunk_overlap=overlap)
            pieces = splitter.split_text(doc.content)
        elif _ENC is not None:
            toks = _ENC.encode(doc.content)
            step = max(1, size - overlap)
            pieces = [_ENC.decode(toks[i : i + size]) for i in range(0, max(1, len(toks)), step)]
        else:
            words = doc.content.split()
            step = max(1, size - overlap)
            pieces = [" ".join(words[i : i + size]) for i in range(0, max(1, len(words)), step)]
        for i, text in enumerate(p for p in pieces if p.strip()):
            chunks.append(SpecChunk(chunk_id=f"{doc.name}#{i}", doc=doc.name, text=text, tokens=count_tokens(text), req_ids=sorted({m.upper() for m in REQ_ID.findall(text)})))
    return chunks


# --------------------------------------------------------------------------- retrieval
_WORD = re.compile(r"[a-z][a-z0-9_]{1,}")
_STOP = frozenset("the a an and or of to in is are be must never should for with on by it this that as at from not no any all per".split())


def _tokens(text: str) -> List[str]:
    out = []
    for w in _WORD.findall(text.lower()):
        if w in _STOP:
            continue
        out.append(w)
        out.extend(p for p in w.split("_") if len(p) > 2 and p != w)
    return out


class RequirementIndex:
    """TF-IDF vector index over requirement nodes (FAISS-backed when available)."""

    def __init__(self, nodes: List[RequirementNode]) -> None:
        self.nodes = nodes
        self.backend = "none"
        self._docs = [f"{n.title} {n.text} {' '.join(n.symbols)}" for n in nodes]
        self._matrix: Optional[np.ndarray] = None
        self._faiss = None
        if not nodes:
            return
        if TfidfVectorizer is not None:
            self._vec = TfidfVectorizer(tokenizer=_tokens, lowercase=False, token_pattern=None, sublinear_tf=True)
            self._matrix = self._vec.fit_transform(self._docs).toarray().astype("float32")
            self.backend = "tfidf-sklearn"
        else:
            self._vocab: Dict[str, int] = {}
            df: Counter = Counter()
            toks = [_tokens(d) for d in self._docs]
            for t in toks:
                df.update(set(t))
            self._vocab = {w: i for i, w in enumerate(sorted(df))}
            self._idf = np.array([math.log((1 + len(toks)) / (1 + df[w])) + 1 for w in sorted(df)], dtype="float32")
            self._matrix = np.stack([self._embed_manual(t) for t in toks])
            self.backend = "tfidf-numpy"
        norms = np.linalg.norm(self._matrix, axis=1, keepdims=True)
        self._matrix = self._matrix / np.where(norms == 0, 1, norms)
        if faiss is not None:
            self._faiss = faiss.IndexFlatIP(self._matrix.shape[1])
            self._faiss.add(self._matrix)
            self.backend += "+faiss"

    def _embed_manual(self, toks: List[str]) -> np.ndarray:
        v = np.zeros(len(self._vocab), dtype="float32")
        for w, c in Counter(toks).items():
            if w in self._vocab:
                v[self._vocab[w]] = (1 + math.log(c)) * self._idf[self._vocab[w]]
        return v

    def _embed(self, text: str) -> np.ndarray:
        if TfidfVectorizer is not None:
            v = self._vec.transform([text]).toarray().astype("float32")[0]
        else:
            v = self._embed_manual(_tokens(text))
        n = np.linalg.norm(v)
        return v / n if n else v

    def search(self, text: str, k: int = 3) -> List[Tuple[RequirementNode, float]]:
        if self._matrix is None or not self.nodes:
            return []
        q = self._embed(text)
        if self._faiss is not None:
            scores, idx = self._faiss.search(q.reshape(1, -1), min(k, len(self.nodes)))
            pairs = [(int(i), float(s)) for i, s in zip(idx[0], scores[0]) if i >= 0]
        else:
            sims = self._matrix @ q
            order = np.argsort(-sims)[:k]
            pairs = [(int(i), float(sims[i])) for i in order]
        return [(self.nodes[i], round(s, 4)) for i, s in pairs]

    def get(self, req_id: str) -> Optional[RequirementNode]:
        for n in self.nodes:
            if n.req_id == req_id.upper():
                return n
        return None

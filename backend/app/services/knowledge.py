"""Knowledge base: chunking and keyword search.

Embeddings are not used yet (provider not chosen). Search is BM25-style keyword scoring over
active chunks, which is enough for a small knowledge base. When the embeddings provider is
chosen, add a vector column to knowledge_chunks and replace `search` with a hybrid query.
"""
import math
import re
import uuid
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KnowledgeChunk, KnowledgeItem

STOPWORDS = set(
    "a an and are as at be but by for from has have how i if in is it its me my of on or our so that the their "
    "this to was we what when where which who will with you your do does can could would should about please".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS and len(t) > 1]


def chunk_text(text: str, max_chars: int = 900, overlap: int = 100) -> list[str]:
    """Split on blank lines, pack paragraphs up to max_chars, split long paragraphs with overlap."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    pieces: list[str] = []
    for p in paragraphs:
        if len(p) <= max_chars:
            pieces.append(p)
            continue
        start = 0
        while start < len(p):
            end = min(start + max_chars, len(p))
            pieces.append(p[start:end])
            if end == len(p):
                break
            start = end - overlap
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 2 > max_chars:
            chunks.append(current)
            current = piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def create_item(
    db: Session, title: str, text: str, kind: str = "other", source: str = "pasted", user_id: uuid.UUID | None = None
) -> KnowledgeItem:
    item = KnowledgeItem(title=title, kind=kind, source=source, raw_text=text, updated_by=user_id)
    _fill_chunks(item, text)
    db.add(item)
    db.flush()
    return item


def replace_text(db: Session, item: KnowledgeItem, text: str, user_id: uuid.UUID | None = None) -> None:
    item.chunks.clear()
    db.flush()
    item.raw_text = text
    item.updated_by = user_id
    _fill_chunks(item, text)
    db.flush()


def _fill_chunks(item: KnowledgeItem, text: str) -> None:
    for i, c in enumerate(chunk_text(text)):
        item.chunks.append(KnowledgeChunk(chunk_index=i, content=c, token_count=len(tokenize(c))))


def search(db: Session, query: str, top_k: int = 5) -> list[tuple[KnowledgeChunk, float]]:
    q_terms = tokenize(query)
    if not q_terms:
        return []
    rows = db.execute(
        select(KnowledgeChunk).join(KnowledgeItem).where(KnowledgeItem.status == "active")
    ).scalars().all()
    if not rows:
        return []
    docs = [(c, Counter(tokenize(c.content))) for c in rows]
    n = len(docs)
    avg_len = sum(sum(tf.values()) for _, tf in docs) / n or 1.0
    df = Counter()
    for _, tf in docs:
        for t in set(q_terms):
            if t in tf:
                df[t] += 1
    k1, b = 1.5, 0.75
    scored: list[tuple[KnowledgeChunk, float]] = []
    for chunk, tf in docs:
        length = sum(tf.values()) or 1
        score = 0.0
        for t in set(q_terms):
            if t not in tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * (tf[t] * (k1 + 1)) / (tf[t] + k1 * (1 - b + b * length / avg_len))
        if score > 0:
            scored.append((chunk, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]

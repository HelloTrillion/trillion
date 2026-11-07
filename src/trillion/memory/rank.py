"""Which memories a question is about, by the words they share (MEM-2's keyword path).

Deliberately plain: the store is small and local, so every active memory can be
scored on each request. Search by meaning (`meaning.py`) sits beside this, not in
place of it: an exact name or number is found by its letters, not by its meaning,
and `blend` keeps it first.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence

from trillion.memory.types import Memory

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
# Words that say nothing about the subject. English only for now; anything not
# listed simply counts, which costs a little precision and no correctness.
_STOP = frozenset(
    """a about after again all also am an and any are as at be because been before being but by
    can could did do does doing done down for from had has have having he her here hers him his
    how i if in into is it its just me more most my no nor not now of off on once only or other
    our out over own same she should so some such than that the their them then there these they
    this those through to too under until up very was we were what when where which while who
    whom why will with would you your yours tell know remember recall said say please""".split()
)


def words(text: str) -> list[str]:
    out = []
    for raw in _WORD.findall(text.lower()):
        if raw in _STOP or (len(raw) < 2 and not raw.isdigit()):
            continue
        # The crudest stemming that still joins "coffee" with "coffees".
        out.append(
            raw[:-1] if len(raw) > 3 and raw.endswith("s") and not raw.endswith("ss") else raw
        )
    return out


def scores(memories: Sequence[Memory], query: str) -> dict[str, float]:
    """How much each memory shares with `query`, by id. A memory sharing no word is absent."""
    wanted = set(words(query))
    if not wanted or not memories:
        return {}
    bags = [set(words(m.text)) for m in memories]
    total = len(memories)
    weight = {w: math.log(1 + total / (1 + sum(w in bag for bag in bags))) + 0.1 for w in wanted}
    out = {}
    for m, bag in zip(memories, bags, strict=True):
        hit = wanted & bag
        if hit:
            # Shared rare words count most; a short memory beats a long one saying the same.
            out[m.id] = sum(weight[w] for w in hit) / (1 + math.log(1 + len(bag)) / 4)
    return out


def rank(memories: Sequence[Memory], query: str, limit: int) -> list[Memory]:
    """The memories that share words with `query`, best first. No shared word, no result."""
    return _best(memories, scores(memories, query), limit)


# A memory found by meaning alone counts for at most this much of the best match by
# words, so it can reorder weaker word matches but never outrank the best one.
MEANING_WEIGHT = 0.5


def blend(
    memories: Sequence[Memory],
    query: str,
    meaning: Mapping[str, float],
    *,
    floor: float,
    limit: int,
) -> list[Memory]:
    """Words and meaning together (MEM-2), best first.

    `meaning` is each memory's cosine similarity to the question. Below `floor` it
    counts for nothing, so a memory is a result only if it shares a word with the
    question or is close to it in meaning. The best match by words scores 1; meaning
    adds up to `MEANING_WEIGHT`. An exact name or number therefore still wins."""
    by_words = scores(memories, query)
    top = max(by_words.values(), default=0.0)
    combined = {}
    for m in memories:
        near = max(0.0, (meaning.get(m.id, 0.0) - floor) / (1 - floor)) if floor < 1 else 0.0
        said = by_words.get(m.id, 0.0) / top if top else 0.0
        if said or near:
            combined[m.id] = said + MEANING_WEIGHT * near
    return _best(memories, combined, limit)


def _best(memories: Sequence[Memory], scored: Mapping[str, float], limit: int) -> list[Memory]:
    found = [(scored[m.id], m.as_of, m) for m in memories if m.id in scored]
    found.sort(key=lambda s: (s[0], s[1]), reverse=True)
    # An imported claim never outranks what the user said, or anything else she holds
    # (MEM-6a): it comes after every other match, best first among themselves.
    found.sort(key=lambda s: s[2].source == "imported_claim")
    return [m for _, _, m in found[:limit]]

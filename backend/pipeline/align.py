"""Sentence alignment for bilingual style-reference uploads (source file + its translation).

When a user uploads both the original and its translation, we pair sentences so the pairs
can seed Translation Memory. Equal-length inputs align positionally (the common case — same
document structure). Otherwise we run a cosine Needleman-Wunsch over the embedded sentences,
keeping monotonic 1:1 matches above a similarity floor and dropping the rest. Alignment is
best-effort: TM reuse downstream is similarity-thresholded, so a missed/odd pair is harmless.
"""
from __future__ import annotations

from pipeline.embeddings import DOCUMENT, cosine

_GAP = -0.2            # cost of skipping a sentence on one side
_MIN_SCORE = 0.4       # a diagonal step below this isn't recorded as a real pair
_MAX_CELLS = 250_000   # DP size cap; beyond it, fall back to positional zip of the shorter side


def align_segments(src: list[str], tgt: list[str], embedder, min_score: float = _MIN_SCORE):
    """Return [(source, target)] aligned pairs. `embedder` is used only for the unequal-length
    DP path; equal-length inputs align positionally without embedding."""
    src = [s.strip() for s in src if s and s.strip()]
    tgt = [t.strip() for t in tgt if t and t.strip()]
    if not src or not tgt:
        return []
    if len(src) == len(tgt):
        return list(zip(src, tgt))
    if len(src) * len(tgt) > _MAX_CELLS:
        n = min(len(src), len(tgt))
        return list(zip(src[:n], tgt[:n]))

    S = embedder.embed(src, input_type=DOCUMENT)
    T = embedder.embed(tgt, input_type=DOCUMENT)
    n, m = len(src), len(tgt)
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] + _GAP
    for j in range(1, m + 1):
        dp[0][j] = dp[0][j - 1] + _GAP
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dp[i][j] = max(
                dp[i - 1][j - 1] + cosine(S[i - 1], T[j - 1]),   # match i,j
                dp[i - 1][j] + _GAP,                              # skip source i
                dp[i][j - 1] + _GAP,                              # skip target j
            )

    pairs: list[tuple[str, str]] = []
    i, j = n, m
    while i > 0 and j > 0:
        if dp[i][j] == dp[i - 1][j - 1] + cosine(S[i - 1], T[j - 1]):
            if cosine(S[i - 1], T[j - 1]) >= min_score:
                pairs.append((src[i - 1], tgt[j - 1]))
            i -= 1
            j -= 1
        elif dp[i][j] == dp[i - 1][j] + _GAP:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return pairs

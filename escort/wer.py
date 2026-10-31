from __future__ import annotations

import re


_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?", re.I)


def tokenize(text: str) -> list[str]:
    return [match.group(0).lower() for match in _TOKEN.finditer(text)]


def edit_counts(reference: list[str], hypothesis: list[str]) -> tuple[int, int, int]:
    rows = len(reference) + 1
    cols = len(hypothesis) + 1
    dp = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        dp[i][0] = i
    for j in range(cols):
        dp[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            if reference[i - 1] == hypothesis[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])
    i = len(reference)
    j = len(hypothesis)
    substitutions = deletions = insertions = 0
    while i > 0 or j > 0:
        if i > 0 and j > 0 and reference[i - 1] == hypothesis[j - 1] and dp[i][j] == dp[i - 1][j - 1]:
            i -= 1
            j -= 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            substitutions += 1
            i -= 1
            j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            deletions += 1
            i -= 1
        else:
            insertions += 1
            j -= 1
    return substitutions, deletions, insertions


def wer(reference: str, hypothesis: str) -> float:
    ref = tokenize(reference)
    hyp = tokenize(hypothesis)
    if not ref and not hyp:
        return 0.0
    if not ref:
        return 1.0
    substitutions, deletions, insertions = edit_counts(ref, hyp)
    return (substitutions + deletions + insertions) / len(ref)

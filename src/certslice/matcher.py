from __future__ import annotations

from rapidfuzz import fuzz

from certslice.models import Candidate, Employee
from certslice.normalize import name_tokens


def partial_cover(emp_tokens: list[str], line_tokens: list[str]) -> bool:
    m = len(emp_tokens)
    for s in range(len(line_tokens)):
        if line_tokens[s] != emp_tokens[0]:
            continue
        full, i, j = 1, s + 1, 1
        while i < len(line_tokens) and j < m:
            if line_tokens[i] == emp_tokens[j]:
                full += 1
                i += 1
                j += 1
            elif len(line_tokens[i]) == 1 and line_tokens[i] == emp_tokens[j][0]:
                i += 1
                j += 1
            elif line_tokens[i] in emp_tokens[j + 1:]:
                j += 1
            else:
                break
        if full >= 2 and full < m:
            return True
    return False


def decide(cands: list[Candidate], employees: list[Employee], cfg: dict):
    m = cfg["matching"]
    if not cands:
        return "unmatched", None, "no confident holder"
    if cands[0].score < m["accept_score"]:
        return "unmatched", None, "partial name" if cands[0].partial else "no confident holder"
    top = cands[0]
    if len(cands) > 1:
        second = cands[1]
        if employees[second.index].norm == employees[top.index].norm:
            return "unmatched", None, "homonym"
        if top.score - second.score < m["margin"]:
            return "unmatched", None, "close candidates"
    return "accepted", top, ""


def page_candidates(lines: list[str], employees: list[Employee], cfg: dict) -> list[Candidate]:
    m = cfg["matching"]
    accept_score = float(m["accept_score"])
    list_score = float(m["list_score"])
    partial_score = float(m.get("partial_score", 90))
    min_tokens_fuzzy = int(m.get("min_tokens_fuzzy", 2))
    titles = frozenset(m.get("titles", []))

    # Tokenize each line
    line_tokens: list[tuple[int, list[str]]] = []
    for line_idx, line in enumerate(lines):
        toks = name_tokens(line, titles)
        if toks:
            line_tokens.append((line_idx, toks))

    best_scores: dict[int, float] = {}
    best_lines: dict[int, int] = {}
    best_partials: dict[int, bool] = {}

    for emp_idx, emp in enumerate(employees):
        emp_norm = emp.norm
        if not emp_norm:
            continue
        emp_toks = emp_norm.split()
        k = len(emp_toks)
        if k == 0:
            continue

        emp_best_score = -1.0
        emp_best_line = -1
        emp_best_partial = False

        for line_idx, toks in line_tokens:
            line_best = 0.0
            is_partial = False
            if k < min_tokens_fuzzy:
                if emp_norm in toks:
                    line_best = 100.0
                else:
                    line_best = 0.0
            else:
                tok_len = len(toks)
                # Test windows of sizes k-1, k, k+1
                for w in (k - 1, k, k + 1):
                    if 1 <= w <= tok_len:
                        for s in range(tok_len - w + 1):
                            win = " ".join(toks[s : s + w])
                            score = float(fuzz.ratio(emp_norm, win))
                            if score > line_best:
                                line_best = score
                                if line_best == 100.0:
                                    break
                    if line_best == 100.0:
                        break

                if line_best < accept_score and k >= 2:
                    if partial_cover(emp_toks, toks):
                        line_best = partial_score
                        is_partial = True

            if line_best > emp_best_score:
                emp_best_score = line_best
                emp_best_line = line_idx
                emp_best_partial = is_partial

        if emp_best_score >= list_score:
            best_scores[emp_idx] = emp_best_score
            best_lines[emp_idx] = emp_best_line
            best_partials[emp_idx] = emp_best_partial

    raw_cands = [
        Candidate(
            index=emp_idx,
            score=best_scores[emp_idx],
            line=best_lines[emp_idx],
            partial=best_partials[emp_idx],
        )
        for emp_idx in best_scores
    ]

    # Subsumption: drop candidate X when another non-partial candidate Y on the same line
    # scores >= accept_score, Y has more tokens, and X is inside Y.
    cands: list[Candidate] = []
    for x in raw_cands:
        x_norm = employees[x.index].norm
        x_tok_count = len(x_norm.split())
        subsumed = False
        for y in raw_cands:
            if y is x or y.partial or y.score < accept_score or y.line != x.line:
                continue
            y_norm = employees[y.index].norm
            y_tok_count = len(y_norm.split())
            if y_tok_count > x_tok_count and f" {x_norm} " in f" {y_norm} ":
                subsumed = True
                break
        if not subsumed:
            cands.append(x)

    cands.sort(key=lambda c: (-c.score, c.index))
    return cands

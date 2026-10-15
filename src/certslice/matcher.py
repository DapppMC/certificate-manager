from __future__ import annotations

from rapidfuzz import fuzz

from certslice.models import Candidate, Employee
from certslice.normalize import name_tokens


def decide(cands: list[Candidate], employees: list[Employee], cfg: dict):
    m = cfg["matching"]
    if not cands or cands[0].score < m["accept_score"]:
        return "unmatched", None, "no confident holder"
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
    list_score = float(m["list_score"])
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

        for line_idx, toks in line_tokens:
            line_best = 0.0
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

            if line_best > emp_best_score:
                emp_best_score = line_best
                emp_best_line = line_idx

        if emp_best_score >= list_score:
            best_scores[emp_idx] = emp_best_score
            best_lines[emp_idx] = emp_best_line

    cands = [
        Candidate(index=emp_idx, score=best_scores[emp_idx], line=best_lines[emp_idx])
        for emp_idx in best_scores
    ]
    cands.sort(key=lambda c: (-c.score, c.index))
    return cands

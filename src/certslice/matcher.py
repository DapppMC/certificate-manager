import functools
import math
import re

import numpy as np
from rapidfuzz import fuzz, process

from certslice.models import Candidate, Employee
from certslice.normalize import fold, name_tokens


class _EmployeeIndex:
    __slots__ = ("norms", "emp_tokens", "by_k", "by_k_norms")

    def __init__(self, norms: tuple[str, ...]):
        self.norms = norms
        self.emp_tokens: list[list[str]] = []
        self.by_k: dict[int, list[int]] = {}
        self.by_k_norms: dict[int, list[str]] = {}
        for idx, norm in enumerate(norms):
            toks = norm.split() if norm else []
            self.emp_tokens.append(toks)
            k = len(toks)
            if k > 0:
                self.by_k.setdefault(k, []).append(idx)
                self.by_k_norms.setdefault(k, []).append(norm)


@functools.lru_cache(maxsize=16)
def _get_employee_index(norms: tuple[str, ...]) -> _EmployeeIndex:
    return _EmployeeIndex(norms)


@functools.lru_cache(maxsize=32)
def _compile_cue_regex(cues: tuple[str, ...]) -> re.Pattern | None:
    folded = [re.escape(fold(c)) for c in cues if fold(c)]
    if not folded:
        return None
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(folded) + r")(?![a-z0-9])")


def template_employees(per_page: list[list[Candidate]], n_pages: int, cfg: dict) -> set[int]:
    m = cfg["matching"]
    counts: dict[int, int] = {}
    for cands in per_page:
        for c in cands:
            if c.score >= m["list_score"]:
                counts[c.index] = counts.get(c.index, 0) + 1
    need = max(m["template_min_pages"], math.ceil(m["template_min_share"] * n_pages))
    return {i for i, count in counts.items() if count >= need}


def cue_filter(cands: list[Candidate], lines: list[str], cfg: dict) -> list[Candidate]:
    m = cfg["matching"]
    accept_score = float(m["accept_score"])
    cue_window_lines = int(m.get("cue_window_lines", 3))
    holder_cues = tuple(m.get("holder_cues", []))

    cue_regex = _compile_cue_regex(holder_cues)
    cue_line_indices: list[int] = []
    if cue_regex is not None:
        for idx, l in enumerate(lines):
            if cue_regex.search(fold(l)):
                cue_line_indices.append(idx)

    if not cue_line_indices:
        res = list(cands)
        res.sort(key=lambda c: (-c.score, c.index))
        return res

    def in_window(line_idx: int) -> bool:
        return any(c_idx <= line_idx <= c_idx + cue_window_lines for c_idx in cue_line_indices)

    has_confident_near_cue = any(c.score >= accept_score and in_window(c.line) for c in cands)
    if has_confident_near_cue:
        res = [c for c in cands if in_window(c.line)]
    else:
        res = list(cands)

    res.sort(key=lambda c: (-c.score, c.index))
    return res


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
    if not lines or not employees:
        return []

    m = cfg["matching"]
    accept_score = float(m["accept_score"])
    list_score = float(m["list_score"])
    partial_score = float(m.get("partial_score", 90))
    min_tokens_fuzzy = int(m.get("min_tokens_fuzzy", 2))
    titles = frozenset(m.get("titles", []))

    # Tokenize each line once
    line_tokens: list[tuple[int, list[str]]] = []
    page_token_set: set[str] = set()
    for line_idx, line in enumerate(lines):
        toks = name_tokens(line, titles)
        if toks:
            line_tokens.append((line_idx, toks))
            page_token_set.update(toks)

    if not line_tokens:
        return []

    index = _get_employee_index(tuple(e.norm for e in employees))
    cutoff = max(0.0, min(list_score, accept_score, partial_score) - 1e-6)

    best_scores: dict[int, float] = {}
    best_lines: dict[int, int] = {}
    best_partials: dict[int, bool] = {}

    for k, emp_indices in index.by_k.items():
        if k < min_tokens_fuzzy:
            for emp_idx in emp_indices:
                emp_norm = index.norms[emp_idx]
                if emp_norm not in page_token_set:
                    continue
                emp_best_score = -1.0
                emp_best_line = -1
                emp_best_partial = False
                for line_idx, toks in line_tokens:
                    if emp_norm in toks:
                        if len(toks) == 1:
                            line_best = 100.0
                            is_partial = False
                        else:
                            line_best = partial_score
                            is_partial = True
                    else:
                        line_best = 0.0
                        is_partial = False
                    if line_best > emp_best_score:
                        emp_best_score = line_best
                        emp_best_line = line_idx
                        emp_best_partial = is_partial
                if emp_best_score >= list_score:
                    best_scores[emp_idx] = emp_best_score
                    best_lines[emp_idx] = emp_best_line
                    best_partials[emp_idx] = emp_best_partial
        else:
            # Pre-build deduplicated windows of sizes (k-1, k, k+1) across all lines
            unique_wins: list[str] = []
            win_to_col: dict[str, int] = {}
            line_win_cols: list[list[int]] = []

            for _, toks in line_tokens:
                tok_len = len(toks)
                cols_for_line: list[int] = []
                seen_on_line: set[int] = set()
                for w in (k - 1, k, k + 1):
                    if 1 <= w <= tok_len:
                        for s in range(tok_len - w + 1):
                            win = " ".join(toks[s : s + w])
                            col = win_to_col.get(win)
                            if col is None:
                                col = len(unique_wins)
                                win_to_col[win] = col
                                unique_wins.append(win)
                            if col not in seen_on_line:
                                seen_on_line.add(col)
                                cols_for_line.append(col)
                line_win_cols.append(cols_for_line)

            k_norms = index.by_k_norms[k]
            if unique_wins:
                scores_matrix = process.cdist(
                    k_norms,
                    unique_wins,
                    scorer=fuzz.ratio,
                    score_cutoff=cutoff,
                    dtype=np.float64,
                )
                row_max = scores_matrix.max(axis=1)
            else:
                scores_matrix = None
                row_max = None

            for row_i, emp_idx in enumerate(emp_indices):
                emp_toks = index.emp_tokens[emp_idx]
                has_win_hit = row_max is not None and float(row_max[row_i]) > 0.0
                has_first_tok = emp_toks[0] in page_token_set
                if not has_win_hit and not has_first_tok:
                    continue

                emp_best_score = -1.0
                emp_best_line = -1
                emp_best_partial = False
                emp_row = scores_matrix[row_i] if scores_matrix is not None else None

                for l_pos, (line_idx, toks) in enumerate(line_tokens):
                    cols = line_win_cols[l_pos]
                    if has_win_hit and cols and emp_row is not None:
                        line_best = float(emp_row[cols].max())
                    else:
                        line_best = 0.0
                    is_partial = False

                    if line_best < accept_score and k >= 2 and has_first_tok:
                        if emp_toks[0] in toks and partial_cover(emp_toks, toks):
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
        for emp_idx in sorted(best_scores)
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

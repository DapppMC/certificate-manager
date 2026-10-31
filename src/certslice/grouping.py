from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from certslice.errors import AccountingError
from certslice.models import Candidate, Employee


@dataclass
class PageInfo:
    decision: tuple[str, Candidate | None, str | None]
    is_start: bool
    number: str | None
    continuation: bool
    source: str


def group_pages(
    infos: list[PageInfo],
    employees: list[Employee],
    mode: str,
    cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    if not infos:
        return []

    if mode != "auto":
        n = int(mode)
        groups: list[dict[str, Any]] = []
        for i in range(0, len(infos), n):
            chunk_pages = list(range(i + 1, min(i + n + 1, len(infos) + 1)))
            chunk_infos = [infos[p - 1] for p in chunk_pages]

            start_pages = [(p, info) for p, info in zip(chunk_pages, chunk_infos) if info.is_start]
            all_source_none = all(info.source == "none" for info in chunk_infos)

            if start_pages:
                deciding_page, deciding_info = start_pages[0]
            else:
                deciding_page, deciding_info = chunk_pages[0], chunk_infos[0]

            cand = deciding_info.decision[1]

            if (
                start_pages
                and all(info.decision[0] == "accepted" for _, info in start_pages)
                and len({employees[info.decision[1].index].nid for _, info in start_pages if info.decision[1] is not None}) == 1
            ):
                status = "accepted"
                reason = None
            else:
                status = "unmatched"
                reason = "no text" if all_source_none else "fixed chunk without one clear holder"

            groups.append({
                "pages": chunk_pages,
                "status": status,
                "candidate": cand,
                "reason": reason,
                "deciding_page": deciding_page,
            })
        return groups

    # Auto mode
    max_pages = cfg.get("grouping", {}).get("max_pages_per_cert", 3)
    groups = []

    for idx, curr in enumerate(infos):
        p = idx + 1
        if not groups:
            groups.append({"pages": [p]})
            continue

        prev = groups[-1]
        first_p = prev["pages"][0]
        first_info = infos[first_p - 1]

        if curr.is_start:
            same_nid = False
            if (
                curr.decision[0] == "accepted"
                and first_info.decision[0] == "accepted"
                and curr.decision[1] is not None
                and first_info.decision[1] is not None
            ):
                c1_emp = employees[curr.decision[1].index]
                c0_emp = employees[first_info.decision[1].index]
                same_nid = (c1_emp.nid == c0_emp.nid)

            can_append = (
                len(prev["pages"]) == 1
                and len(prev["pages"]) < max_pages
                and same_nid
                and (
                    curr.continuation
                    or (curr.number is not None and first_info.number is not None and curr.number == first_info.number)
                    or curr.number is None
                )
            )
            if can_append:
                prev["pages"].append(p)
            else:
                groups.append({"pages": [p]})
        else:
            can_append = (
                len(prev["pages"]) < max_pages
                and (
                    curr.continuation
                    or (curr.number is not None and first_info.number is not None and curr.number == first_info.number)
                )
            )
            if can_append:
                prev["pages"].append(p)
            else:
                groups.append({"pages": [p]})

    for g in groups:
        first_p = g["pages"][0]
        first_info = infos[first_p - 1]
        status, cand, reason = first_info.decision
        if status == "unmatched" and first_info.source == "none":
            reason = "no text"
        g["status"] = status
        g["candidate"] = cand
        g["reason"] = reason
        g["deciding_page"] = first_p

    return groups


def check_accounting(groups: list[dict[str, Any]], n_pages: int) -> None:
    seen = []
    for g in groups:
        seen.extend(g["pages"])
    if sorted(seen) != list(range(1, n_pages + 1)):
        raise AccountingError(f"Groups cover {sorted(seen)}, expected 1..{n_pages}")

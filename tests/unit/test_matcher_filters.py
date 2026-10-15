from certslice.config import load_config
from certslice.matcher import cue_filter, template_employees
from certslice.models import Candidate


def test_template_employees():
    cfg = load_config(None)  # template_min_pages = 3, template_min_share = 0.5
    # 5 pages total: need = max(3, ceil(0.5 * 5)) = 3. A name on 4 of 5 pages is template:
    per_page_5 = [
        [Candidate(index=1, score=95.0, line=1)],
        [Candidate(index=1, score=95.0, line=1)],
        [Candidate(index=1, score=95.0, line=1)],
        [Candidate(index=1, score=95.0, line=1)],
        [],
    ]
    assert template_employees(per_page_5, 5, cfg) == {1}

    # 10 pages total: need = max(3, ceil(0.5 * 10)) = 5. A name on 2 of 10 pages is NOT template:
    per_page_10 = [
        [Candidate(index=2, score=95.0, line=1)],
        [Candidate(index=2, score=95.0, line=1)],
    ] + [[] for _ in range(8)]
    assert template_employees(per_page_10, 10, cfg) == set()


def test_cue_filter():
    cfg = load_config(None)  # cue_window_lines = 3, holder_cues has "diberikan kepada"
    lines = [
        "SERTIFIKAT",
        "Diberikan kepada:",  # index 1
        "Budi Santoso",       # index 2 (1 line after cue)
        "Line 3",
        "Line 4",
        "Line 5",
        "Line 6",
        "Line 7",
        "Line 8",
        "Line 9",             # index 9 (8 lines after cue)
    ]

    cands_with_near = [
        Candidate(index=0, score=95.0, line=2),
        Candidate(index=1, score=94.0, line=9),
    ]
    # With a confident candidate near cue (line 2), candidate at line 9 is dropped:
    filtered = cue_filter(cands_with_near, lines, cfg)
    assert len(filtered) == 1
    assert filtered[0].index == 0

    # Without any confident candidate near a cue, nothing is dropped:
    cands_far_only = [
        Candidate(index=1, score=94.0, line=9),
    ]
    filtered_far = cue_filter(cands_far_only, lines, cfg)
    assert len(filtered_far) == 1
    assert filtered_far[0].index == 1

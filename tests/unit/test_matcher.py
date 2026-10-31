from certslice.config import load_config
from certslice.matcher import decide, page_candidates
from certslice.models import Candidate, Employee


def test_page_candidates_and_decide():
    cfg = load_config(None)
    employees = [
        Employee(nid="001", nama="Budi Santoso", norm="budi santoso"),
        Employee(nid="002", nama="Budi Santosa", norm="budi santosa"),
        Employee(nid="003", nama="Ahmad Fauzi", norm="ahmad fauzi"),
        Employee(nid="004", nama="Ahmad Fauzi", norm="ahmad fauzi"),
        Employee(nid="005", nama="Dewi Lestari", norm="dewi lestari"),
    ]

    # 1. Exact name with titles scores 100
    lines_exact = ["SERTIFIKAT", "Diberikan kepada Ir. Budi Santoso, S.T."]
    cands_exact = page_candidates(lines_exact, employees, cfg)
    assert len(cands_exact) >= 1
    assert cands_exact[0].index == 0
    assert cands_exact[0].score == 100.0

    # 2. One-letter typo scores at least 92
    lines_typo = ["Diberikan kepada Siti Rahmawato"]
    emp_siti = [Employee(nid="010", nama="Siti Rahmawati", norm="siti rahmawati")]
    cands_typo = page_candidates(lines_typo, emp_siti, cfg)
    assert len(cands_typo) >= 1
    assert cands_typo[0].index == 0
    assert cands_typo[0].score >= 92.0

    # 3. Two rows with the same normalised name give homonym
    cands_homonym = [
        Candidate(index=2, score=100.0, line=1),
        Candidate(index=3, score=100.0, line=1),
    ]
    status, top, reason = decide(cands_homonym, employees, cfg)
    assert status == "unmatched"
    assert reason == "homonym"
    assert top is None

    # 4. Two different names within 5 points give close candidates
    cands_close = [
        Candidate(index=0, score=95.0, line=1),
        Candidate(index=1, score=93.0, line=1),
    ]
    status, top, reason = decide(cands_close, employees, cfg)
    assert status == "unmatched"
    assert reason == "close candidates"
    assert top is None

    # 5. A page without any name gives no confident holder
    lines_empty = ["SERTIFIKAT TANPA NAMA", "Jakarta 2025"]
    cands_empty = page_candidates(lines_empty, employees, cfg)
    status, top, reason = decide(cands_empty, employees, cfg)
    assert status == "unmatched"
    assert reason == "no confident holder"
    assert top is None

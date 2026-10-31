from certslice.config import load_config
from certslice.matcher import decide, page_candidates, partial_cover
from certslice.models import Employee


def test_partial_cover():
    emp1 = ["hana", "pertiwi", "kusumawardani"]
    emp2 = ["sri", "ayu", "dewi", "kartika"]
    assert partial_cover(emp1, ["hana", "pertiwi"]) is True
    assert partial_cover(emp2, ["sri", "ayu", "dewi", "k"]) is True

    # False for ["budi"] vs budi santoso wijaya (full < 2)
    assert partial_cover(["budi", "santoso", "wijaya"], ["budi"]) is False

    # False for a line starting with a different first name
    assert partial_cover(emp1, ["budi", "pertiwi"]) is False

    # False for a two-word employee (full >= 2 and full < 2 is impossible)
    assert partial_cover(["budi", "santoso"], ["budi", "santoso"]) is False


def test_subsumption_and_partial_name():
    cfg = load_config(None)
    emp_sutrisna = Employee(nid="1", nama="Sutrisna", norm="sutrisna")
    emp_ilham = Employee(nid="2", nama="Ilham Sutrisna", norm="ilham sutrisna")

    # Page line "ilham sutrisna" keeps only the second employee
    cands = page_candidates(["Ilham Sutrisna"], [emp_sutrisna, emp_ilham], cfg)
    assert len(cands) == 1
    assert cands[0].index == 1

    # Page line "hana pertiwi" with employee "Hana Pertiwi Kusumawardani" gives partial candidate score 90
    emp_hana = Employee(nid="3", nama="Hana Pertiwi Kusumawardani", norm="hana pertiwi kusumawardani")
    cands_hana = page_candidates(["Hana Pertiwi"], [emp_hana], cfg)
    assert len(cands_hana) == 1
    assert cands_hana[0].score == 90.0
    assert cands_hana[0].partial is True

    status, top, reason = decide(cands_hana, [emp_hana], cfg)
    assert status == "unmatched"
    assert reason == "partial name"

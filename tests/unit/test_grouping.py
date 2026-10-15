import pytest
from certslice.errors import AccountingError
from certslice.grouping import PageInfo, check_accounting, group_pages
from certslice.models import Candidate, Employee


@pytest.fixture
def dummy_employees():
    return [
        Employee(nid="001", nama="Budi", unit="Unit A", email="budi@example.com"),
        Employee(nid="002", nama="Siti", unit="Unit B", email="siti@example.com"),
    ]


@pytest.fixture
def base_cfg():
    return {
        "matching": {"accept_score": 92},
        "grouping": {
            "max_pages_per_cert": 3,
            "continuation_patterns": [r"\bhalaman\s*2\b", r"\b2\s*/\s*2\b"],
        },
    }


def test_holderless_continuation_joins_prev(dummy_employees, base_cfg):
    infos = [
        PageInfo(
            decision=("accepted", Candidate(0, 100.0, 0), None),
            is_start=True,
            number="123",
            continuation=False,
            source="text",
        ),
        PageInfo(
            decision=("unmatched", None, "no confident holder"),
            is_start=False,
            number=None,
            continuation=True,
            source="text",
        ),
    ]
    groups = group_pages(infos, dummy_employees, "auto", base_cfg)
    assert len(groups) == 1
    assert groups[0]["pages"] == [1, 2]
    assert groups[0]["status"] == "accepted"


def test_same_holder_new_number_starts_new_group(dummy_employees, base_cfg):
    infos = [
        PageInfo(
            decision=("accepted", Candidate(0, 100.0, 0), None),
            is_start=True,
            number="123",
            continuation=False,
            source="text",
        ),
        PageInfo(
            decision=("accepted", Candidate(0, 100.0, 0), None),
            is_start=True,
            number="456",
            continuation=False,
            source="text",
        ),
    ]
    groups = group_pages(infos, dummy_employees, "auto", base_cfg)
    assert len(groups) == 2
    assert groups[0]["pages"] == [1]
    assert groups[1]["pages"] == [2]


def test_holderless_without_signs_is_unmatched_group(dummy_employees, base_cfg):
    infos = [
        PageInfo(
            decision=("accepted", Candidate(0, 100.0, 0), None),
            is_start=True,
            number="123",
            continuation=False,
            source="text",
        ),
        PageInfo(
            decision=("unmatched", None, "no confident holder"),
            is_start=False,
            number=None,
            continuation=False,
            source="text",
        ),
    ]
    groups = group_pages(infos, dummy_employees, "auto", base_cfg)
    assert len(groups) == 2
    assert groups[0]["pages"] == [1]
    assert groups[1]["pages"] == [2]
    assert groups[1]["status"] == "unmatched"


def test_max_pages_per_cert_limit(dummy_employees, base_cfg):
    base_cfg["grouping"]["max_pages_per_cert"] = 2
    infos = [
        PageInfo(
            decision=("accepted", Candidate(0, 100.0, 0), None),
            is_start=True,
            number="123",
            continuation=False,
            source="text",
        ),
        PageInfo(
            decision=("unmatched", None, "no confident holder"),
            is_start=False,
            number=None,
            continuation=True,
            source="text",
        ),
        PageInfo(
            decision=("unmatched", None, "no confident holder"),
            is_start=False,
            number=None,
            continuation=True,
            source="text",
        ),
    ]
    groups = group_pages(infos, dummy_employees, "auto", base_cfg)
    assert len(groups) == 2
    assert groups[0]["pages"] == [1, 2]
    assert groups[1]["pages"] == [3]


def test_fixed_mode_two_different_nids(dummy_employees, base_cfg):
    infos = [
        PageInfo(
            decision=("accepted", Candidate(0, 100.0, 0), None),
            is_start=True,
            number="123",
            continuation=False,
            source="text",
        ),
        PageInfo(
            decision=("accepted", Candidate(1, 100.0, 0), None),
            is_start=True,
            number="456",
            continuation=False,
            source="text",
        ),
    ]
    groups = group_pages(infos, dummy_employees, "2", base_cfg)
    assert len(groups) == 1
    assert groups[0]["pages"] == [1, 2]
    assert groups[0]["status"] == "unmatched"
    assert groups[0]["reason"] == "fixed chunk without one clear holder"


def test_check_accounting():
    # Valid
    check_accounting([{"pages": [1, 2]}, {"pages": [3]}], 3)

    # Missing page 2
    with pytest.raises(AccountingError):
        check_accounting([{"pages": [1]}, {"pages": [3]}], 3)

    # Doubled page 2
    with pytest.raises(AccountingError):
        check_accounting([{"pages": [1, 2]}, {"pages": [2, 3]}], 3)

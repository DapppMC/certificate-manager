import datetime as dt
import pytest
from certslice.config import load_config
from certslice.expiry import add_years, apply_rules
from certslice.normalize import fold


@pytest.fixture
def cfg():
    return load_config(None)


def test_add_years_leap_year():
    # 2024-02-29 plus 5 years is 2029-02-28
    d = dt.date(2024, 2, 29)
    res = add_years(d, 5)
    assert res == dt.date(2029, 2, 28)


def test_printed_expiry_kept(cfg):
    issue = dt.date(2025, 8, 17)
    exp = dt.date(2028, 8, 16)
    res_exp, conf, src = apply_rules(issue, exp, "high", fold("Direktorat Jenderal Ketenagalistrikan"), cfg)
    assert res_exp == exp
    assert conf == "high"
    assert src == "printed"


def test_djk_text_adds_5_years(cfg):
    issue = dt.date(2025, 8, 17)
    res_exp, conf, src = apply_rules(issue, None, "low", fold("Direktorat Jenderal Ketenagalistrikan"), cfg)
    assert res_exp == dt.date(2030, 8, 17)
    assert conf == "medium"
    assert src == "rule:5y"


def test_k3_whole_word_matching(cfg):
    issue = dt.date(2025, 1, 1)
    # k3 matches
    exp1, conf1, src1 = apply_rules(issue, None, "low", fold("Pelatihan sertifikasi K3 umum"), cfg)
    assert exp1 == dt.date(2028, 1, 1)
    assert src1 == "rule:3y"

    # k30 does not match k3
    exp2, conf2, src2 = apply_rules(issue, None, "low", fold("Pelatihan K30 tidak berlaku"), cfg)
    assert exp2 is None
    assert src2 is None


def test_corporate_university_gives_none_medium(cfg):
    issue = dt.date(2025, 3, 1)
    exp, conf, src = apply_rules(issue, None, "low", fold("PLN Corporate University"), cfg)
    assert exp is None
    assert conf == "medium"
    assert src == "rule:none"


def test_no_rule_gives_none_low_null(cfg):
    issue = dt.date(2025, 3, 1)
    exp, conf, src = apply_rules(issue, None, "low", fold("Sertifikat Seminar Umum"), cfg)
    assert exp is None
    assert conf == "low"
    assert src is None


def test_duration_wins_over_rules(cfg):
    issue = dt.date(2026, 8, 20)
    text = fold("sertifikat ini berlaku untuk 3 (tiga) tahun badan nasional sertifikasi profesi bnsp")
    exp, conf, src = apply_rules(issue, None, "low", text, cfg)
    assert exp == dt.date(2029, 8, 20)
    assert conf == "high"
    assert src == "duration:3y"

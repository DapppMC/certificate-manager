"""T-005 replaceable page reader (R-17): the pipeline accepts any object with read(page) -> PageText."""
from conftest import DB, FIX, load_plan, manifest
from certslice.models import PageText
from certslice.pipeline import run


HOLDERS = {1: "BUDI SANTOSO", 2: "SITI RAHMAWATI", 3: "DEWI LESTARI"}


class FakeReader:
    def read(self, page):
        n = page.number + 1
        text = (f"SERTIFIKAT KOMPETENSI\nDiberikan kepada:\n{HOLDERS[n]}\n"
                f"Nomor: {n}00/FAKE/2025\nJakarta, 1 Januari 2025\n")
        return PageText(index=n, text=text, source="fake")


def test_fake_reader_drives_the_whole_pipeline(tmp_path):
    res = run(FIX / "bundle_simple.pdf", DB, tmp_path / "o", reader=FakeReader())
    assert res.exit_code == 0
    plan = load_plan(res.run_dir)
    assert [p["source"] for p in plan["pages"]] == ["fake", "fake", "fake"]
    assert [g["nid"] for g in plan["groups"]] == ["007123", "0045678", "8812345"]
    if (res.run_dir / "manifest_slice.xlsx").exists():  # written from T-014 on
        assert [r["NID"] for r in manifest(res.run_dir)] == ["007123", "0045678", "8812345"]

from __future__ import annotations

from pathlib import Path
import unicodedata

from certslice.errors import InputError

RUN_PART = 28  # len("/run-YYYYMMDD-HHMMSS-NN/pdf/")
DUP_RESERVE = 6  # len("_dup99")


def clean_field(s: str) -> str:
    res = []
    for c in s:
        if c in '<>:"/\\|?*_' or ord(c) < 32 or unicodedata.category(c).startswith("C"):
            res.append("-")
        else:
            res.append(c)
    cleaned = "".join(res)
    collapsed = " ".join(cleaned.split())
    return collapsed.strip(" .")


class NameBuilder:
    def __init__(self, out_dir: Path, cfg: dict):
        self.out_dir = Path(out_dir).resolve()
        self.cfg = cfg
        naming_cfg = cfg.get("naming", {})
        self.pattern = naming_cfg.get("pattern", "{nid}_{nama}_{judul}_{tahun}")
        self.renewal_suffix = naming_cfg.get("renewal_suffix", "_PERPANJANGAN_{ke}")
        self.duplicate_suffix = naming_cfg.get("duplicate_suffix", "_dup{n}")
        self.max_path_chars = naming_cfg.get("max_path_chars", 250)
        self.min_title_chars = naming_cfg.get("min_title_chars", 10)

        out_len = len(str(self.out_dir))
        self.room = self.max_path_chars - out_len - RUN_PART - DUP_RESERVE
        self.seen_names: dict[str, int] = {}
        self.title_cache: dict[tuple[str, str, str, str, int | None], str] = {}

    def build(self, nid: str, nama: str, judul: str, tahun: str, renewal: int | None) -> str:
        clean_nid = clean_field(nid)
        clean_nama = clean_field(nama)
        clean_judul = clean_field(judul)
        clean_tahun = clean_field(tahun)

        ren_suffix = self.renewal_suffix.format(ke=renewal) if renewal is not None else ""

        cache_key = (clean_nid, clean_nama, clean_judul, clean_tahun, renewal)
        if cache_key in self.title_cache:
            shortened_judul = self.title_cache[cache_key]
        else:
            shortened_judul = clean_judul

            def make_name(j: str) -> str:
                core = self.pattern.format(
                    nid=clean_nid,
                    nama=clean_nama,
                    judul=j,
                    tahun=clean_tahun,
                )
                return f"{core}{ren_suffix}.pdf"

            candidate_name = make_name(shortened_judul)
            while len(candidate_name) > self.room and len(shortened_judul) > self.min_title_chars:
                shortened_judul = shortened_judul[:-1].strip(" .")
                candidate_name = make_name(shortened_judul)

            if len(candidate_name) > self.room:
                raise InputError(
                    f"output folder path is too long ({len(str(self.out_dir))} chars), "
                    f"cannot fit file names within {self.max_path_chars} chars"
                )

            self.title_cache[cache_key] = shortened_judul

        core_name = self.pattern.format(
            nid=clean_nid,
            nama=clean_nama,
            judul=shortened_judul,
            tahun=clean_tahun,
        )
        base_name = f"{core_name}{ren_suffix}"
        base_key = base_name.lower()

        count = self.seen_names.get(base_key, 0) + 1
        self.seen_names[base_key] = count

        if count == 1:
            return f"{base_name}.pdf"
        else:
            dup_suffix = self.duplicate_suffix.format(n=count)
            return f"{base_name}{dup_suffix}.pdf"

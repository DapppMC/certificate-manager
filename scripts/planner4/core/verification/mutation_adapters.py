"""mutation_adapters.py - turn the output of external mutation tools into the same verdict as mutation_gate.

mutation_gate.py is the primary engine for Python (own AST mutants, runs everywhere Python runs, scoped to the diff).
These adapters exist for two cases where an external tool is the better or only choice:

  * StrykerJS (JavaScript / TypeScript tickets, AG3 UI + Node backends): `stryker_command` builds a diff-scoped
    command (`--mutate file:startLine-endLine`), `parse_stryker` reads the mutation-testing-elements JSON report and
    keeps only the mutants that sit on changed lines.
  * mutmut 3.x (Python, Linux / macOS / WSL2 only - it needs os.fork, so on native Windows `run_mutmut` returns an
    INCONCLUSIVE report instead of pretending): `parse_mutmut_*` read its results, `run_mutmut` drives it.

Both adapters apply the same fail-closed rules as the native engine: INCONCLUSIVE is never a pass, only mutants on
changed lines count, "not covered" counts against the score, tool-side errors are excluded from the score but make the
run INCONCLUSIVE when they are too many, and a diff with executable changes but zero mutants fails.

Report formats checked against: mutation-testing-elements schema v1 (Stryker) and mutmut 3.8.0 (`results --all true`,
`export-cicd-stats`, `show NAME`) on 2026-10-25.

Standard library only. Python 3.10+.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from core.platform_compat import IS_WINDOWS, build_sandbox_env, run_process
    from core.verification import mutation_gate as mg
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from core.platform_compat import IS_WINDOWS, build_sandbox_env, run_process
    from core.verification import mutation_gate as mg

Range = Tuple[int, int]

STRYKER_DETECTED = {"Killed", "Timeout"}
STRYKER_UNDETECTED = {"Survived", "NoCoverage"}
STRYKER_EXCLUDED = {"CompileError", "RuntimeError", "Ignored", "Pending"}


def _in_ranges(line: int, ranges: Sequence[Range]) -> bool:
    return any(a <= line <= b for a, b in ranges)


def score_counts(detected: int, survived: int, not_covered: int, excluded: int, threshold: float, *, planned: Optional[int] = None,
                 who: str = "mutation tool") -> mg.MutationReport:
    """Shared verdict logic (same wording and thresholds as mutation_gate)."""
    rep = mg.MutationReport(mg.INCONCLUSIVE, "", threshold=threshold)
    denominator = detected + survived + not_covered
    rep.counts = {mg.KILLED: detected, mg.SURVIVED: survived, mg.NO_TESTS: not_covered, mg.ERROR: excluded}
    rep.executed = denominator
    rep.planned = planned if planned is not None else denominator + excluded
    if not (0 < threshold <= 1):
        rep.status, rep.reason = mg.FAIL, f"invalid threshold {threshold}"
        return rep
    if denominator == 0:
        rep.status, rep.reason = mg.INCONCLUSIVE, f"{who} produced no usable mutant result (excluded: {excluded})"
        return rep
    rep.score = round(detected / denominator, 4)
    if rep.score >= threshold:
        rep.status, rep.reason = mg.PASS, f"kill score {rep.score:.3f} >= {threshold:.2f} ({detected}/{denominator})"
        if excluded > max(2, 0.25 * rep.planned):
            rep.status, rep.reason = mg.INCONCLUSIVE, f"{excluded} of {rep.planned} mutants were invalid or errored in {who}; the score is not trustworthy"
    else:
        rep.status, rep.reason = mg.FAIL, f"kill score {rep.score:.3f} < {threshold:.2f} ({detected}/{denominator} detected, {survived} survived, {not_covered} not covered)"
    return rep


# ------------------------------------------------------------------------------------------------ Stryker (JS / TS)
STRYKER_REPORT = "reports/mutation/mutation.json"  # the json reporter's default; the CLI cannot change it (config only)


def stryker_command(changed: Dict[str, Sequence[Range]], *, config: Optional[str] = None,
                    runner_args: Sequence[str] = (), concurrency: Optional[int] = None) -> List[str]:
    """`npx stryker run ...` limited to the changed line ranges (Stryker's native `--mutate file:a-b` syntax)."""
    cmd = ["npx", "--no-install", "stryker", "run"]
    if config:
        cmd.append(config)
    for rel, ranges in sorted(changed.items()):
        for a, b in ranges:
            cmd += ["--mutate", f"{rel}:{a}-{b}"]
    cmd += ["--reporters", "json", "--cleanTempDir", "true"]
    if concurrency:
        cmd += ["--concurrency", str(int(concurrency))]
    return cmd + list(runner_args)


def parse_stryker(report, changed: Optional[Dict[str, Sequence[Range]]] = None, threshold: float = mg.DEFAULT_THRESHOLD) -> mg.MutationReport:
    """Verdict from a Stryker JSON report (dict or path). With `changed`, only mutants whose START line is inside a
    changed range are scored: a worker cannot dilute a bad score with untouched code, nor hide behind it."""
    if not isinstance(report, dict):
        try:
            report = json.loads(Path(report).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return mg.MutationReport(mg.INCONCLUSIVE, f"STRYKER_REPORT_UNREADABLE: {exc}", threshold=threshold)
    files = report.get("files")
    if not isinstance(files, dict):
        return mg.MutationReport(mg.INCONCLUSIVE, "STRYKER_REPORT_MALFORMED: no `files` object", threshold=threshold)
    detected = survived = not_covered = excluded = 0
    survivors: List[dict] = []
    per_file: Dict[str, int] = {}
    unknown = set()
    norm = {k.replace("\\", "/"): v for k, v in (changed or {}).items()}
    for fname, data in sorted(files.items()):
        key = fname.replace("\\", "/")
        ranges = None
        if changed is not None:
            ranges = norm.get(key)
            if ranges is None:
                ranges = next((v for k, v in norm.items() if key.endswith("/" + k)), None)
            if not ranges:
                continue
        for m in (data or {}).get("mutants", []):
            try:
                line = int(m["location"]["start"]["line"])
            except (KeyError, TypeError, ValueError):
                continue
            if ranges is not None and not _in_ranges(line, ranges):
                continue
            status = m.get("status", "")
            per_file[key] = per_file.get(key, 0) + 1
            if status in STRYKER_DETECTED:
                detected += 1
            elif status == "Survived":
                survived += 1
                survivors.append({"file": key, "line": line, "operator": m.get("mutatorName", ""), "status": "survived",
                                  "description": str(m.get("replacement", ""))[:80]})
            elif status == "NoCoverage":
                not_covered += 1
                survivors.append({"file": key, "line": line, "operator": m.get("mutatorName", ""), "status": "no_tests",
                                  "description": str(m.get("replacement", ""))[:80]})
            elif status in STRYKER_EXCLUDED:
                excluded += 1
            else:
                unknown.add(status)
                excluded += 1
    total = detected + survived + not_covered + excluded
    if changed is not None and total == 0:
        rep = mg.MutationReport(mg.FAIL, "NO_MUTANTS: Stryker produced no mutants on the changed lines", threshold=threshold)
        return rep
    rep = score_counts(detected, survived, not_covered, excluded, threshold, who="Stryker")
    rep.survivors = survivors[:50]
    rep.files = per_file
    if unknown:
        rep.notes.append("unknown Stryker statuses treated as excluded: " + ", ".join(sorted(map(str, unknown))))
    return rep


# ------------------------------------------------------------------------------------------------ mutmut (Python, POSIX)
_RESULT_LINE = re.compile(r"^\s*(?P<name>\S+?):\s*(?P<status>[a-z_ ]+?)\s*$")
MUTMUT_DETECTED = {"killed", "timeout", "caught by type check"}
MUTMUT_UNDETECTED = {"survived"}
MUTMUT_NOT_COVERED = {"no tests"}
MUTMUT_EXCLUDED = {"skipped", "suspicious", "segfault", "check was interrupted by user", "not checked"}


def parse_mutmut_results(text: str) -> Dict[str, str]:
    """`python -m mutmut results --all true` -> {mutant name: status}."""
    out: Dict[str, str] = {}
    for line in text.splitlines():
        m = _RESULT_LINE.match(line)
        if m and "__mutmut_" in m.group("name"):
            out[m.group("name")] = m.group("status").strip().lower()
    return out


def parse_mutmut_diff(diff: str) -> Optional[int]:
    """Original line number of the first changed line in the unified diff printed by `mutmut show NAME`."""
    lineno = None
    for raw in diff.splitlines():
        h = re.match(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@", raw)
        if h:
            lineno = int(h.group(1))
            continue
        if lineno is None or raw.startswith(("---", "+++")):
            continue
        if raw.startswith("-"):
            return lineno
        if raw.startswith("+"):
            return lineno  # pure insertion: the line it was inserted before
        lineno += 1
    return None


def parse_mutmut_stats(data: dict) -> Dict[str, int]:
    keys = ("killed", "survived", "total", "no_tests", "skipped", "suspicious", "timeout", "segfault")
    return {k: int(data.get(k, 0) or 0) for k in keys}


def mutmut_verdict(statuses: Dict[str, str], lines: Dict[str, Optional[int]], changed: Optional[Dict[str, Sequence[Range]]],
                   threshold: float = mg.DEFAULT_THRESHOLD) -> mg.MutationReport:
    """statuses: {name: status}; lines: {name: original line or None}. Names look like `pkg.mod.x_func__mutmut_3`; the file is
    derived from the dotted module path and matched against `changed` by suffix."""
    detected = survived = not_covered = excluded = 0
    survivors: List[dict] = []
    per_file: Dict[str, int] = {}
    norm = {k.replace("\\", "/"): v for k, v in (changed or {}).items()}
    for name, status in sorted(statuses.items()):
        module = name.rsplit(".", 1)[0] if "." in name else ""
        rel_guess = module.replace(".", "/") + ".py"
        ranges = None
        file_rel = rel_guess
        if changed is not None:
            for k, v in norm.items():
                if k == rel_guess or k.endswith("/" + rel_guess) or rel_guess.endswith("/" + k) or k.replace("/__init__.py", ".py") == rel_guess:
                    ranges, file_rel = v, k
                    break
            if not ranges:
                continue
            ln = lines.get(name)
            if ln is None or not _in_ranges(ln, ranges):
                continue
        per_file[file_rel] = per_file.get(file_rel, 0) + 1
        if status in MUTMUT_DETECTED:
            detected += 1
        elif status in MUTMUT_UNDETECTED:
            survived += 1
            survivors.append({"file": file_rel, "line": lines.get(name) or 0, "operator": "mutmut", "status": "survived", "description": name.rsplit(".", 1)[-1]})
        elif status in MUTMUT_NOT_COVERED:
            not_covered += 1
            survivors.append({"file": file_rel, "line": lines.get(name) or 0, "operator": "mutmut", "status": "no_tests", "description": name.rsplit(".", 1)[-1]})
        else:
            excluded += 1
    if changed is not None and (detected + survived + not_covered + excluded) == 0:
        return mg.MutationReport(mg.FAIL, "NO_MUTANTS: mutmut produced no mutants on the changed lines", threshold=threshold)
    rep = score_counts(detected, survived, not_covered, excluded, threshold, who="mutmut")
    rep.survivors = survivors[:50]
    rep.files = per_file
    rep.notes.append("mutmut only mutates code inside functions and produces fewer mutants than mutation_gate; prefer mutation_gate where possible")
    return rep


def run_mutmut(worktree, changed: Dict[str, Sequence[Range]], *, python: str = sys.executable, threshold: float = mg.DEFAULT_THRESHOLD,
               timeout: float = 900.0, source_paths: Sequence[str] = (), test_args: Sequence[str] = ()) -> mg.MutationReport:
    """Run mutmut 3.x in a sandbox copy of `worktree` and score the mutants on `changed`. Never modifies `worktree`."""
    if IS_WINDOWS:
        return mg.MutationReport(mg.INCONCLUSIVE, "MUTMUT_UNSUPPORTED_ON_WINDOWS: mutmut needs os.fork; use mutation_gate or run under WSL2", threshold=threshold)
    probe = run_process([python, "-c", "import mutmut,sys;print(getattr(mutmut,'__version__','?'))"], timeout=30)
    if probe.returncode != 0:
        return mg.MutationReport(mg.INCONCLUSIVE, "MUTMUT_NOT_INSTALLED: pip install mutmut (Linux / macOS / WSL2)", threshold=threshold)
    root = Path(worktree).resolve()
    with tempfile.TemporaryDirectory(prefix="mutmut_", ignore_cleanup_errors=True) as tmp:
        sb = Path(tmp) / "tree"
        shutil.copytree(root, sb, ignore=shutil.ignore_patterns(".git", ".planner", "__pycache__", "mutants", ".pytest_cache", "node_modules", ".venv"))
        srcs = list(source_paths) or sorted({str(Path(r).parent) if str(Path(r).parent) != "." else r for r in changed if r.endswith(".py")})
        pyproject = sb / "pyproject.toml"
        if not pyproject.exists():
            body = "[tool.mutmut]\nsource_paths = [" + ", ".join(json.dumps(s) for s in srcs) + "]\n"
            if test_args:
                body += "pytest_add_cli_args_test_selection = [" + ", ".join(json.dumps(a) for a in test_args) + "]\n"
            pyproject.write_text(body, encoding="utf-8")
        env = build_sandbox_env({"PYTHONPATH": str(sb), "PYTHONHASHSEED": "0"})
        run = run_process([python, "-m", "mutmut", "run"], cwd=str(sb), env=env, timeout=timeout)
        if run.timed_out:
            return mg.MutationReport(mg.INCONCLUSIVE, "MUTMUT_TIMEOUT: mutmut did not finish in time", threshold=threshold)
        res = run_process([python, "-m", "mutmut", "results", "--all", "true"], cwd=str(sb), env=env, timeout=120)
        statuses = parse_mutmut_results(res.stdout)
        if not statuses:
            tail = (run.stdout + run.stderr)[-300:].replace("\n", " ")
            return mg.MutationReport(mg.INCONCLUSIVE, f"MUTMUT_NO_RESULTS: {tail}", threshold=threshold)
        lines: Dict[str, Optional[int]] = {}
        for name in statuses:
            show = run_process([python, "-m", "mutmut", "show", name], cwd=str(sb), env=env, timeout=60)
            lines[name] = parse_mutmut_diff(show.stdout)
        return mutmut_verdict(statuses, lines, changed, threshold)


# ------------------------------------------------------------------------------------------------ Stryker runner
STRYKER_CONFIGS = ("stryker.config.json", "stryker.config.js", "stryker.config.mjs", "stryker.config.cjs", ".strykerrc.json", ".strykerrc.js")


def _stryker_installed(root: Path) -> bool:
    return any((root / "node_modules" / ".bin" / n).exists() for n in ("stryker", "stryker.cmd"))


def run_stryker(worktree, changed: Dict[str, Sequence[Range]], *, threshold: float = mg.DEFAULT_THRESHOLD, timeout: float = 900.0,
                test_command: str = "", concurrency: int = 2) -> mg.MutationReport:
    """Run the project's own StrykerJS (installed in its node_modules; nothing is downloaded) on a COPY of the worktree,
    limited to the changed line ranges, and score the JSON report. Never modifies `worktree`.

    The project needs a Stryker config (stryker.config.*) or the caller passes `test_command` (for example `npm test`,
    used with Stryker's built-in `command` runner and coverageAnalysis off)."""
    root = Path(worktree).resolve()
    js = {k: v for k, v in changed.items() if re.search(r"\.(?:[cm]?[jt]sx?)$", k) and v}
    if not js:
        return mg.MutationReport(mg.NOT_APPLICABLE, "no JavaScript/TypeScript lines changed", threshold=threshold)
    if not _stryker_installed(root):
        return mg.MutationReport(mg.INCONCLUSIVE, "STRYKER_NOT_INSTALLED: add @stryker-mutator/core to the project (npm i -D @stryker-mutator/core) or waive the mutation check explicitly",
                                 threshold=threshold)
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx:
        return mg.MutationReport(mg.INCONCLUSIVE, "NODE_NOT_FOUND: npx is not on PATH", threshold=threshold)
    has_config = any((root / c).is_file() for c in STRYKER_CONFIGS)
    if not has_config and not test_command:
        return mg.MutationReport(mg.INCONCLUSIVE, "STRYKER_NO_CONFIG: no stryker.config.* in the project and no test command given", threshold=threshold)
    with tempfile.TemporaryDirectory(prefix="stryker_", ignore_cleanup_errors=True) as tmp:
        sb = Path(tmp) / "tree"
        shutil.copytree(root, sb, symlinks=True, ignore=shutil.ignore_patterns(".git", ".planner", "node_modules", ".stryker-tmp", "reports", "__pycache__"))
        nm = root / "node_modules"
        try:
            os.symlink(nm, sb / "node_modules", target_is_directory=True)
        except (OSError, NotImplementedError):
            shutil.copytree(nm, sb / "node_modules", symlinks=True)
        report_rel = STRYKER_REPORT
        extra: List[str] = []
        if not has_config:
            extra += ["--testRunner", "command", "--commandRunner.command", test_command, "--coverageAnalysis", "off"]
        cmd = stryker_command(js, runner_args=extra, concurrency=concurrency)
        cmd[0] = npx
        env = build_sandbox_env({"CI": "true", "FORCE_COLOR": "0"})
        res = run_process(cmd, cwd=str(sb), env=env, timeout=timeout)
        if res.timed_out:
            return mg.MutationReport(mg.INCONCLUSIVE, "STRYKER_TIMEOUT: Stryker did not finish in time", threshold=threshold)
        rp = sb / report_rel
        if not rp.is_file():
            found = sorted((sb / "reports").rglob("mutation*.json")) if (sb / "reports").is_dir() else []
            rp = found[0] if found else rp
        if not rp.is_file():
            full = re.sub(r"\x1b\[[0-9;]*m", "", (res.stdout + res.stderr))
            tail = full[-400:].replace("\n", " ")
            if "failed tests in the initial test run" in full:
                return mg.MutationReport(mg.FAIL, "BASELINE_NOT_GREEN: the tests fail before any mutation; a score would be meaningless", threshold=threshold)
            return mg.MutationReport(mg.INCONCLUSIVE, f"STRYKER_NO_REPORT: {tail}", threshold=threshold)
        return parse_stryker(rp, js, threshold)

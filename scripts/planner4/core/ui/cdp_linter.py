"""cdp_linter.py - Python bridge to the CDP kinematic/optical linter (core/ui/cdp-kinematic-optical-linter.mjs).

The linter needs Node >= 18, the `playwright` package and a Chromium. None of them is a Python dependency, so
this module never imports anything outside the standard library: it locates node, runs the .mjs through
`platform_compat.run_process` (hard timeout, process-tree kill, bounded output) and returns a typed result.

Verdicts: HUMAN_CRAFT_VERIFIED | REJECTED_AI_SLOP | INCONCLUSIVE | TOOLING_ERROR.
`CdpResult.passed` is True only for HUMAN_CRAFT_VERIFIED: INCONCLUSIVE and TOOLING_ERROR are NOT a pass, and the
caller (verifier / orchestrator) must treat them as "needs human or retry", never as green.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
from core.platform_compat import build_sandbox_env, find_executable, run_process  # noqa: E402

LINTER = HERE / "cdp-kinematic-optical-linter.mjs"
VERDICTS = ("HUMAN_CRAFT_VERIFIED", "REJECTED_AI_SLOP", "INCONCLUSIVE", "TOOLING_ERROR")
EXIT_FOR = {"HUMAN_CRAFT_VERIFIED": 0, "REJECTED_AI_SLOP": 1, "TOOLING_ERROR": 2, "INCONCLUSIVE": 3}
_PASSTHROUGH = ("PLANNER_CHROMIUM_PATH", "PLANNER_PLAYWRIGHT_PATH", "NODE_PATH", "PLAYWRIGHT_BROWSERS_PATH",
                "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA")


class CdpUnavailable(RuntimeError):
    """node, playwright or Chromium cannot be found (an environment problem, not a design verdict)."""


@dataclass
class CdpResult:
    verdict: str
    exit_code: int
    report: dict = field(default_factory=dict)
    stderr: str = ""
    error: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict == "HUMAN_CRAFT_VERIFIED"

    @property
    def checks(self) -> dict:
        return self.report.get("checks", {})

    def failures(self) -> list:
        """Short human-readable lines for every failed / undecided check (for ticket feedback)."""
        out = []
        for name, c in self.checks.items():
            st = c.get("status")
            if st in ("fail", "inconclusive"):
                why = c.get("reason") or ""
                for el in c.get("elements", []) or []:
                    if el.get("status") in ("fail", "inconclusive"):
                        bits = [f"{k}={el[k]}" for k in ("lc", "need", "asymmetryPx") if k in el]
                        why += f" [{el.get('path') or el.get('tag')}: {el.get('reason') or ' '.join(bits)}]"
                if name == "spring" and "fittedZeta" in c:
                    why += f" fitted zeta {c['fittedZeta']} vs {c['targetZeta']}"
                if name == "interrupt" and c.get("worstJump") is not None:
                    why += f" velocity jump {c['worstJump']}"
                out.append(f"{name}: {st}{(' - ' + why.strip()) if why.strip() else ''}")
        if self.error:
            out.append(f"tooling: {self.error}")
        return out


def normalize_url(u: str) -> str:
    """URLs pass through; a local path is resolved against the CALLER's cwd (the node process runs elsewhere)."""
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]+:", u) and not re.match(r"^[a-zA-Z]:[\\/]", u):
        return u
    return Path(u).resolve().as_uri()


def find_node(explicit: Optional[str] = None) -> Optional[str]:
    return explicit or os.environ.get("PLANNER_NODE") or find_executable("node")


def node_version(node: str, timeout: float = 15.0) -> Optional[tuple]:
    r = run_process([node, "--version"], timeout=timeout)
    m = re.match(r"v(\d+)\.(\d+)\.(\d+)", (r.stdout or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def spec_to_options(kinematic_spec: Optional[Mapping], *, interrupt: bool = True) -> dict:
    """Map a ticket's `worker_ui.kinematic_spec` (spec section 6) onto linter options."""
    ks = dict(kinematic_spec or {})
    opts: dict = {}
    if ks.get("target_apca_lc") is not None:
        opts["target_lc"] = float(ks["target_apca_lc"])
    if ks.get("spring_damping_zeta") is not None:
        opts["zeta"] = float(ks["spring_damping_zeta"])
        opts["interrupt"] = bool(interrupt)
    trim = ks.get("optical_trim")
    opts["check_optical"] = trim not in (False, "off", "none", "disabled")
    return opts


def build_command(node: str, url: str, selector: str, *, target_lc=None, zeta=None, zeta_tol=None, interrupt=False,
                  optical_max_px=None, check_optical=True, check_contrast=True, chromium=None, nav_timeout_ms=None) -> list:
    cmd = [node, str(LINTER), "--url", url, "--selector", selector, "--json"]
    if target_lc is not None:
        cmd += ["--target-lc", repr(float(target_lc))]
    if zeta is not None:
        cmd += ["--zeta", repr(float(zeta))]
    if zeta_tol is not None:
        cmd += ["--zeta-tol", repr(float(zeta_tol))]
    if optical_max_px is not None:
        cmd += ["--optical-max-px", repr(float(optical_max_px))]
    if nav_timeout_ms is not None:
        cmd += ["--timeout", str(int(nav_timeout_ms))]
    if chromium:
        cmd += ["--chromium", str(chromium)]
    if interrupt:
        cmd.append("--interrupt")
    if not check_optical:
        cmd.append("--no-optical")
    if not check_contrast:
        cmd.append("--no-contrast")
    return cmd


def _last_json(text: str) -> Optional[dict]:
    for line in reversed((text or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def run_cdp_linter(url: str, selector: str, *, timeout: float = 180.0, node: Optional[str] = None,
                   cwd: Optional[str] = None, extra_env: Optional[Mapping[str, str]] = None, **options) -> CdpResult:
    """Run the linter. Never raises for environment problems: they come back as TOOLING_ERROR (exit code 2)."""
    exe = find_node(node)
    if not exe:
        return CdpResult("TOOLING_ERROR", 2, error="node was not found on PATH (install Node >= 18 or set PLANNER_NODE)")
    if not LINTER.exists():  # pragma: no cover - broken install
        return CdpResult("TOOLING_ERROR", 2, error=f"linter script missing: {LINTER}")
    ver = node_version(exe)
    if ver is not None and ver[0] < 18:
        return CdpResult("TOOLING_ERROR", 2, error=f"Node {'.'.join(map(str, ver))} is too old (need >= 18)")
    cmd = build_command(exe, normalize_url(url), selector, **options)
    env = build_sandbox_env(extra_env, keep=_PASSTHROUGH)
    proc = run_process(cmd, cwd=cwd or str(HERE), env=env, timeout=timeout, max_output=2_000_000)
    if proc.timed_out:
        return CdpResult("TOOLING_ERROR", 2, stderr=proc.stderr, error=f"linter timed out after {timeout:.0f}s")
    report = _last_json(proc.stdout)
    if report is None:
        return CdpResult("TOOLING_ERROR", 2, stderr=proc.stderr, error=(proc.error or proc.stderr or "linter printed no JSON").strip()[:500])
    verdict = report.get("verdict", "TOOLING_ERROR")
    if verdict not in VERDICTS:
        verdict = "TOOLING_ERROR"
    return CdpResult(verdict, EXIT_FOR[verdict], report=report, stderr=proc.stderr, error=str(report.get("error", "")) if verdict == "TOOLING_ERROR" else "")


def available(node: Optional[str] = None) -> tuple:
    """(ok, reason). Cheap probe used by tests and by the verifier before it schedules a UI ticket."""
    exe = find_node(node)
    if not exe:
        return False, "node not found"
    probe = run_process([exe, "-e", "import('" + (HERE / 'cdp-kinematic-optical-linter.mjs').as_uri() + "')"
                         ".then(m=>m.loadChromium()).then(()=>process.exit(0)).catch(e=>{console.error(e.message);process.exit(2)})"],
                        cwd=str(HERE), env=build_sandbox_env(None, keep=_PASSTHROUGH), timeout=60)
    if probe.returncode == 0:
        return True, ""
    return False, (probe.stderr or probe.error or "playwright not importable").strip()[:300]


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Run the CDP kinematic/optical linter and print the JSON report")
    ap.add_argument("url")
    ap.add_argument("selector")
    ap.add_argument("--target-lc", type=float)
    ap.add_argument("--zeta", type=float)
    ap.add_argument("--zeta-tol", type=float)
    ap.add_argument("--interrupt", action="store_true")
    ap.add_argument("--no-optical", action="store_true")
    ap.add_argument("--no-contrast", action="store_true")
    ap.add_argument("--timeout", type=float, default=180.0)
    a = ap.parse_args(argv)
    r = run_cdp_linter(a.url, a.selector, timeout=a.timeout, target_lc=a.target_lc, zeta=a.zeta, zeta_tol=a.zeta_tol,
                       interrupt=a.interrupt, check_optical=not a.no_optical, check_contrast=not a.no_contrast)
    print(json.dumps({"verdict": r.verdict, "exit_code": r.exit_code, "failures": r.failures(), "report": r.report}, indent=2))
    return r.exit_code


if __name__ == "__main__":
    sys.exit(main())

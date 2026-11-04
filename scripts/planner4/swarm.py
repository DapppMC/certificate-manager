#!/usr/bin/env python3
"""swarm.py - dispatch worker tasks to the AG1..AG8 fleet, in parallel, with leases and MASP port isolation.

Legacy contract (Planner 3.0 swarm) is kept, so existing callers keep working:

    python swarm.py parallel --spec tasks.json
    tasks.json = [ {"worker_id": 3, "prompt": "...", "workspace": "ws3", "model": "...", "effort": "high", "timeout": 600}, ... ]
    stdout     = {"orchestrator_summary": "...", "worker_results": {"worker_3": {"status", "duration_seconds", "output", "stderr"}}}

What 4.0 adds (all additive keys):
  * `worker_id` may be 3, "3", "worker_3" or "AG3". AG8 (The Arbiter) is an ACTIVE worker with its own ports 3700-3799 /
    8700-8799; with `"standby_for": "AG2"` it instead takes over the failed worker's scope AND ports (escalation solver).
  * optional `tier` ("premium" | "standard"): the model tier of this run, exported as PLANNER_MODEL_TIER.
  * optional `ticket` and `paths`: the task then takes an ATOMIC LEASE on those path globs on the blackboard before it
    starts (all or nothing, directory/glob/`**` aware), renews it with a heartbeat, releases it afterwards, and gets the
    fencing token as PLANNER_FENCE. Two tasks whose paths overlap never run at the same time.
  * every task receives its MASP ports (DEV_PORT / API_PORT / PORT and the ranges) and a scrubbed environment: secrets
    are NOT inherited. Credentials an agent CLI needs must be named explicitly with `--pass-env NAME,...`.
  * pluggable executors: `dry-run` (default; nothing is executed), `command` (any agent CLI through a template with
    {prompt_file} {workspace} {model} {effort} {worker} {ticket} {task}) and `legacy` (delegates one task at a time to
    the original swarm.py named by PLANNER_LEGACY_SWARM). Tests and the orchestrator can pass a Python callable.
  * a hard timeout that kills the whole process tree (platform_compat.run_process).

The agent itself is outside this repository: Antigravity / Gemini CLI / Claude Code are driven through the `command`
executor. The default is `dry-run` so that nothing is ever run by accident.

Honest limits: the workspace is a directory, not a container; a process that ignores its environment can still
bind any port or read files the OS user can read. MASP is enforced for cooperating workers, checked statically
by the verifier, and observable by the operator - not by the kernel.

Standard library only. Python 3.10+.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from core import fleet  # noqa: E402
from core.platform_compat import IS_WINDOWS, atomic_write_text, build_sandbox_env, find_executable, normalize_rel_path, run_process  # noqa: E402

OK, FAILED, TIMEOUT, ERROR, BLOCKED, DRY, LEASE_LOST, REJECTED = "ok", "failed", "timeout", "error", "blocked", "dry_run", "lease_lost", "rejected"
MAX_OUTPUT = 20_000
DEFAULT_TIMEOUT = 900.0
MAX_TIMEOUT = 7200.0
EFFORT_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,24}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9_.:/@\-]{1,80}$")
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}$")


class SwarmError(ValueError):
    pass


def parse_worker_id(raw) -> str:
    """3 | '3' | 'worker_3' | 'AG3' -> 'AG3'. Raises SwarmError for anything else."""
    if isinstance(raw, bool):
        raise SwarmError(f"bad worker id {raw!r}")
    s = str(raw).strip()
    m = re.fullmatch(r"(?i)(?:worker[_\-]?|ag)?(\d)", s)
    if not m:
        raise SwarmError(f"bad worker id {raw!r}: use 1..8, 'worker_N' or 'AGN'")
    wid = f"AG{int(m.group(1))}"
    if wid not in fleet.FLEET:
        raise SwarmError(f"unknown worker {wid}; the fleet is {', '.join(fleet.FLEET)}")
    return wid


def legacy_key(worker_id: str) -> str:
    return f"worker_{worker_id[2:]}"


@dataclass
class Task:
    worker: str
    prompt: str
    workspace: str
    id: str = ""
    ticket: str = ""
    model: str = ""
    effort: str = ""
    timeout: float = DEFAULT_TIMEOUT
    paths: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    standby_for: str = ""
    lease_ttl: float = 0.0
    tier: str = ""

    @staticmethod
    def from_legacy(d: Mapping, index: int = 0) -> "Task":
        if not isinstance(d, Mapping):
            raise SwarmError(f"task #{index} must be an object")
        unknown = set(d) - {"worker_id", "worker", "prompt", "workspace", "model", "effort", "timeout", "ticket", "paths", "id", "env", "standby_for", "lease_ttl", "tier"}
        if unknown:
            raise SwarmError(f"task #{index}: unknown keys {sorted(unknown)}")
        raw = d.get("worker_id", d.get("worker"))
        if raw is None:
            raise SwarmError(f"task #{index}: worker_id is required")
        prompt = d.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise SwarmError(f"task #{index}: prompt must be a non-empty string")
        ws = d.get("workspace") or "."
        timeout = float(d.get("timeout", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT)
        if not (1 <= timeout <= MAX_TIMEOUT):
            raise SwarmError(f"task #{index}: timeout must be within 1..{MAX_TIMEOUT:g} seconds")
        model, effort = str(d.get("model") or ""), str(d.get("effort") or "")
        if model and not MODEL_RE.match(model):
            raise SwarmError(f"task #{index}: suspicious model string")
        if effort and not EFFORT_RE.match(effort):
            raise SwarmError(f"task #{index}: suspicious effort string")
        paths = d.get("paths") or []
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise SwarmError(f"task #{index}: paths must be a list of strings")
        env = d.get("env") or {}
        if not isinstance(env, Mapping):
            raise SwarmError(f"task #{index}: env must be an object")
        wid = parse_worker_id(raw)
        standby_for = str(d.get("standby_for") or "")
        if standby_for:
            standby_for = parse_worker_id(standby_for)
        t = Task(worker=wid, prompt=prompt, workspace=str(ws), id=str(d.get("id") or ""), ticket=str(d.get("ticket") or ""), model=model, effort=effort,
                 timeout=timeout, paths=list(paths), env={str(k): str(v) for k, v in env.items()}, standby_for=standby_for,
                 lease_ttl=float(d.get("lease_ttl") or 0.0), tier=str(d.get("tier") or ""))
        return t

    def validate(self) -> None:
        w = fleet.get(self.worker)
        if self.standby_for and w.id != "AG8":
            raise SwarmError("standby_for is only valid for AG8 (the escalation solver)")
        if self.standby_for:
            replaced = fleet.get(self.standby_for)
            if replaced.id == "AG8":
                raise SwarmError("AG8 cannot replace itself")
            if not replaced.scope:
                raise SwarmError(f"AG8 can only replace a worker that owns a write scope; {replaced.id} ({replaced.title}) does not")
        if self.tier and self.tier not in (fleet.PREMIUM, fleet.STANDARD):
            raise SwarmError(f"tier must be {fleet.PREMIUM!r} or {fleet.STANDARD!r}, not {self.tier!r}")
        if self.id and not TASK_ID_RE.match(self.id):
            raise SwarmError(f"bad task id {self.id!r}")
        if self.paths and not self.ticket:
            raise SwarmError("a task with `paths` needs a `ticket` (the lease is recorded against it)")
        for p in self.paths:
            normalize_rel_path(p)  # raises PathError (a ValueError) for absolute paths, '..', reserved names, ...


@dataclass
class TaskResult:
    id: str
    worker: str
    status: str
    duration_seconds: float = 0.0
    output: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    ports: Dict[str, int] = field(default_factory=dict)
    fence: int = 0
    command: List[str] = field(default_factory=list)
    ticket: str = ""
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (OK, DRY)

    def legacy(self) -> dict:
        return {"status": self.status, "duration_seconds": round(self.duration_seconds, 3), "output": self.output, "stderr": self.stderr}

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["duration_seconds"] = round(self.duration_seconds, 3)
        return d


def _clip(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    return text[:head] + f"\n... [{len(text) - limit} characters omitted] ...\n" + text[-(limit - head):]


# ------------------------------------------------------------------------------------------------ executors
@dataclass
class ExecResult:
    status: str
    exit_code: Optional[int] = None
    output: str = ""
    stderr: str = ""
    command: List[str] = field(default_factory=list)


class Executor:
    name = "base"

    def run(self, task: Task, env: Dict[str, str], timeout: float, ctx: "RunContext") -> ExecResult:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class RunContext:
    task_id: str
    planner_dir: Path
    workspace: Path
    ports: Dict[str, int]


class DryRunExecutor(Executor):
    """Does nothing but report what would run. The default: nothing is executed unless the operator chooses an executor."""
    name = "dry-run"

    def run(self, task, env, timeout, ctx):
        shown = {k: v for k, v in env.items() if k.startswith(("PLANNER_", "MASP_", "DEV_PORT", "API_PORT", "PORT"))}
        return ExecResult(DRY, 0, f"[dry-run] {task.worker} in {ctx.workspace}\nprompt: {task.prompt[:200]}\nenv: {json.dumps(shown, sort_keys=True)}")


class CommandExecutor(Executor):
    """Runs any agent CLI. The prompt travels through a FILE (never through a shell string).

    `template`: a list of argv items or a string (split with shlex); placeholders {prompt_file} {workspace} {model}
    {effort} {worker} {ticket} {task}. `stdin_prompt=True` also pipes the prompt to stdin."""
    name = "command"

    def __init__(self, template: Sequence[str] | str, *, pass_env: Iterable[str] = (), stdin_prompt: bool = False):
        self.template = shlex.split(template, posix=not IS_WINDOWS) if isinstance(template, str) else list(template)
        if not self.template:
            raise SwarmError("empty command template")
        self.pass_env = [n for n in pass_env if n]
        self.stdin_prompt = stdin_prompt

    _PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

    def _extra_fmt(self, task: Task, ctx: RunContext) -> Dict[str, str]:
        """Hook for subclasses (the Antigravity bridge adds {profile_dir} and {account})."""
        return {}

    def _environment(self, task: Task, env: Mapping[str, str]) -> Dict[str, str]:
        """Environment handed to the agent process before scrubbing: the swarm's variables plus the operator's `--pass-env`."""
        extra = dict(env)
        for name in self.pass_env:  # an explicit, visible decision by the operator
            if name in os.environ:
                extra[name] = os.environ[name]
        return extra

    def argv(self, task: Task, ctx: RunContext, prompt_file: Path) -> List[str]:
        """Known placeholders are substituted; every other brace is left alone (agent CLIs and `python -c` code are full
        of them, so str.format would be wrong here)."""
        fmt = {"prompt_file": str(prompt_file), "workspace": str(ctx.workspace), "model": task.model, "effort": task.effort, "worker": task.worker,
               "ticket": task.ticket, "task": ctx.task_id}
        fmt.update(self._extra_fmt(task, ctx))
        return [self._PLACEHOLDER.sub(lambda m: fmt.get(m.group(1), m.group(0)), part) for part in self.template]

    def run(self, task, env, timeout, ctx):
        pdir = ctx.planner_dir / "prompts"
        pdir.mkdir(parents=True, exist_ok=True)
        pf = pdir / (re.sub(r"[^A-Za-z0-9_.\-]", "_", ctx.task_id) + ".txt")
        atomic_write_text(str(pf), task.prompt)
        argv = self.argv(task, ctx, pf)
        exe = find_executable(argv[0]) or argv[0]
        argv[0] = exe
        extra = self._environment(task, env)
        res = run_process(argv, cwd=str(ctx.workspace), env=build_sandbox_env(extra), timeout=timeout,
                          input_text=task.prompt if self.stdin_prompt else None)
        if res.error:
            return ExecResult(ERROR, res.returncode, res.stdout, res.stderr or res.error, argv)
        if res.timed_out:
            return ExecResult(TIMEOUT, res.returncode, res.stdout, (res.stderr + f"\ntimed out after {timeout:g}s; process tree killed").strip(), argv)
        return ExecResult(OK if res.returncode == 0 else FAILED, res.returncode, res.stdout, res.stderr, argv)


class LegacyExecutor(Executor):
    """Delegates ONE task to the original swarm.py (`parallel --spec`), for installations that already drive their
    agents through it. The path comes from PLANNER_LEGACY_SWARM; the legacy script receives the workspace as cwd."""
    name = "legacy"

    def __init__(self, script: Optional[str] = None, python: str = sys.executable):
        self.script = script or os.environ.get("PLANNER_LEGACY_SWARM", "")
        self.python = python
        if not self.script:
            raise SwarmError("legacy executor needs PLANNER_LEGACY_SWARM (path to the original swarm.py) or --legacy-script")

    def run(self, task, env, timeout, ctx):
        script = Path(self.script)
        if not script.is_file():
            return ExecResult(ERROR, None, "", f"legacy swarm script not found: {script}")
        spec = [{"worker_id": int(task.worker[2:]), "prompt": task.prompt, "workspace": str(ctx.workspace), "model": task.model or "default",
                 "effort": task.effort or "medium", "timeout": int(timeout)}]
        sf = ctx.planner_dir / "prompts" / (re.sub(r"[^A-Za-z0-9_.\-]", "_", ctx.task_id) + ".spec.json")
        sf.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(str(sf), json.dumps(spec))
        res = run_process([self.python, str(script), "parallel", "--spec", str(sf)], cwd=str(ctx.workspace), env=build_sandbox_env(env), timeout=timeout + 30)
        if res.error or res.timed_out:
            return ExecResult(TIMEOUT if res.timed_out else ERROR, res.returncode, res.stdout, res.stderr or res.error, [self.python, str(script), "parallel"])
        try:
            data = json.loads(res.stdout[res.stdout.index("{"):])
            wr = data["worker_results"][legacy_key(task.worker)]
        except (ValueError, KeyError, TypeError):
            return ExecResult(ERROR, res.returncode, res.stdout, "legacy swarm printed no parseable worker_results\n" + res.stderr)
        status = str(wr.get("status", "")).lower()
        mapped = OK if status in ("ok", "success", "completed", "done") else TIMEOUT if "time" in status else FAILED
        return ExecResult(mapped, res.returncode, str(wr.get("output", "")), str(wr.get("stderr", "")), [self.python, str(script), "parallel"])


class CallableExecutor(Executor):
    """Wrap a Python function `fn(task, env, ctx) -> str | ExecResult` (used by tests and by in-process agents)."""
    name = "callable"

    def __init__(self, fn: Callable):
        self.fn = fn

    def run(self, task, env, timeout, ctx):
        try:
            out = self.fn(task, env, ctx)
        except Exception as exc:  # an agent crash is a failed task, not a crashed swarm
            return ExecResult(ERROR, None, "", f"{type(exc).__name__}: {exc}")
        if isinstance(out, ExecResult):
            return out
        return ExecResult(OK, 0, "" if out is None else str(out))


# ------------------------------------------------------------------------------------------------ the swarm
class Swarm:
    def __init__(self, executor: Optional[Executor] = None, *, board=None, planner_dir=".planner", max_parallel: int = 4,
                 claim_wait: float = 30.0, heartbeat: bool = True):
        self.executor = executor or DryRunExecutor()
        self.board = board
        self.planner_dir = Path(planner_dir)
        self.max_parallel = max(1, min(int(max_parallel), len(fleet.FLEET)))
        self.claim_wait = claim_wait
        self.heartbeat = heartbeat
        self._ports_lock = threading.Lock()
        self._ports_in_use: Dict[str, set] = {}
        self._worker_locks: Dict[str, threading.Lock] = {w: threading.Lock() for w in fleet.FLEET}

    # -- single task ------------------------------------------------------------------------------
    def _alloc_ports(self, task: Task, tid: str) -> Optional[fleet.PortPlan]:
        w = fleet.get(task.standby_for or task.worker)
        if not w.has_ports:
            return None
        with self._ports_lock:
            used = self._ports_in_use.setdefault(w.id, set())
            plan = fleet.allocate_ports(w.id, task.ticket or tid, taken=used, probe=True)
            used.update(p for p in (plan.dev, plan.api) if p is not None)
            return plan

    def _free_ports(self, task: Task, plan: Optional[fleet.PortPlan]) -> None:
        if plan is None:
            return
        w = fleet.get(task.standby_for or task.worker)
        with self._ports_lock:
            self._ports_in_use.get(w.id, set()).difference_update({plan.dev, plan.api})

    def run_task(self, task: Task, index: int = 0) -> TaskResult:
        """One worker is one agent account: its tasks run one at a time (a second task for the same worker waits)."""
        wid = task.worker if task.worker in self._worker_locks else None
        if wid is None:
            return self._run_task(task, index)
        with self._worker_locks[wid]:
            return self._run_task(task, index)

    def _run_task(self, task: Task, index: int = 0) -> TaskResult:
        tid = task.id or f"{task.worker}-{index + 1}"
        started = time.monotonic()
        res = TaskResult(tid, task.worker, REJECTED, ticket=task.ticket)
        try:
            task.validate()
            ws = Path(task.workspace).resolve()
            if not ws.is_dir():
                raise SwarmError(f"workspace not found: {ws}")
        except (SwarmError, ValueError) as exc:
            res.stderr = str(exc)
            return res
        plan: Optional[fleet.PortPlan] = None
        fence, stop_hb, hb_thread, lost = 0, threading.Event(), None, threading.Event()
        lease_worker = task.worker
        try:
            if task.paths and self.board is not None:
                ttl = task.lease_ttl or min(max(task.timeout * 1.5 + 30, 60.0), 6 * 3600.0)
                claim = self.board.claim(lease_worker, task.ticket, task.paths, ttl=ttl, wait=self.claim_wait)
                if not claim.ok:
                    res.status, res.stderr = BLOCKED, f"could not lease {task.paths}: {claim}"
                    res.note = "lease conflict"
                    return res
                fence = claim.fence
                if self.heartbeat:
                    hb_thread = threading.Thread(target=self._beat, args=(lease_worker, task.ticket, ttl, stop_hb, lost), daemon=True)
                    hb_thread.start()
            elif task.paths:
                res.note = "no blackboard: paths were not leased"
            try:
                plan = self._alloc_ports(task, tid)
            except fleet.FleetError as exc:
                res.status, res.stderr = ERROR, str(exc)
                return res
            env = fleet.worker_env(task.standby_for or task.worker, task.ticket or tid, plan=plan, extra=task.env, tier=task.tier or None)
            if task.standby_for:
                env["PLANNER_STANDBY"] = task.worker
            if fence:
                env["PLANNER_FENCE"] = str(fence)
            ctx = RunContext(tid, self.planner_dir, ws, plan.env() if plan else {})
            er = self.executor.run(task, env, task.timeout, ctx)
            res.status, res.exit_code, res.command = er.status, er.exit_code, er.command
            res.output, res.stderr = _clip(er.output), _clip(er.stderr)
            if plan:
                res.ports = {k: int(v) for k, v in plan.env().items() if k in ("DEV_PORT", "API_PORT")}
            res.fence = fence
            if lost.is_set() and res.status == OK:
                res.status, res.note = LEASE_LOST, "the lease expired or was taken while the task ran: its result cannot be trusted"
        finally:
            stop_hb.set()
            if hb_thread is not None:
                hb_thread.join(timeout=2)
            if fence and self.board is not None:
                try:
                    self.board.release(lease_worker, ticket=task.ticket)
                except Exception as exc:
                    res.note = (res.note + f" release failed: {exc}").strip()
            self._free_ports(task, plan)
            res.duration_seconds = time.monotonic() - started
        return res

    def _beat(self, worker: str, ticket: str, ttl: float, stop: threading.Event, lost: threading.Event) -> None:
        interval = max(1.0, ttl / 3.0)
        while not stop.wait(interval):
            try:
                if self.board.heartbeat(worker, ticket, ttl=ttl) <= 0:
                    lost.set()
                    return
            except Exception:
                lost.set()
                return

    # -- many tasks -------------------------------------------------------------------------------
    def run(self, tasks: Sequence[Task]) -> List[TaskResult]:
        tasks = list(tasks)
        seen = set()
        for i, t in enumerate(tasks):
            tid = t.id or f"{t.worker}-{i + 1}"
            if tid in seen:
                raise SwarmError(f"duplicate task id {tid!r}")
            seen.add(tid)
        if len(tasks) <= 1 or self.max_parallel == 1:
            return [self.run_task(t, i) for i, t in enumerate(tasks)]
        with ThreadPoolExecutor(max_workers=min(self.max_parallel, len(tasks))) as ex:
            return list(ex.map(lambda p: self.run_task(p[1], p[0]), enumerate(tasks)))


def summarize(results: Sequence[TaskResult]) -> str:
    counts: Dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    body = ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
    return f"{len(results)} task(s): {body or 'none'}"


def legacy_report(results: Sequence[TaskResult], executor: str) -> dict:
    wr: Dict[str, dict] = {}
    for r in results:
        key = legacy_key(r.worker)
        n, k = 1, key
        while k in wr:
            n += 1
            k = f"{key}.{n}"
        wr[k] = r.legacy()
    return {"orchestrator_summary": summarize(results), "worker_results": wr, "schema": "swarm/4.0", "executor": executor,
            "tasks": [r.to_dict() for r in results]}


# ------------------------------------------------------------------------------------------------ CLI
def load_spec(path: str) -> List[Task]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SwarmError(f"cannot read spec {path}: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("tasks") or data.get("workers")
    if not isinstance(data, list) or not data:
        raise SwarmError("spec must be a non-empty JSON list of tasks (or an object with a `tasks` list)")
    return [Task.from_legacy(d, i) for i, d in enumerate(data)]


def build_executor(name: str, cmd: str = "", pass_env: str = "", legacy_script: str = "", stdin_prompt: bool = False) -> Executor:
    if name == "dry-run":
        return DryRunExecutor()
    if name == "command":
        template = cmd or os.environ.get("PLANNER_EXECUTOR_CMD", "")
        if not template:
            raise SwarmError("the command executor needs --cmd TEMPLATE or PLANNER_EXECUTOR_CMD")
        return CommandExecutor(template, pass_env=[x.strip() for x in pass_env.split(",")], stdin_prompt=stdin_prompt)
    if name == "legacy":
        return LegacyExecutor(legacy_script or None)
    if name == "antigravity":
        from core import antigravity_bridge  # lazy: the bridge subclasses CommandExecutor from this module
        template = cmd or os.environ.get("PLANNER_EXECUTOR_CMD", "")
        if not template:
            raise SwarmError("the antigravity executor needs --cmd TEMPLATE or PLANNER_EXECUTOR_CMD (your headless agent command; {profile_dir} is available)")
        return antigravity_bridge.BridgeExecutor(template, pass_env=[x.strip() for x in pass_env.split(",")], stdin_prompt=stdin_prompt)
    raise SwarmError(f"unknown executor {name!r}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="swarm", description="Dispatch tasks to the AG1..AG8 fleet (legacy `parallel --spec` contract + 4.0 extensions)")
    sub = ap.add_subparsers(dest="subcmd")
    p = sub.add_parser("parallel", help="run a tasks.json in parallel")
    p.add_argument("--spec", required=True)
    p.add_argument("--executor", choices=("dry-run", "command", "legacy", "antigravity"), default=os.environ.get("PLANNER_EXECUTOR", "dry-run"))
    p.add_argument("--cmd", default="", help="command template for the command executor")
    p.add_argument("--stdin-prompt", action="store_true")
    p.add_argument("--pass-env", default="", help="comma separated environment variable NAMES the agent CLI needs (credentials)")
    p.add_argument("--legacy-script", default="")
    p.add_argument("--max-parallel", type=int, default=4)
    p.add_argument("--board", default="", help="blackboard database for path leases (default .planner/board.db when it exists)")
    p.add_argument("--planner-dir", default=".planner")
    p.add_argument("--claim-wait", type=float, default=30.0)
    p.add_argument("--out", default="", help="also write the report to this file")
    sub.add_parser("fleet", help="print the worker table")
    q = sub.add_parser("ports", help="show the MASP ports a (worker, ticket) pair would get")
    q.add_argument("worker")
    q.add_argument("ticket")
    a = ap.parse_args(argv)
    if not a.subcmd:
        ap.print_help()
        return 0
    try:
        if a.subcmd == "fleet":
            print(json.dumps({"fleet": fleet.default_fleet_summary(), "table_problems": fleet.validate_table()}, indent=2))
            return 0
        if a.subcmd == "ports":
            plan = fleet.allocate_ports(parse_worker_id(a.worker), a.ticket)
            print(json.dumps({"worker": plan.worker, "dev": plan.dev, "api": plan.api}, indent=2))
            return 0
        tasks = load_spec(a.spec)
        executor = build_executor(a.executor, a.cmd, a.pass_env, a.legacy_script, a.stdin_prompt)
        board = None
        board_path = a.board or str(Path(a.planner_dir) / "board.db")
        if any(t.paths for t in tasks) and (a.board or Path(board_path).is_file()):
            from core.coordination.blackboard import Board
            board = Board(board_path)
        try:
            swarm = Swarm(executor, board=board, planner_dir=a.planner_dir, max_parallel=a.max_parallel, claim_wait=a.claim_wait)
            results = swarm.run(tasks)
        finally:
            if board is not None:
                board.close()
    except (SwarmError, ValueError, OSError) as exc:
        print(f"swarm: {exc}", file=sys.stderr)
        return 2
    report = legacy_report(results, executor.name)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    if a.out:
        atomic_write_text(a.out, text + "\n")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

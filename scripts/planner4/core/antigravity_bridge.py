"""antigravity_bridge.py - map the fleet (AG1..AG8) to the physical Antigravity account profiles on this machine and launch
headless agent tasks inside the right profile with a scrubbed environment.

One worker is one agent ACCOUNT, and an account is one profile directory on disk:

    Windows   AG1  %USERPROFILE%\\AppData\\Roaming\\Antigravity
              AG2..AG8  %USERPROFILE%\\AppData\\Roaming\\Antigravity<N>
    Linux     AG1  <home>                                  (its config lives in ~/.gemini/antigravity)
              AG2..AG8  <home>/AntigravityProfiles/Account<N>
    macOS     treated like Linux (the layout is the operator's; override with PLANNER_PROFILE_ROOT)
    WSL2      is Linux: <home> is the WSL home. Windows-side profiles are reachable only by overriding
              PLANNER_PROFILE_ROOT (e.g. /mnt/c/Users/<you>/AppData/Roaming), which is slow and loses permissions.

<home> is the current user's home (`Path.home()`), or PLANNER_AG_HOME. The profile root of AG2..AG8 can be moved with
PLANNER_PROFILE_ROOT. Nothing is hard-coded to one user name.

What the bridge does, and what it does not
------------------------------------------
* `profile_for()` / `profile_table()` are pure functions of (OS, home, env): they never touch the disk and can be
  evaluated for another OS (the tests do that for Windows on Linux). `probe_profiles()` checks the real disk.
* `profile_env()` / `sanitized_env()` build the environment of ONE headless task: the MASP variables (PLANNER_WORKER,
  DEV_PORT, API_PORT, PORT, MASP_DEV_RANGE, MASP_API_RANGE, PLANNER_MODEL_TIER, ...) plus the profile location
  (PLANNER_PROFILE_DIR, PLANNER_PROFILE_KIND, PLANNER_ACCOUNT). Everything else comes from the allow-listed sandbox base
  environment: secrets are dropped unless the operator names them with `pass_env`.
* On Linux/macOS an account profile is selected the way that layout implies: `HOME` is pointed at the profile directory
  (AG1 keeps the real home). On Windows the profile is an application data directory, not a home, so the bridge exports
  it and offers `{profile_dir}` in the command template; whether your CLI wants `--user-data-dir {profile_dir}` or
  something else is the operator's knowledge. THIS MODULE DOES NOT KNOW Antigravity's command-line flags and invents
  none: the agent command is always yours (`--cmd` / PLANNER_EXECUTOR_CMD).
* A missing profile directory is an error before anything runs (the account was never logged in on this machine), not a
  silent run under the wrong account.
* The prompt travels through a file, never through a shell string; the process runs under platform_compat.run_process
  (new process group, hard timeout, the whole tree is killed).

CLI:  python -m core.antigravity_bridge profiles | check | env --worker AG3 --ticket T-1 | launch --worker AG3 --cmd "..." --prompt "..."

Standard library only. Python 3.10+.
"""
from __future__ import annotations

import argparse
import json
import ntpath
import os
import platform
import posixpath
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import fleet  # noqa: E402
from core.platform_compat import build_sandbox_env, find_executable, run_process  # noqa: E402

WINDOWS, LINUX, MACOS = "windows", "linux", "macos"
DEFAULT_KIND, ACCOUNT_KIND = "default", "account"
ENV_HOME = "PLANNER_AG_HOME"
ENV_ROOT = "PLANNER_PROFILE_ROOT"
# the variables a task is guaranteed to see (the spec's list); more are added by fleet.worker_env
REQUIRED_ENV = ("PLANNER_WORKER", "DEV_PORT", "API_PORT", "MASP_DEV_RANGE", "MASP_API_RANGE")


class BridgeError(ValueError):
    pass


# ----------------------------------------------------------------------------------------------------- platform
def detect_os(system: Optional[str] = None) -> str:
    """`platform.system()` -> 'windows' | 'linux' | 'macos'. WSL2 reports 'Linux' and is Linux here (see detect_wsl)."""
    name = (system if system is not None else platform.system()).strip().lower()
    if name.startswith(("win", "cygwin", "msys")):
        return WINDOWS
    if name == "darwin":
        return MACOS
    if name == "linux" or not name:
        return LINUX
    return LINUX  # other unixes follow the Linux layout


def detect_wsl(version_text: Optional[str] = None, env: Optional[Mapping[str, str]] = None) -> bool:
    env = os.environ if env is None else env
    if env.get("WSL_DISTRO_NAME") or env.get("WSL_INTEROP"):
        return True
    try:
        text = version_text if version_text is not None else Path("/proc/version").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(re.search(r"(?i)microsoft|wsl", text))


# ----------------------------------------------------------------------------------------------------- mapping
@dataclass(frozen=True)
class Profile:
    worker: str
    os_name: str
    path: str            # the physical profile directory
    kind: str            # default (AG1) | account (AG2..AG8)
    number: int
    home_override: bool  # True when launching must set HOME to `path` (Linux/macOS account profiles)

    def to_dict(self) -> dict:
        return {"worker": self.worker, "os": self.os_name, "path": self.path, "kind": self.kind, "number": self.number,
                "sets_home": self.home_override}


def _worker_number(worker) -> int:
    raw = str(worker).strip().upper()
    raw = raw[2:] if raw.startswith("AG") else raw
    if not raw.isdigit():
        raise BridgeError(f"unknown worker {worker!r}; expected AG1..AG8")
    n = int(raw)
    if not 1 <= n <= len(fleet.FLEET):
        raise BridgeError(f"unknown worker {worker!r}; expected AG1..AG{len(fleet.FLEET)}")
    return n


def user_home(os_name: str, env: Optional[Mapping[str, str]] = None, home: Optional[str] = None) -> str:
    """The home directory the profile layout hangs off: explicit argument > PLANNER_AG_HOME > the OS's own variable > ~."""
    env = os.environ if env is None else env
    if home:
        return str(home)
    if env.get(ENV_HOME):
        return env[ENV_HOME]
    if os_name == WINDOWS:
        up = env.get("USERPROFILE")
        if up:
            return up
        drive, hp = env.get("HOMEDRIVE", ""), env.get("HOMEPATH", "")
        if drive and hp:
            return drive + hp
    elif env.get("HOME"):
        return env["HOME"]
    return str(Path.home())


def profile_for(worker, *, os_name: Optional[str] = None, env: Optional[Mapping[str, str]] = None, home: Optional[str] = None) -> Profile:
    """Physical profile directory of one worker. Pure: no disk access, any `os_name` can be evaluated anywhere."""
    n = _worker_number(worker)
    wid = f"AG{n}"
    os_name = os_name or detect_os()
    env = os.environ if env is None else env
    base = user_home(os_name, env, home)
    if os_name == WINDOWS:
        root = env.get(ENV_ROOT) or ntpath.join(base, "AppData", "Roaming")
        name = "Antigravity" if n == 1 else f"Antigravity{n}"
        return Profile(wid, os_name, ntpath.join(root, name), DEFAULT_KIND if n == 1 else ACCOUNT_KIND, n, False)
    if n == 1:
        return Profile(wid, os_name, base, DEFAULT_KIND, n, False)  # its config is <home>/.gemini/antigravity; HOME stays the real home
    root = env.get(ENV_ROOT) or posixpath.join(base, "AntigravityProfiles")
    return Profile(wid, os_name, posixpath.join(root, f"Account{n}"), ACCOUNT_KIND, n, True)


def profile_table(*, os_name: Optional[str] = None, env: Optional[Mapping[str, str]] = None, home: Optional[str] = None) -> Dict[str, Profile]:
    order = sorted(fleet.FLEET, key=lambda w: int(w[2:]))  # AG1..AG8, not the fleet's duty order
    return {w: profile_for(w, os_name=os_name, env=env, home=home) for w in order}


def default_config_dir(profile: Profile) -> Optional[str]:
    """Where AG1's Antigravity configuration lives on Linux/macOS (`~/.gemini/antigravity`); None elsewhere."""
    if profile.worker == "AG1" and profile.os_name != WINDOWS:
        return posixpath.join(profile.path, ".gemini", "antigravity")
    return None


def probe_profiles(table: Optional[Mapping[str, Profile]] = None) -> Dict[str, bool]:
    """Which profile directories exist on THIS machine (only meaningful for the current OS)."""
    table = table or profile_table()
    return {w: os.path.isdir(p.path) for w, p in table.items()}


def missing_profiles(workers: Optional[Iterable[str]] = None, table: Optional[Mapping[str, Profile]] = None) -> List[str]:
    table = table or profile_table()
    want = list(workers) if workers is not None else list(table)
    return [w for w in want if not os.path.isdir(table[w].path)]


# ----------------------------------------------------------------------------------------------------- environment
def profile_env(profile: Profile) -> Dict[str, str]:
    """The profile part of a task's environment."""
    env = {"PLANNER_PROFILE_DIR": profile.path, "PLANNER_PROFILE_KIND": profile.kind, "PLANNER_ACCOUNT": profile.worker}
    if profile.home_override:
        env["HOME"] = profile.path
    return env


def sanitized_env(worker, ticket: str = "", *, plan: Optional[fleet.PortPlan] = None, tier: Optional[str] = None,
                  standby_for: Optional[str] = None, os_name: Optional[str] = None, home: Optional[str] = None,
                  base_env: Optional[Mapping[str, str]] = None, pass_env: Sequence[str] = (), extra: Optional[Mapping[str, str]] = None,
                  profile: Optional[Profile] = None) -> Dict[str, str]:
    """Environment of one headless task.

    * the allow-listed base environment (no tokens, keys, cookies...), then
    * the MASP variables of the worker whose scope runs (`standby_for` when AG8 is the escalation solver; the ACCOUNT
      and therefore the profile stay the solver's), then
    * the profile location, then
    * variables the operator explicitly passes (`pass_env` names, `extra`).
    A name in `pass_env` that is not set is skipped, never invented."""
    src = os.environ if base_env is None else base_env
    prof = profile or profile_for(worker, os_name=os_name, env=src, home=home)
    runs_as = standby_for or str(worker)
    env: Dict[str, str] = dict(build_sandbox_env(base=src))
    env.update(fleet.worker_env(runs_as, ticket or "T-0", plan=plan, tier=tier))
    if standby_for:
        env["PLANNER_STANDBY"] = fleet.get(worker).id
    env.update(profile_env(prof))
    for name in pass_env:
        if name and name in src:
            env[name] = src[name]
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    missing = [k for k in REQUIRED_ENV if k not in env]
    if missing:
        raise BridgeError(f"{fleet.get(runs_as).id} has no ports; its environment lacks {', '.join(missing)}")
    return env


# ----------------------------------------------------------------------------------------------------- launching
@dataclass
class LaunchResult:
    ok: bool
    status: str           # ok | failed | timeout | error | no_profile
    returncode: Optional[int]
    stdout: str
    stderr: str
    argv: List[str]
    profile: str
    duration: float = 0.0

    def to_dict(self) -> dict:
        return dict(self.__dict__)


_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand(template: Sequence[str], values: Mapping[str, str]) -> List[str]:
    """Substitute known {placeholders}; any other brace (JSON, shell, python -c) is left alone."""
    return [_PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), m.group(0))), part) for part in template]


def split_template(template) -> List[str]:
    import shlex
    return list(template) if not isinstance(template, str) else shlex.split(template, posix=os.name != "nt")


def launch_headless(worker, command, *, prompt: str, workspace, ticket: str = "", model: str = "", effort: str = "", timeout: float = 900.0,
                    standby_for: Optional[str] = None, tier: Optional[str] = None, pass_env: Sequence[str] = (), stdin_prompt: bool = False,
                    require_profile: bool = True, os_name: Optional[str] = None, home: Optional[str] = None,
                    base_env: Optional[Mapping[str, str]] = None, prompt_dir: Optional[str] = None) -> LaunchResult:
    """Run `command` (argv list or string with {prompt_file} {workspace} {model} {effort} {worker} {ticket} {profile_dir} {account})
    headless for `worker`, inside that account's profile, with the sanitized MASP environment."""
    ws = Path(workspace)
    prof = profile_for(worker, os_name=os_name, env=base_env, home=home)
    argv0 = split_template(command)
    if not argv0:
        raise BridgeError("empty command")
    if require_profile and not os.path.isdir(prof.path):
        return LaunchResult(False, "no_profile", None, "", f"profile directory for {prof.worker} does not exist: {prof.path} "
                            f"(log that account in on this machine, or move the layout with {ENV_ROOT}/{ENV_HOME})", argv0, prof.path)
    try:
        env = sanitized_env(worker, ticket, tier=tier, standby_for=standby_for, os_name=os_name, home=home, base_env=base_env,
                            pass_env=pass_env, profile=prof)
    except (BridgeError, fleet.FleetError) as exc:
        return LaunchResult(False, "error", None, "", str(exc), argv0, prof.path)
    pdir = Path(prompt_dir) if prompt_dir else ws / ".planner_prompts"
    pdir.mkdir(parents=True, exist_ok=True)
    pf = pdir / (re.sub(r"[^A-Za-z0-9_.\-]", "_", f"{prof.worker}-{ticket or 'task'}") + ".txt")
    pf.write_text(prompt, encoding="utf-8")
    values = {"prompt_file": str(pf), "workspace": str(ws), "model": model, "effort": effort, "worker": prof.worker, "ticket": ticket,
              "profile_dir": prof.path, "account": prof.worker}
    argv = expand(argv0, values)
    argv[0] = find_executable(argv[0]) or argv[0]
    res = run_process(argv, cwd=str(ws), env=env, timeout=timeout, input_text=prompt if stdin_prompt else None)
    if res.error:
        return LaunchResult(False, "error", res.returncode, res.stdout, res.stderr or res.error, argv, prof.path, res.duration)
    if res.timed_out:
        return LaunchResult(False, "timeout", res.returncode, res.stdout, (res.stderr + f"\ntimed out after {timeout:g}s; process tree killed").strip(), argv, prof.path, res.duration)
    return LaunchResult(res.returncode == 0, "ok" if res.returncode == 0 else "failed", res.returncode, res.stdout, res.stderr, argv, prof.path, res.duration)


# ----------------------------------------------------------------------------------------------------- swarm executor
def _executor_base():
    import swarm  # lazy: swarm imports this module only inside build_executor, so there is no import cycle
    return swarm


def _make_bridge_executor():
    sw = _executor_base()

    class BridgeExecutor(sw.CommandExecutor):
        """The swarm's `command` executor, bound to the account profiles: each task runs under ITS worker's profile
        (the escalation solver AG8 runs under AG8's own account but with the replaced worker's scope and ports)."""
        name = "antigravity"

        def __init__(self, template, *, pass_env: Iterable[str] = (), stdin_prompt: bool = False, require_profile: bool = True,
                     os_name: Optional[str] = None, home: Optional[str] = None, base_env: Optional[Mapping[str, str]] = None):
            super().__init__(template, pass_env=pass_env, stdin_prompt=stdin_prompt)
            self.require_profile = require_profile
            self.os_name, self.home, self.base_env = os_name, home, base_env

        def profile(self, task) -> Profile:
            return profile_for(task.worker, os_name=self.os_name, env=self.base_env, home=self.home)

        def _extra_fmt(self, task, ctx):
            p = self.profile(task)
            return {"profile_dir": p.path, "account": p.worker}

        def _environment(self, task, env):
            p = self.profile(task)
            out = dict(env)
            out.update(profile_env(p))
            src = os.environ if self.base_env is None else self.base_env
            for name in self.pass_env:
                if name in src:
                    out[name] = src[name]
            return out

        def run(self, task, env, timeout, ctx):
            p = self.profile(task)
            if self.require_profile and not os.path.isdir(p.path):
                return sw.ExecResult(sw.ERROR, None, "", f"profile directory for {p.worker} does not exist: {p.path} "
                                     f"(log that account in on this machine, or move the layout with {ENV_ROOT}/{ENV_HOME})")
            return super().run(task, env, timeout, ctx)

    return BridgeExecutor


def __getattr__(name: str):  # lazy attribute: `from core.antigravity_bridge import BridgeExecutor`
    if name == "BridgeExecutor":
        cls = _make_bridge_executor()
        globals()["BridgeExecutor"] = cls
        return cls
    raise AttributeError(name)


# ----------------------------------------------------------------------------------------------------- CLI
def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="antigravity_bridge", description="Map AG1..AG8 to their Antigravity account profiles and launch headless tasks")
    ap.add_argument("--os", choices=(WINDOWS, LINUX, MACOS), default=None, help="evaluate the layout for another OS (profiles/env only)")
    ap.add_argument("--home", default=None)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("profiles", help="print the worker -> profile table as JSON")
    sub.add_parser("check", help="exit 1 when a profile directory is missing on this machine")
    e = sub.add_parser("env", help="print the sanitized environment of one task (only PLANNER/MASP/profile variables)")
    e.add_argument("--worker", required=True)
    e.add_argument("--ticket", default="T-0")
    e.add_argument("--standby-for", default=None)
    e.add_argument("--tier", default=None)
    la = sub.add_parser("launch", help="run a headless command inside a worker's profile")
    la.add_argument("--worker", required=True)
    la.add_argument("--cmd", required=True)
    la.add_argument("--prompt", default="")
    la.add_argument("--prompt-file", default=None)
    la.add_argument("--workspace", default=".")
    la.add_argument("--ticket", default="")
    la.add_argument("--model", default="")
    la.add_argument("--effort", default="")
    la.add_argument("--timeout", type=float, default=900.0)
    la.add_argument("--standby-for", default=None)
    la.add_argument("--tier", default=None)
    la.add_argument("--pass-env", default="")
    la.add_argument("--stdin-prompt", action="store_true")
    ns = ap.parse_args(argv)
    if not ns.cmd:
        ap.print_help()
        return 0
    try:
        if ns.cmd == "profiles":
            table = profile_table(os_name=ns.os, home=ns.home)
            print(json.dumps({"os": ns.os or detect_os(), "wsl": detect_wsl(), "profiles": {w: p.to_dict() for w, p in table.items()}}, indent=2))
            return 0
        if ns.cmd == "check":
            table = profile_table(os_name=ns.os, home=ns.home)
            miss = missing_profiles(table=table)
            print(json.dumps({"missing": miss, "present": [w for w in table if w not in miss]}, indent=2))
            return 1 if miss else 0
        if ns.cmd == "env":
            env = sanitized_env(ns.worker, ns.ticket, tier=ns.tier, standby_for=ns.standby_for, os_name=ns.os, home=ns.home)
            keep = {k: v for k, v in env.items() if k.startswith(("PLANNER_", "MASP_")) or k in ("DEV_PORT", "API_PORT", "PORT", "HOME")}
            print(json.dumps(keep, indent=2, sort_keys=True))
            return 0
        prompt = Path(ns.prompt_file).read_text(encoding="utf-8") if ns.prompt_file else ns.prompt
        if not prompt.strip():
            print("antigravity_bridge: empty prompt (use --prompt or --prompt-file)", file=sys.stderr)
            return 2
        r = launch_headless(ns.worker, ns.cmd, prompt=prompt, workspace=ns.workspace, ticket=ns.ticket, model=ns.model, effort=ns.effort,
                            timeout=ns.timeout, standby_for=ns.standby_for, tier=ns.tier,
                            pass_env=[x.strip() for x in ns.pass_env.split(",") if x.strip()], stdin_prompt=ns.stdin_prompt,
                            os_name=ns.os, home=ns.home)
        print(json.dumps(r.to_dict(), indent=2))
        return 0 if r.ok else 1
    except (BridgeError, fleet.FleetError) as exc:
        print(f"antigravity_bridge: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

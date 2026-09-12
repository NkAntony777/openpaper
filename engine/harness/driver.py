#!/usr/bin/env python3
"""
ABOUTME: PiDriver — headless driver for the pi coding agent in RPC mode (design doc §5.2/§6).
ABOUTME: Spawns `pi --mode rpc`, streams events from a reader thread, enforces the budget
ABOUTME: (cost via get_session_stats / turns / wall clock) with steer → abort → terminate
ABOUTME: escalation, and appends a run journal (one line per event) for offline debugging.
"""

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_tools.registry import MODULE_BY_NAME
from harness.paper_map import write_paper_map

ENGINE_DIR = Path(__file__).parent.parent
REPO_DIR = ENGINE_DIR.parent
ASSETS_DIR = Path(__file__).parent / "assets"
EXTENSION_SOURCE = ASSETS_DIR / "opendraft-tools.ts"

DEFAULT_PI_BIN = r"E:\npm-global\pi.cmd"
DEFAULT_MODEL = "minimax-cn/MiniMax-M3"  # api.minimaxi.com/anthropic — where our key lives

BUILTIN_TOOL_ALLOWLIST = "read,write,edit,grep,find,ls"
TOOL_NAMES = sorted(MODULE_BY_NAME.keys())

ABORT_EXIT_GRACE_S = 15.0
FINALIZE_GRACE_S = 15.0

WRAP_UP_STEER_MESSAGE = (
    "BUDGET LIMIT REACHED. Stop exploring now: finish your current tool call, then write your "
    "final report as a plain text message with: the file path you wrote, the word count, the "
    "number of citations used, and the remaining score_draft issues. Do not start new tool calls."
)

_EOF = object()  # reader-thread sentinel: process stdout closed


class _Eof(Exception):
    """Raised by a line source when the process stream is exhausted."""


@dataclass
class BudgetConfig:
    max_cost_usd: float = 1.0
    max_turns: int = 40
    max_seconds: float = 900.0
    poll_interval_s: float = 5.0
    steer_grace_s: float = 60.0


@dataclass
class DriverResult:
    ok: bool
    reason: str
    stats: Dict = field(default_factory=dict)
    journal_path: Optional[str] = None
    settled_text: Optional[str] = None
    budget_exceeded: bool = False


@dataclass
class _LoopState:
    settled: bool = False
    budget_exceeded: bool = False
    budget_reason: str = ""
    reason: str = ""
    stats: Dict = field(default_factory=dict)
    usage: Dict = field(default_factory=dict)  # last message_update usage (cumulative)
    settled_text: Optional[str] = None
    turns: int = 0


class _Journal:
    """Append-only run journal: one JSON object per line."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.touch(exist_ok=True)

    def log(self, type_: str, summary: str) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "type": type_[:64],
            "summary": summary[:400],
        }
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")


def _load_engine_dotenv() -> None:
    """Load engine/.env then engine/.env.local — same pattern as engine/config.py."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_path = ENGINE_DIR / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    env_local_path = ENGINE_DIR / ".env.local"
    if env_local_path.exists():
        load_dotenv(env_local_path, override=True)


def _default_opendraft_bin() -> str:
    exe = REPO_DIR / ".venv" / "Scripts" / "opendraft.exe"
    if exe.exists():
        return str(exe)
    for candidate in ("opendraft.exe", "opendraft.cmd", "opendraft"):
        found = shutil.which(candidate)
        if found:
            return found
    return "opendraft"


def _compact(value, limit: int = 200) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return text[:limit]


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill the process tree. pi runs under a .cmd wrapper (or cmd.exe /c ...) on
    Windows — killing only the direct child orphans the node grandchild and its
    pipe handles (this hung the first smoke test)."""
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=10,
            )
            return
        except Exception:
            pass
    try:
        proc.kill()
    except Exception:
        pass


class PiDriver:
    """Drives one pi RPC session for a single task (e.g. writing one section)."""

    def __init__(
        self,
        root,
        model: Optional[str] = None,
        pi_bin: Optional[str] = None,
        budget: Optional[BudgetConfig] = None,
    ):
        self.root = Path(root)
        self.model = model or os.environ.get("PI_MODEL") or DEFAULT_MODEL
        self.pi_bin = pi_bin or os.environ.get("PI_BIN") or DEFAULT_PI_BIN
        self.budget = budget or BudgetConfig()

    # ------------------------------------------------------------------ setup

    def prepare(self) -> None:
        """Generate AGENTS.md (paper map) and install the pi extension (overwrite every run)."""
        write_paper_map(self.root)
        target = self.root / ".pi" / "extensions" / EXTENSION_SOURCE.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(EXTENSION_SOURCE, target)

    def _build_env(self) -> Dict[str, str]:
        _load_engine_dotenv()
        env = dict(os.environ)
        openai_key = env.get("OPENAI_API_KEY")
        if openai_key:
            env.setdefault("MINIMAX_API_KEY", openai_key)
            env.setdefault("MINIMAX_CN_API_KEY", openai_key)
        env["OPENDRAFT_BIN"] = env.get("OPENDRAFT_BIN") or _default_opendraft_bin()
        return env

    def _pi_argv(self, name: str) -> List[str]:
        tools = ",".join([BUILTIN_TOOL_ALLOWLIST, *TOOL_NAMES])
        return [
            str(self.pi_bin),
            "--mode", "rpc",
            "--model", self.model,
            "--session-dir", str(self.root / ".pi" / "sessions"),
            "--name", name,
            # Global/user extensions (e.g. subagent, autoresearch) can block the
            # session with extension_ui_request dialogs in headless runs. Discovery
            # off; load ONLY our extension by explicit path.
            "--no-extensions",
            "-e", str(self.root / ".pi" / "extensions" / "opendraft-tools.ts"),
            "--tools", tools,
            # Defense in depth: the PoC showed bash executing despite the allowlist
            # (pi --tools semantics don't hard-restrict the shell tool). Deny it too;
            # a writing agent has no business spawning shells.
            "--exclude-tools", "bash,powershell",
        ]

    def _spawn(self, argv: List[str], env: Dict[str, str]) -> subprocess.Popen:
        common = dict(
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=str(self.root),
            env=env,
        )
        try:
            return subprocess.Popen(argv, **common)
        except OSError:
            # Windows: CreateProcess cannot run bare .cmd/.batch files.
            if os.name == "nt" and str(argv[0]).lower().endswith((".cmd", ".bat")):
                return subprocess.Popen(["cmd.exe", "/d", "/c", *argv], **common)
            raise

    # ------------------------------------------------------------------- run

    def run(self, prompt: str, name: str) -> DriverResult:
        """Run one task to settled/budget-abort. Never raises — failures come back
        as DriverResult."""
        journal_path = None
        state = _LoopState(reason="unknown")
        proc: Optional[subprocess.Popen] = None
        try:
            self.prepare()
            env = self._build_env()
            (self.root / ".pi" / "sessions").mkdir(parents=True, exist_ok=True)

            journal = _Journal(self.root / "run_journal.jsonl")
            journal_path = str(journal.path)
            argv = self._pi_argv(name)
            proc = self._spawn(argv, env)
            journal.log(
                "prompt",
                f"session={name} model={self.model} pi={self.pi_bin} "
                f"budget(cost={self.budget.max_cost_usd} turns={self.budget.max_turns} "
                f"seconds={self.budget.max_seconds})",
            )

            lines: "queue.Queue" = queue.Queue()
            stderr_tail: deque = deque(maxlen=64)

            def _reader() -> None:
                try:
                    for raw in proc.stdout:
                        lines.put(raw.rstrip("\n").rstrip("\r"))
                except Exception:
                    pass
                finally:
                    lines.put(_EOF)

            def _stderr_drain() -> None:
                try:
                    for chunk in proc.stderr:
                        stderr_tail.append(chunk.rstrip("\n"))
                except Exception:
                    pass

            threading.Thread(target=_reader, daemon=True).start()
            threading.Thread(target=_stderr_drain, daemon=True).start()

            def next_line(timeout: float) -> Optional[str]:
                item = lines.get(timeout=timeout)
                if item is _EOF:
                    raise _Eof()
                return item

            def send(cmd: Dict) -> None:
                proc.stdin.write(json.dumps(cmd, ensure_ascii=False) + "\n")
                proc.stdin.flush()

            def on_terminate() -> None:
                try:
                    proc.terminate()
                except Exception:
                    pass

            def eof_note() -> str:
                rc = proc.poll()
                tail_text = " | ".join(stderr_tail)[-300:]
                return f" (exit={rc}{'; stderr: ' + tail_text if tail_text else ''})"

            send({"id": "od-prompt-1", "type": "prompt", "message": prompt})
            state = self._event_loop(
                next_line,
                send,
                journal,
                on_terminate=on_terminate,
                eof_note=eof_note(),
            )
            if not state.reason:
                state.reason = "unknown"
        except Exception as e:
            state.reason = f"{type(e).__name__}: {e}"
        finally:
            if proc is not None:
                try:
                    if proc.stdin:
                        proc.stdin.close()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=10)
                except Exception:
                    _kill_process_tree(proc)
                    try:
                        proc.wait(timeout=5)
                    except Exception:
                        pass

        ok = state.settled
        stats = dict(state.stats)
        if not stats and state.usage:
            stats["usage"] = state.usage
        stats["turns"] = state.turns
        return DriverResult(
            ok=ok,
            reason=state.reason,
            stats=stats,
            journal_path=journal_path,
            settled_text=state.settled_text,
            budget_exceeded=state.budget_exceeded,
        )

    # -------------------------------------------------------------- event loop

    def _event_loop(
        self,
        next_line: Callable[[float], Optional[str]],
        send: Callable[[Dict], None],
        journal: _Journal,
        on_terminate: Optional[Callable[[], None]] = None,
        clock: Optional[Callable[[], float]] = None,
        eof_note: str = "",
    ) -> _LoopState:
        """Consume pi RPC events until the agent settles or the budget escalates to termination.

        `next_line(timeout)` returns a JSON line, None on timeout, and raises _Eof when the
        stream is closed. `send(cmd)` writes one RPC command. Both are injectable so tests can
        drive the loop with canned JSONL (no real pi process).
        """
        clock = clock or time.monotonic
        budget = self.budget
        state = _LoopState()
        start = clock()

        req_seq = 0
        stats_polled_at = 0.0
        pending_stats: Dict[str, float] = {}
        steer_at: Optional[float] = None
        aborted_at: Optional[float] = None
        finalize_deadline: Optional[float] = None
        finalize_stats_id: Optional[str] = None
        finalize_text_id: Optional[str] = None
        got_final_stats = False
        got_final_text = False
        terminated = False

        def terminate() -> None:
            nonlocal terminated
            if terminated:
                return
            terminated = True
            if on_terminate is not None:
                try:
                    on_terminate()
                except Exception:
                    pass

        def steer(now: float, why: str) -> None:
            nonlocal steer_at
            if steer_at is not None:
                return
            steer_at = now
            state.budget_exceeded = True
            state.budget_reason = why
            journal.log("budget", f"exceeded ({why}); steering wrap-up message")
            send({"type": "steer", "message": WRAP_UP_STEER_MESSAGE})

        def request_stats(now: float) -> None:
            nonlocal req_seq, stats_polled_at
            req_seq += 1
            rid = f"od-stats-{req_seq}"
            pending_stats[rid] = now
            stats_polled_at = now
            send({"type": "get_session_stats", "id": rid})

        def request_text() -> str:
            nonlocal req_seq
            req_seq += 1
            rid = f"od-text-{req_seq}"
            send({"type": "get_last_assistant_text", "id": rid})
            return rid

        while True:
            now = clock()

            # Escalation step 3: post-abort grace expired → hard terminate.
            if aborted_at is not None and now - aborted_at > ABORT_EXIT_GRACE_S:
                terminate()
                state.reason = f"terminated after abort grace ({state.budget_reason})"
                break

            # Escalation step 2: steer sent but nothing settled within the grace → abort.
            grace_expired = (
                steer_at is not None
                and aborted_at is None
                and now - steer_at > budget.steer_grace_s
            )
            if grace_expired:
                aborted_at = now
                journal.log(
                    "budget",
                    f"no agent_settled within {budget.steer_grace_s}s of steer; aborting",
                )
                send({"type": "abort"})
                finalize_text_id = request_text()  # salvage whatever the model last wrote

            if state.settled:
                # Finalize: collect session stats + last assistant text (bounded wait).
                if (got_final_stats and got_final_text) or now > finalize_deadline:
                    state.reason = "settled" + (
                        f" (after budget steer: {state.budget_reason})"
                        if state.budget_exceeded
                        else ""
                    )
                    break
            elif steer_at is None:
                # Budget checks (checked on every event and every timeout tick).
                if now - start > budget.max_seconds:
                    steer(now, f"seconds>{budget.max_seconds:g}")
                elif state.turns > budget.max_turns:
                    steer(now, f"turns>{budget.max_turns}")
                elif not pending_stats and now - stats_polled_at >= budget.poll_interval_s:
                    request_stats(now)

            # Blocking budget guard: wake at least every poll_interval even when pi is silent.
            try:
                line = next_line(budget.poll_interval_s)
            except _Eof:
                if state.settled:
                    state.reason = "settled (stream closed)"
                else:
                    state.reason = f"pi stream closed before agent_settled{eof_note}"
                break
            if line is None:
                continue
            if not line.strip():
                continue

            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                journal.log("unparsed_line", line[:200])
                continue
            if not isinstance(ev, dict):
                journal.log("unparsed_line", line[:200])
                continue

            etype = ev.get("type")

            if etype == "response":
                rid = ev.get("id")
                if rid in pending_stats:
                    pending_stats.pop(rid, None)
                    if ev.get("success"):
                        data = ev.get("data") or {}
                        state.stats = data
                        cost = data.get("cost")
                        tokens = data.get("tokens") or {}
                        journal.log(
                            "session_stats",
                            f"cost=${cost} tokens={_compact(tokens, 120)}",
                        )
                        if rid == finalize_stats_id:
                            got_final_stats = True
                        if (
                            isinstance(cost, (int, float))
                            and not isinstance(cost, bool)
                            and cost > budget.max_cost_usd
                            and steer_at is None
                        ):
                            steer(clock(), f"cost>{budget.max_cost_usd}")
                    else:
                        journal.log("session_stats", f"request failed: {ev.get('error')}")
                elif rid is not None and rid == finalize_text_id:
                    if ev.get("success"):
                        state.settled_text = (ev.get("data") or {}).get("text")
                    got_final_text = True
                continue

            if etype == "tool_execution_start":
                journal.log(etype, f"{ev.get('toolName')} {_compact(ev.get('args'))}")
            elif etype == "tool_execution_end":
                journal.log(
                    etype,
                    f"{ev.get('toolName')} {'ERROR' if ev.get('isError') else 'ok'}",
                )
            elif etype == "turn_end":
                state.turns += 1
                journal.log("turn", f"turn {state.turns} completed")
            elif etype == "message_update":
                usage = ev.get("usage")
                if isinstance(usage, dict):
                    state.usage = usage
            elif etype == "agent_settled":
                state.settled = True
                finalize_deadline = clock() + FINALIZE_GRACE_S
                journal.log("settled", "agent_settled")
                finalize_stats_id = f"od-stats-{req_seq + 1}"
                req_seq += 1
                pending_stats[finalize_stats_id] = clock()
                send({"type": "get_session_stats", "id": finalize_stats_id})
                finalize_text_id = request_text()
            elif etype == "extension_error":
                journal.log(etype, f"{ev.get('event')}: {str(ev.get('error'))[:200]}")
            elif etype == "extension_ui_request":
                # Unattended policy (design §8): never block on extension UI dialogs.
                # Dialog methods get an immediate cancel; fire-and-forget pass through.
                method = ev.get("method")
                if method in ("confirm", "select", "input", "editor"):
                    send({"type": "extension_ui_response", "id": ev.get("id"), "cancelled": True})
                    journal.log(
                        etype,
                        f"{method} auto-cancelled: "
                        f"{str(ev.get('title') or ev.get('message') or '')[:120]}",
                    )
                else:
                    journal.log(etype, f"{method} (fire-and-forget) ignored")
            elif etype in ("auto_retry_start", "auto_retry_end"):
                if etype == "auto_retry_start":
                    detail = ev.get("errorMessage")
                else:
                    detail = ev.get("success")
                journal.log(etype, str(detail)[:200])
            # All other events (message_*, agent_start/agent_end, compaction_*,
            # queue_update, ...) are consumed without journaling.

        return state

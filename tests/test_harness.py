#!/usr/bin/env python3
"""Offline tests for the pi agent harness (engine/harness/).

Canned-JSONL driven: no network, no real pi subprocess. The driver's event loop is fed by
injected line sources + senders (see FakeFeed), which is exactly the dependency seam the
loop was designed around (design doc §11 "loop 测试（mock）").
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "engine"))

import harness.driver as driver_mod
from agent_tools.common import (
    FULL_LEDGER_KEY,
    SECTION_FILES,
    update_section_status,
    write_checkpoint,
)
from harness.driver import BudgetConfig, DriverResult, PiDriver, _Eof, _Journal
from harness.paper_map import write_paper_map
from harness.paper_task import PaperBudget, PaperResult, parse_global_issues, run_paper
from harness.review_task import NO_ISSUES_TEXT
from harness.section_task import build_section_prompt


# --------------------------------------------------------------------------- fakes


class FakeClock:
    """Deterministic monotonic clock: only advances when the loop waits on a timeout."""

    def __init__(self, start=1000.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeFeed:
    """Line source + sender pair for PiDriver._event_loop.

    scripted lines are yielded first; a send() of get_session_stats / get_last_assistant_text
    appends the canned RPC response to the script, which exercises request/response id
    correlation through the real loop code.
    """

    def __init__(self, events=(), clock=None, stats_costs=None, stats_errors=()):
        self.script = [json.dumps(e) if not isinstance(e, str) else e for e in events]
        self.clock = clock or FakeClock()
        self.stats_costs = stats_costs or {}
        self.stats_errors = set(stats_errors)
        self.stats_requests = 0
        self.sent = []
        self.terminated = False

    def send(self, cmd):
        self.sent.append(cmd)
        cmd_type = cmd.get("type")
        if cmd_type == "get_session_stats":
            self.stats_requests += 1
            n = self.stats_requests
            if n in self.stats_errors:
                self.script.append(json.dumps({
                    "type": "response", "id": cmd["id"], "command": "get_session_stats",
                    "success": False, "error": "stats unavailable",
                }))
            else:
                cost = self.stats_costs.get(n, 0.01)
                self.script.append(json.dumps({
                    "type": "response", "id": cmd["id"], "command": "get_session_stats",
                    "success": True,
                    "data": {"cost": cost, "tokens": {"input": 10, "output": 5, "total": 15}},
                }))
        elif cmd_type == "get_last_assistant_text":
            self.script.append(json.dumps({
                "type": "response", "id": cmd["id"], "command": "get_last_assistant_text",
                "success": True, "data": {"text": "FAKE FINAL TEXT"},
            }))

    def next_line(self, timeout):
        if self.script:
            return self.script.pop(0)
        self.clock.advance(timeout)  # simulate the wall clock while pi is silent
        return None

    def on_terminate(self):
        self.terminated = True

    def commands_of(self, cmd_type):
        return [c for c in self.sent if c.get("type") == cmd_type]


def journal_types(path):
    types = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            types.append(json.loads(line)["type"])
    return types


@pytest.fixture
def journal(tmp_path):
    return _Journal(tmp_path / "run_journal.jsonl")


# ---------------------------------------------------------------------- paper map


def test_paper_map_empty_dir(tmp_path):
    out = write_paper_map(tmp_path)
    assert out == tmp_path / "AGENTS.md"
    text = out.read_text(encoding="utf-8")
    assert "(topic unknown" in text
    assert "(no outline yet)" in text
    assert "Citations in database: 0" in text
    for section in SECTION_FILES:
        assert section in text
    assert "pending" in text


def test_paper_map_full_fixture(tmp_path):
    write_checkpoint(tmp_path, {
        "topic": "AI in education",
        "academic_level": "master",
        "citation_style": "apa",
        "language": "en",
        "completed_phase": "structure",
        "word_targets": {"literature_review": "2000-2500", "methodology": 1500},
    })
    (tmp_path / "drafts").mkdir()
    (tmp_path / "drafts" / "00_formatted_outline.md").write_text(
        "# Outline\n1. Intro\n2. Lit", "utf-8")
    (tmp_path / "drafts" / "02_1_literature_review.md").write_text("some words here", "utf-8")
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "bibliography.json").write_text(json.dumps({
        "citations": [{"id": "cite_001", "year": 2019}, {"id": "cite_002", "year": 2021}],
    }), "utf-8")

    write_paper_map(tmp_path)
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "AI in education" in text
    assert "2000-2500" in text  # raw word-target spec shown
    assert "| 1500 |" in text
    assert "Citations in database: 2" in text
    assert "2019-2021" in text
    assert "| literature_review |" in text and "written" in text
    assert "| methodology |" in text and "pending" in text
    assert "# Outline" in text  # outline content embedded


# ------------------------------------------------------------------- driver: loop


def test_event_loop_settled_path(tmp_path, journal):
    clock = FakeClock()
    feed = FakeFeed(
        events=[
            {"type": "agent_start"},
            {"type": "turn_start"},
            {"type": "message_update",
             "usage": {"input": 5, "output": 2, "cost": {"total": 0.01}},
             "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "hi"}},
            {"type": "turn_end", "message": {}, "toolResults": []},
            {"type": "tool_execution_start", "toolCallId": "c1", "toolName": "write_section",
             "args": {"section": "literature_review", "content": "x" * 300}},
            {"type": "tool_execution_end", "toolCallId": "c1", "toolName": "write_section",
             "result": {"content": [{"type": "text", "text": "ok"}]}, "isError": False},
            {"type": "agent_settled"},
        ],
        clock=clock,
        stats_costs={1: 0.02, 2: 0.03},
    )
    driver = PiDriver(tmp_path)
    state = driver._event_loop(
        feed.next_line, feed.send, journal,
        on_terminate=feed.on_terminate, clock=clock,
    )

    assert state.settled is True
    assert state.reason == "settled"
    assert state.budget_exceeded is False
    assert state.settled_text == "FAKE FINAL TEXT"
    assert state.stats["cost"] == 0.03  # final stats response correlated by id
    assert state.turns == 1
    assert feed.terminated is False
    assert feed.commands_of("steer") == [] and feed.commands_of("abort") == []
    # two stats requests: the budget poll and the post-settle finalize
    assert len(feed.commands_of("get_session_stats")) == 2
    assert all(c.get("id") for c in feed.commands_of("get_session_stats"))

    types = journal_types(journal.path)
    assert "turn" in types and "settled" in types and "session_stats" in types
    assert "tool_execution_start" in types and "tool_execution_end" in types
    assert "budget" not in types


def test_event_loop_cost_budget_steer_abort(tmp_path, journal):
    clock = FakeClock()
    feed = FakeFeed(clock=clock, stats_costs={1: 0.50})
    driver = PiDriver(tmp_path, budget=BudgetConfig(
        max_cost_usd=0.10, max_turns=40, max_seconds=900, poll_interval_s=5, steer_grace_s=60,
    ))
    state = driver._event_loop(
        feed.next_line, feed.send, journal,
        on_terminate=feed.on_terminate, clock=clock,
    )

    assert state.settled is False
    assert state.budget_exceeded is True
    assert "cost>0.1" in state.budget_reason
    assert "terminated after abort grace" in state.reason
    assert feed.terminated is True
    assert len(feed.commands_of("steer")) == 1
    assert len(feed.commands_of("abort")) == 1
    # steer is only sent once even though the loop keeps ticking
    assert "budget" in journal_types(journal.path)


def test_event_loop_turns_budget(tmp_path, journal):
    clock = FakeClock()
    feed = FakeFeed(
        events=[{"type": "turn_end", "message": {}, "toolResults": []} for _ in range(3)],
        clock=clock,
    )
    driver = PiDriver(tmp_path, budget=BudgetConfig(
        max_cost_usd=10.0, max_turns=2, max_seconds=900, poll_interval_s=5, steer_grace_s=30,
    ))
    state = driver._event_loop(
        feed.next_line, feed.send, journal,
        on_terminate=feed.on_terminate, clock=clock,
    )

    assert state.budget_exceeded is True
    assert state.budget_reason == "turns>2"
    assert state.turns == 3
    assert len(feed.commands_of("steer")) == 1
    assert len(feed.commands_of("abort")) == 1


def test_event_loop_seconds_budget(tmp_path, journal):
    clock = FakeClock()
    feed = FakeFeed(clock=clock)  # silent pi: every read is a timeout tick
    driver = PiDriver(tmp_path, budget=BudgetConfig(
        max_cost_usd=10.0, max_turns=100, max_seconds=20, poll_interval_s=5, steer_grace_s=30,
    ))
    state = driver._event_loop(
        feed.next_line, feed.send, journal,
        on_terminate=feed.on_terminate, clock=clock,
    )

    assert state.budget_exceeded is True
    assert state.budget_reason == "seconds>20"
    assert len(feed.commands_of("steer")) == 1
    assert len(feed.commands_of("abort")) == 1


def test_event_loop_non_json_and_unknown_events_tolerated(tmp_path, journal):
    clock = FakeClock()
    feed = FakeFeed(
        events=[
            "this is not json",
            "",
            '{"type": "turn_start"',  # broken JSON
            {"type": "weird_future_event", "payload": 1},
            {"type": "turn_end", "message": {}, "toolResults": []},
            {"type": "agent_settled"},
        ],
        clock=clock,
    )
    driver = PiDriver(tmp_path)
    state = driver._event_loop(feed.next_line, feed.send, journal, clock=clock)

    assert state.settled is True
    types = journal_types(journal.path)
    assert types.count("unparsed_line") == 2  # the two bad lines, empty line skipped
    assert "settled" in types


def test_event_loop_extension_and_tool_errors_do_not_break(tmp_path, journal):
    clock = FakeClock()
    feed = FakeFeed(
        events=[
            {"type": "extension_error", "extensionPath": "/x.ts", "event": "tool_call",
             "error": "boom"},
            {"type": "tool_execution_start", "toolCallId": "c9", "toolName": "search_literature",
             "args": {"query": "ai in education"}},
            {"type": "tool_execution_end", "toolCallId": "c9", "toolName": "search_literature",
             "result": {"content": [{"type": "text", "text": "err"}]}, "isError": True},
            {"type": "agent_settled"},
        ],
        clock=clock,
    )
    driver = PiDriver(tmp_path)
    state = driver._event_loop(feed.next_line, feed.send, journal, clock=clock)

    assert state.settled is True
    types = journal_types(journal.path)
    assert "extension_error" in types
    assert "tool_execution_end" in types


def test_event_loop_stats_response_failure_tolerated(tmp_path, journal):
    clock = FakeClock()
    feed = FakeFeed(
        events=[{"type": "turn_end", "message": {}, "toolResults": []},
                {"type": "agent_settled"}],
        clock=clock,
        stats_errors={1},  # first (poll) stats request fails
    )
    driver = PiDriver(tmp_path)
    state = driver._event_loop(feed.next_line, feed.send, journal, clock=clock)

    assert state.settled is True  # loop survives a failed stats request
    assert "request failed" in Path(journal.path).read_text(encoding="utf-8")


def test_event_loop_steer_then_settle_is_ok_but_flagged(tmp_path, journal):
    clock = FakeClock()
    feed = FakeFeed(
        events=[{"type": "turn_end", "message": {}, "toolResults": []},
                {"type": "agent_settled"}],
        clock=clock,
    )
    driver = PiDriver(tmp_path, budget=BudgetConfig(
        max_cost_usd=10.0, max_turns=0, max_seconds=900, poll_interval_s=5, steer_grace_s=60,
    ))
    state = driver._event_loop(
        feed.next_line, feed.send, journal,
        on_terminate=feed.on_terminate, clock=clock,
    )

    assert state.settled is True
    assert state.budget_exceeded is True
    assert "after budget steer" in state.reason
    assert feed.commands_of("abort") == []  # settled before the grace expired
    assert state.settled_text == "FAKE FINAL TEXT"


def test_event_loop_eof_before_settled(tmp_path, journal):
    def eof_line(timeout):
        raise _Eof()

    driver = PiDriver(tmp_path)
    state = driver._event_loop(eof_line, lambda cmd: None, journal)

    assert state.settled is False
    assert "stream closed before agent_settled" in state.reason


# ------------------------------------------------------------------ driver: run()


def test_run_full_wiring_with_fake_proc(tmp_path, monkeypatch):
    """run() with a fake proc: exercises the real reader thread, queue, send() and the
    prompt command — the seam that canned loop tests bypass."""
    import io
    import threading

    scripted_events = [
        {"type": "agent_start"},
        {"type": "turn_start"},
        {"type": "turn_end", "message": {}, "toolResults": []},
        {"type": "agent_settled"},
    ]

    class FakeStdout:
        def __init__(self):
            self._items = []
            self._cv = threading.Condition()

        def feed(self, line):
            with self._cv:
                self._items.append(line)
                self._cv.notify()

        def __iter__(self):
            return self

        def __next__(self):
            with self._cv:
                while not self._items:
                    self._cv.wait()
                return self._items.pop(0)

    class FakeStdin:
        def __init__(self, stdout):
            self._stdout = stdout
            self._buf = ""
            self.closed = False

        def write(self, s):
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                self._dispatch(line)

        def _dispatch(self, line):
            cmd = json.loads(line)
            if cmd.get("type") == "prompt":
                for ev in scripted_events:
                    self._stdout.feed(json.dumps(ev) + "\n")
            elif cmd.get("type") == "get_session_stats":
                self._stdout.feed(json.dumps({
                    "type": "response", "id": cmd["id"], "command": "get_session_stats",
                    "success": True,
                    "data": {"cost": 0.07, "tokens": {"input": 3, "output": 2, "total": 5}},
                }) + "\n")
            elif cmd.get("type") == "get_last_assistant_text":
                self._stdout.feed(json.dumps({
                    "type": "response", "id": cmd["id"], "command": "get_last_assistant_text",
                    "success": True, "data": {"text": "FAKE FINAL TEXT"},
                }) + "\n")

        def flush(self):
            pass

        def close(self):
            self.closed = True

    class FakeProc:
        def __init__(self):
            self.stdout = FakeStdout()
            self.stdin = FakeStdin(self.stdout)
            self.stderr = iter([])
            self.terminated = False

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    fake = FakeProc()
    monkeypatch.setattr(driver_mod.PiDriver, "_spawn", lambda self, argv, env: fake)

    driver = PiDriver(tmp_path, pi_bin="fake-pi")
    result = driver.run("write the literature review", name="test-wiring")

    assert result.ok is True
    assert result.reason == "settled"
    assert result.budget_exceeded is False
    assert result.settled_text == "FAKE FINAL TEXT"
    assert result.stats["cost"] == 0.07
    assert fake.stdin.closed is True
    assert fake.terminated is False  # clean settle: no terminate needed
    assert (tmp_path / ".pi" / "extensions" / "opendraft-tools.ts").exists()
    assert (tmp_path / "AGENTS.md").exists()
    types = journal_types(result.journal_path)
    assert "prompt" in types and "settled" in types and "turn" in types


def test_run_spawn_failure_returns_result(tmp_path, monkeypatch):
    driver = PiDriver(tmp_path, pi_bin="pi-that-does-not-exist")

    def _boom(argv, env):
        raise FileNotFoundError("no such pi")

    monkeypatch.setattr(driver, "_spawn", _boom)
    result = driver.run("write the intro", name="test-section")

    assert isinstance(result, DriverResult)
    assert result.ok is False
    assert "FileNotFoundError" in result.reason
    assert result.budget_exceeded is False
    # prepare() still ran: paper map + extension installed, journal file created
    assert (tmp_path / "AGENTS.md").exists()
    assert (tmp_path / ".pi" / "extensions" / "opendraft-tools.ts").exists()
    assert result.journal_path and Path(result.journal_path).exists()


def test_prepare_installs_extension_and_paper_map(tmp_path):
    driver = PiDriver(tmp_path)
    driver.prepare()

    ext = tmp_path / ".pi" / "extensions" / "opendraft-tools.ts"
    assert ext.exists()
    source = Path(driver_mod.EXTENSION_SOURCE)
    assert ext.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert (tmp_path / "AGENTS.md").exists()

    # every prepare() overwrites — stale edits cannot survive
    ext.write_text("// stale", encoding="utf-8")
    driver.prepare()
    assert ext.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")


def test_driver_defaults_and_env_overrides(tmp_path, monkeypatch):
    monkeypatch.delenv("PI_MODEL", raising=False)
    monkeypatch.delenv("PI_BIN", raising=False)
    driver = PiDriver(tmp_path)
    assert driver.model == "minimax-cn/MiniMax-M3"
    assert driver.pi_bin == driver_mod.DEFAULT_PI_BIN

    monkeypatch.setenv("PI_MODEL", "minimax/other")
    monkeypatch.setenv("PI_BIN", "C:/custom/pi.cmd")
    driver = PiDriver(tmp_path)
    assert driver.model == "minimax/other"
    assert driver.pi_bin == "C:/custom/pi.cmd"

    # explicit constructor args beat env
    driver = PiDriver(tmp_path, model="m1", pi_bin="p1")
    assert driver.model == "m1" and driver.pi_bin == "p1"


def test_build_env_maps_openai_key_to_minimax(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_CN_API_KEY", raising=False)
    monkeypatch.delenv("OPENDRAFT_BIN", raising=False)
    monkeypatch.setattr(driver_mod, "_load_engine_dotenv", lambda: None)
    driver = PiDriver(tmp_path)
    env = driver._build_env()
    assert env["MINIMAX_API_KEY"] == "sk-test-123"
    assert env["MINIMAX_CN_API_KEY"] == "sk-test-123"
    assert env["OPENDRAFT_BIN"]


# --------------------------------------------------------------- section prompt


def _fixture_root(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    write_checkpoint(root, {
        "topic": "AI in education",
        "academic_level": "master",
        "citation_style": "apa",
        "language": "en",
        "word_targets": {"literature_review": "2000-2500"},
    })
    (root / "drafts").mkdir()
    (root / "drafts" / "00_formatted_outline.md").write_text("# Outline", "utf-8")
    (root / "research").mkdir()
    (root / "research" / "papers").mkdir()
    (root / "research" / "combined_research.md").write_text("notes", "utf-8")
    (root / "research" / "bibliography.json").write_text('{"citations": []}', "utf-8")
    return root


def test_build_section_prompt(tmp_path):
    root = _fixture_root(tmp_path)
    prompt = build_section_prompt(root, "literature_review")

    assert "literature_review" in prompt
    assert "AI in education" in prompt
    assert "2500" in prompt  # parsed max of the "2000-2500" target
    assert "drafts/02_1_literature_review.md" in prompt
    for tool in ("read_artifact", "write_section", "score_draft", "search_literature",
                 "verify_claims", "revise_section", "manage_claims", "write_outline"):
        assert tool in prompt
    assert "AGENTS.md" in prompt
    assert "cite_XXX" in prompt


def test_build_section_prompt_unknown_section(tmp_path):
    root = _fixture_root(tmp_path)
    with pytest.raises(ValueError):
        build_section_prompt(root, "not_a_section")


# ------------------------------------------------------------------------ CLI


def test_cli_harness_missing_section_usage_error(tmp_path, monkeypatch, capsys):
    class _Boom:
        def __init__(self, *args, **kwargs):
            raise AssertionError("PiDriver must not be constructed on usage errors")

    monkeypatch.setattr(driver_mod, "PiDriver", _Boom)
    from opendraft.cli import run_harness_command

    with pytest.raises(SystemExit) as exc:
        run_harness_command(["section", "--root", str(tmp_path)])
    assert exc.value.code == 2


def test_cli_harness_unknown_section_rejected(tmp_path, monkeypatch, capsys):
    class _Boom:
        def __init__(self, *args, **kwargs):
            raise AssertionError("PiDriver must not be constructed for bad sections")

    monkeypatch.setattr(driver_mod, "PiDriver", _Boom)
    from opendraft.cli import run_harness_command

    rc = run_harness_command(["section", "--root", str(tmp_path), "--section", "nope"])
    assert rc == 2


def test_cli_harness_success_envelope_on_stdout(tmp_path, monkeypatch, capsys):
    calls = {}

    class FakeDriver:
        def __init__(self, root, model=None, pi_bin=None, budget=None):
            calls["root"] = Path(root)
            calls["model"] = model
            calls["budget"] = budget
            self.root = Path(root)
            self.model = model
            self.pi_bin = pi_bin or "fake-pi"

        def prepare(self):
            calls["prepared"] = True

        def run(self, prompt, name):
            calls["prompt"] = prompt
            calls["name"] = name
            return DriverResult(
                ok=True,
                reason="settled",
                stats={"cost": 0.01, "turns": 3},
                journal_path=str(tmp_path / "run_journal.jsonl"),
                settled_text="report paragraph",
                budget_exceeded=False,
            )

    monkeypatch.setattr(driver_mod, "PiDriver", FakeDriver)
    from opendraft.cli import run_harness_command

    rc = run_harness_command([
        "section", "--root", str(tmp_path), "--section", "literature_review",
        "--max-cost", "0.5", "--max-turns", "7",
    ])
    assert rc == 0

    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.strip().splitlines() if ln.strip()]
    payload = json.loads(lines[-1])  # stdout: exactly one envelope-style JSON line
    assert payload["ok"] is True
    assert payload["data"]["reason"] == "settled"
    assert payload["data"]["settled_text"] == "report paragraph"
    assert payload["data"]["budget_exceeded"] is False

    assert "[harness]" in captured.err  # human progress on stderr, not stdout
    assert calls["prepared"] is True
    assert calls["name"] == "section-literature_review"
    assert "literature_review" in calls["prompt"]
    assert calls["budget"].max_cost_usd == 0.5
    assert calls["budget"].max_turns == 7


def test_cli_harness_failure_envelope_on_stdout(tmp_path, monkeypatch, capsys):
    class FakeDriver:
        def __init__(self, root, model=None, pi_bin=None, budget=None):
            self.root = Path(root)
            self.model = model
            self.pi_bin = pi_bin or "fake-pi"

        def prepare(self):
            pass

        def run(self, prompt, name):
            return DriverResult(
                ok=False,
                reason="terminated after abort grace (cost>0.5)",
                stats={},
                journal_path=str(tmp_path / "run_journal.jsonl"),
                settled_text=None,
                budget_exceeded=True,
            )

    monkeypatch.setattr(driver_mod, "PiDriver", FakeDriver)
    from opendraft.cli import run_harness_command

    rc = run_harness_command([
        "section", "--root", str(tmp_path), "--section", "methodology",
    ])
    assert rc == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert payload["data"]["budget_exceeded"] is True


# ------------------------------------------------- headless robustness (M1 fixes)


def test_event_loop_ui_request_dialog_auto_cancelled(tmp_path, journal):
    """extension_ui_request dialogs must never block an unattended run (design §8)."""
    clock = FakeClock()
    feed = FakeFeed(events=[
        {"type": "extension_ui_request", "id": "uuid-2", "method": "confirm",
         "title": "Clear session?", "timeout": 5000},
        {"type": "agent_settled"},
    ], clock=clock)
    driver = PiDriver(tmp_path)
    state = driver._event_loop(
        feed.next_line, feed.send, journal,
        on_terminate=feed.on_terminate, clock=clock,
    )
    responses = feed.commands_of("extension_ui_response")
    assert len(responses) == 1
    assert responses[0]["id"] == "uuid-2"
    assert responses[0]["cancelled"] is True
    assert state.settled is True


def test_event_loop_ui_request_fire_and_forget_no_response(tmp_path, journal):
    """notify/setStatus style requests expect no response — driver must not answer them."""
    clock = FakeClock()
    feed = FakeFeed(events=[
        {"type": "extension_ui_request", "id": "n1", "method": "notify",
         "message": "hello"},
        {"type": "agent_settled"},
    ], clock=clock)
    driver = PiDriver(tmp_path)
    driver._event_loop(
        feed.next_line, feed.send, journal,
        on_terminate=feed.on_terminate, clock=clock,
    )
    assert feed.commands_of("extension_ui_response") == []


def test_pi_argv_runs_extension_isolated(tmp_path):
    """Global/user extensions caused the first smoke-test hang (extension_ui_request
    block). The driver must disable discovery and load only our extension."""
    driver = PiDriver(tmp_path)
    argv = driver._pi_argv("n")
    assert "--no-extensions" in argv
    assert "--approve" not in argv
    e_idx = argv.index("-e")
    assert argv[e_idx + 1] == str(tmp_path / ".pi" / "extensions" / "opendraft-tools.ts")
    tools = argv[argv.index("--tools") + 1]
    assert "bash" not in tools and "powershell" not in tools
    for name in ("write_section", "score_draft", "search_literature"):
        assert name in tools
    # PoC finding: the --tools allowlist alone did NOT stop bash. The denylist must.
    excluded = argv[argv.index("--exclude-tools") + 1]
    assert "bash" in excluded and "powershell" in excluded


def test_kill_process_tree_uses_taskkill_on_nt(tmp_path, monkeypatch):
    """pi.cmd wraps node; killing only the wrapper orphans the grandchild and its
    pipe handles (the original smoke hang). Windows must taskkill the tree."""
    calls = []
    monkeypatch.setattr(driver_mod.os, "name", "nt")

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(driver_mod.subprocess, "run", fake_run)
    proc = type("P", (), {"pid": 1234})()
    driver_mod._kill_process_tree(proc)
    assert calls and calls[0][:3] == ["taskkill", "/F", "/T"]
    assert calls[0][3:] == ["/PID", "1234"]


# --------------------------------------------------------------- review task (M2)


def _ledger_root(tmp_path):
    """Fixture root with a checkpoint, two summary-ledger files and a status ledger."""
    root = tmp_path / "out"
    root.mkdir()
    write_checkpoint(root, {
        "topic": "AI in education",
        "academic_level": "master",
        "citation_style": "apa",
        "language": "en",
        "word_targets": {},
    })
    (root / "drafts" / ".ledger").mkdir(parents=True)
    (root / "drafts" / ".ledger" / "introduction.summary.md").write_text(
        "Intro promises a comparative study.", "utf-8")
    (root / "drafts" / ".ledger" / "literature_review.summary.md").write_text(
        "Lit review covers A and B.", "utf-8")
    update_section_status(root, "introduction", status="written", passed=True,
                          open_issues=[], updated_at="2026-01-01T00:00:00")
    update_section_status(root, FULL_LEDGER_KEY, last_total=62, last_passed=False,
                          open_issues=["structure issue somewhere"], updated_at="2026-01-01T00:00:00")
    return root


def test_build_review_prompt_with_ledgers(tmp_path):
    from harness.review_task import build_review_prompt

    root = _ledger_root(tmp_path)
    prompt = build_review_prompt(root)

    assert "GLOBAL cross-section review" in prompt
    assert "AGENTS.md" in prompt
    assert "section_status.json" in prompt
    assert "introduction.summary.md" in prompt
    assert "literature_review.summary.md" in prompt
    # status digest is inlined
    assert "introduction: written, score pass" in prompt
    assert "full draft: 62/100 (fail)" in prompt
    # the five review dimensions
    for kw in ("Terminology", "Narrative", "redundancy", "Citation consistency",
               "Outline conformance"):
        assert kw in prompt
    # strict output format contract
    assert "# Global Issues" in prompt
    assert "## GI-1 [high|medium|low] scope: global|<section name>" in prompt
    assert "Issue:" in prompt
    assert "Suggested fix:" in prompt
    assert "No cross-section issues found." in prompt


def test_build_review_prompt_empty_dir(tmp_path):
    from harness.review_task import build_review_prompt

    prompt = build_review_prompt(tmp_path / "does_not_exist")
    assert "# Global Issues" in prompt
    assert "(status ledger is empty or missing)" in prompt
    assert "No section summaries found" in prompt


# ---------------------------------------------------------- paper map with ledgers


def test_paper_map_with_status_and_summary_ledgers(tmp_path):
    root = _ledger_root(tmp_path)
    (root / "drafts" / "01_introduction.md").write_text("intro words here", "utf-8")

    write_paper_map(root)
    text = (root / "AGENTS.md").read_text(encoding="utf-8")

    # extended Sections table with score/issues columns
    assert "| section | file | target words | words | status | score | issues |" in text
    assert "| introduction | drafts/01_introduction.md | ? | 3 | written | ✓ | 0 |" in text
    # open issues block with the full-draft score
    assert "## Open issues" in text
    assert "Full draft: 62/100 (fail)" in text
    # summary block
    assert "## Section summaries" in text
    assert "- introduction: Intro promises a comparative study." in text
    assert "- literature_review: Lit review covers A and B." in text


def test_paper_map_without_ledger_keeps_legacy_table(tmp_path):
    write_checkpoint(tmp_path, {"topic": "T", "word_targets": {}})
    write_paper_map(tmp_path)
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "| section | file | target words | words | status |" in text
    assert "score" not in text.split("## Writing discipline")[0].split("| section |")[1][:200]
    assert "## Open issues" not in text


# ------------------------------------------------------------- CLI: harness review


def test_cli_harness_review_missing_root_usage_error(tmp_path, monkeypatch):
    class _Boom:
        def __init__(self, *args, **kwargs):
            raise AssertionError("PiDriver must not be constructed on usage errors")

    monkeypatch.setattr(driver_mod, "PiDriver", _Boom)
    from opendraft.cli import run_harness_command

    with pytest.raises(SystemExit) as exc:
        run_harness_command(["review"])
    assert exc.value.code == 2


def test_cli_harness_review_success_writes_global_issues(tmp_path, monkeypatch, capsys):
    calls = {}

    class FakeDriver:
        def __init__(self, root, model=None, pi_bin=None, budget=None):
            self.root = Path(root)
            self.model = model
            self.pi_bin = pi_bin or "fake-pi"

        def prepare(self):
            calls["prepared"] = True

        def run(self, prompt, name):
            calls["prompt"] = prompt
            calls["name"] = name
            return DriverResult(
                ok=True,
                reason="settled",
                stats={"cost": 0.01, "turns": 2},
                journal_path=str(tmp_path / "run_journal.jsonl"),
                settled_text=(
                    "# Global Issues\n\n"
                    "## GI-1 [high] scope: global\nIssue: terms drift.\n"
                    "Suggested fix: unify in methodology.\n\n"
                    "## GI-2 [low] scope: introduction\nIssue: weak echo.\n"
                    "Suggested fix: strengthen conclusion.\n"
                ),
                budget_exceeded=False,
            )

    monkeypatch.setattr(driver_mod, "PiDriver", FakeDriver)
    from opendraft.cli import run_harness_command

    rc = run_harness_command(["review", "--root", str(tmp_path)])
    assert rc == 0

    out_file = tmp_path / "global_issues.md"
    assert out_file.exists()
    assert out_file.read_text(encoding="utf-8").startswith("# Global Issues")

    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["data"]["global_issues_path"] == str(out_file)
    assert payload["data"]["settled_text"].startswith("# Global Issues")
    assert "global issues: 2" in captured.err
    assert calls["name"] == "global-review"
    assert "GLOBAL cross-section review" in calls["prompt"]


def test_cli_harness_review_empty_settled_text_fails(tmp_path, monkeypatch, capsys):
    class FakeDriver:
        def __init__(self, root, model=None, pi_bin=None, budget=None):
            self.root = Path(root)
            self.model = model
            self.pi_bin = pi_bin or "fake-pi"

        def prepare(self):
            pass

        def run(self, prompt, name):
            return DriverResult(
                ok=True, reason="settled", stats={},
                journal_path=str(tmp_path / "run_journal.jsonl"),
                settled_text="   ", budget_exceeded=False,
            )

    monkeypatch.setattr(driver_mod, "PiDriver", FakeDriver)
    from opendraft.cli import run_harness_command

    rc = run_harness_command(["review", "--root", str(tmp_path)])
    assert rc == 1
    assert not (tmp_path / "global_issues.md").exists()
    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert "no text" in payload["error"]




# ------------------------------------------------------- paper orchestrator (M2)


REVIEW_TEXT_TWO_ISSUES = (
    "# Global Issues\n\n"
    "## GI-1 [high] scope: methodology\n"
    "Issue: Methodology lacks the data-collection detail.\n"
    "Suggested fix: Add the sampling paragraph in methodology.\n\n"
    "## GI-2 [medium] scope: global\n"
    "Issue: Terminology drifts between sections.\n"
    "Suggested fix: Unify the term \"adaptivity\" in introduction.\n"
)


def test_parse_global_issues_contract():
    text = (
        "# Global Issues\n\n"
        "## GI-1 [high] scope: methodology\nIssue: lacks detail.\nSuggested fix: add it.\n\n"
        "## GI-2 [low] scope: global\nIssue: drift.\n\n"
        "## GI-3 [medium] scope: introduction\nSuggested fix: strengthen.\n"
    )
    issues = parse_global_issues(text)
    assert [i["id"] for i in issues] == ["GI-1", "GI-2", "GI-3"]
    assert issues[0] == {"id": "GI-1", "severity": "high", "scope": "methodology",
                         "issue": "lacks detail.", "fix": "add it."}
    assert issues[1]["fix"] == ""  # missing Suggested fix tolerated
    assert issues[2]["issue"] == ""  # missing Issue line tolerated


def test_parse_global_issues_no_issues_text():
    assert parse_global_issues(NO_ISSUES_TEXT) == []
    assert parse_global_issues("") == []


def test_parse_global_issues_noise_and_defaults():
    text = (
        "random noise\n"
        "## GI-9 scope: results\nIssue: x\nSuggested fix: y\n"  # no severity bracket
        "## gi-10 [HIGH] scope: discussion\nIssue: z\nSuggested fix: w\n"
        "## GI-11 [medium]\nIssue: orphan scope\nSuggested fix: q\n"  # no scope -> global
    )
    issues = parse_global_issues(text)
    assert issues[0]["severity"] == "medium" and issues[0]["scope"] == "results"
    assert issues[1]["id"] == "gi-10" and issues[1]["severity"] == "high"
    assert issues[2]["scope"] == "global"


class _FakePaperDriver:
    """Canned driver: section sessions write a real draft file (so acceptance scoring and
    full scoring run for real), review/fix sessions return scripted text."""

    def __init__(self, root, calls, review_text, cost=0.05):
        self.root = Path(root)
        self.calls = calls
        self.review_text = review_text
        self.cost = cost

    def run(self, prompt, name):
        self.calls.append(name)
        if name.startswith("section-"):
            section = name[len("section-"):]
            rel = SECTION_FILES[section]["file"]
            f = self.root / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("word " * 200, "utf-8")
            settled = "section written"
        elif name == "global-review":
            settled = self.review_text
        elif name.startswith("fix-"):
            settled = "fixed report"
        else:
            settled = ""
        return DriverResult(
            ok=True, reason="settled", stats={"cost": self.cost, "turns": 2},
            journal_path=str(self.root / f"journal_{name}.jsonl"),
            settled_text=settled, budget_exceeded=False,
        )


def _paper_factory(calls, review_text, cost=0.05):
    def _factory(root=None, model=None, budget=None):
        return _FakePaperDriver(root, calls, review_text, cost)

    return _factory


def _paper_root(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    write_checkpoint(root, {
        "topic": "AI in education", "academic_level": "master",
        "citation_style": "apa", "language": "en", "word_targets": {},
    })
    return root


def test_run_paper_full_flow(tmp_path):
    from agent_tools.common import read_section_status

    root = _paper_root(tmp_path)
    # introduction is already written+passed -> must be skipped (resume semantics)
    (root / "drafts").mkdir(exist_ok=True)
    (root / "drafts" / "01_introduction.md").write_text("word " * 300, "utf-8")
    update_section_status(root, "introduction", status="written", passed=True,
                          open_issues=[], updated_at="2026-01-01T00:00:00")

    calls = []
    result = run_paper(
        root, driver_factory=_paper_factory(calls, REVIEW_TEXT_TWO_ISSUES),
        budget=PaperBudget(),
    )

    assert result.ok is True
    assert result.sections_skipped == ["introduction"]
    assert result.sections_completed == [
        "literature_review", "methodology", "results", "discussion", "conclusion",
    ]
    # session names + order: sections (outline order), review, then one fix per section
    assert calls == [
        "section-literature_review", "section-methodology", "section-results",
        "section-discussion", "section-conclusion",
        "global-review",
        "fix-methodology", "fix-introduction",
    ]
    # review deliverable persisted (settled text is stripped before writing)
    assert (root / "global_issues.md").read_text(encoding="utf-8") == REVIEW_TEXT_TWO_ISSUES.strip()
    assert result.review_ok is True
    assert result.issues_found == 2
    assert sorted(result.issues_fixed_report) == ["introduction", "methodology"]
    # full-score phase ran and the ledger "full" bucket matches the reported score
    assert result.full_score is not None
    ledger = read_section_status(root)
    assert ledger["full"]["last_total"] == result.full_score
    # one journal path per session; cost accumulated from session stats
    assert len(result.journal_paths) == len(calls)
    assert result.total_cost == pytest.approx(0.05 * len(calls))
    assert result.warnings == []


def test_run_paper_unknown_sections_dropped(tmp_path):
    root = _paper_root(tmp_path)
    calls = []
    result = run_paper(
        root, sections=["introduction", "not_a_section"],
        driver_factory=_paper_factory(calls, ""),
        budget=PaperBudget(),
    )
    assert calls == ["section-introduction", "global-review"]
    assert result.ok is True
    assert any("not_a_section" in w for w in result.warnings)


def test_run_paper_total_budget_exhaustion(tmp_path):
    root = _paper_root(tmp_path)
    calls = []
    result = run_paper(
        root, driver_factory=_paper_factory(calls, REVIEW_TEXT_TWO_ISSUES, cost=1.0),
        budget=PaperBudget(total_cost=0.5),
    )
    # first session consumed $1.0 > $0.5 total: everything after is skipped with warnings
    assert calls == ["section-introduction"]
    assert result.ok is False  # un-attempted sections count as failed
    assert result.review_ok is False
    assert any("budget" in w for w in result.warnings)
    assert any("review skipped" in w for w in result.warnings)


def test_run_paper_empty_review_text_continues(tmp_path):
    root = _paper_root(tmp_path)
    calls = []
    result = run_paper(
        root, sections=["introduction"],
        driver_factory=_paper_factory(calls, ""),  # review settles with no text
        budget=PaperBudget(),
    )
    assert result.review_ok is False
    assert result.issues_found == 0
    assert not (root / "global_issues.md").exists()
    assert [c for c in calls if c.startswith("fix-")] == []  # nothing to fix
    assert result.full_score is not None  # acceptance still ran
    assert result.sections_completed == ["introduction"]
    assert result.ok is True


# ------------------------------------------------------------- CLI: harness paper


def test_cli_harness_paper_missing_root_usage_error(tmp_path, monkeypatch):
    class _Boom:
        def __init__(self, *args, **kwargs):
            raise AssertionError("run_paper must not be called on usage errors")

    monkeypatch.setattr("harness.paper_task.run_paper", _Boom)
    from opendraft.cli import run_harness_command

    with pytest.raises(SystemExit) as exc:
        run_harness_command(["paper"])
    assert exc.value.code == 2


def test_cli_harness_paper_success_envelope(tmp_path, monkeypatch, capsys):
    captured = {}

    def fake_run_paper(root, **kwargs):
        captured["root"] = root
        captured.update(kwargs)
        return PaperResult(
            ok=True,
            sections_completed=["introduction", "literature_review", "methodology",
                                "results", "discussion", "conclusion"],
            sections_skipped=[],
            review_ok=True,
            issues_found=2,
            issues_fixed_report={"methodology": "fixed report"},
            full_score=88,
            warnings=[],
            total_cost=0.42,
            journal_paths=[str(tmp_path / "run_journal.jsonl")],
        )

    monkeypatch.setattr("harness.paper_task.run_paper", fake_run_paper)
    from opendraft.cli import run_harness_command

    rc = run_harness_command([
        "paper", "--root", str(tmp_path),
        "--sections", "introduction,conclusion",
        "--max-cost", "2.5", "--max-turns", "9", "--compile",
    ])
    assert rc == 0

    captured_io = capsys.readouterr()
    payload = json.loads(captured_io.out.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["data"]["full_score"] == 88
    assert payload["data"]["issues_found"] == 2
    assert payload["data"]["total_cost"] == 0.42

    assert captured["root"] == Path(tmp_path)
    assert captured["sections"] == ["introduction", "conclusion"]
    assert captured["budget"].total_cost == 2.5
    assert captured["max_turns"] == 9
    assert captured["compile_at_end"] is True


def test_queue_line_source_timeout_returns_none_not_raises():
    """queue.Empty on timeout is a budget-tick, not a crash (end-to-end regression:
    pi silence > poll_interval used to kill sessions with reason 'Empty: ')."""
    import queue as _queue

    src = driver_mod._queue_line_source(_queue.Queue())
    assert src(timeout=0.01) is None  # must NOT raise queue.Empty

    q = _queue.Queue()
    q.put('{"type":"x"}')
    src2 = driver_mod._queue_line_source(q)
    assert src2(timeout=0.01) == '{"type":"x"}'

    q3 = _queue.Queue()
    q3.put(driver_mod._EOF)
    src3 = driver_mod._queue_line_source(q3)
    with pytest.raises(driver_mod._Eof):
        src3(timeout=0.01)


def test_default_opendraft_bin_prefers_shell_free_binary(tmp_path, monkeypatch):
    """The pi extension spawns OPENDRAFT_BIN shell-free — a bare 'opendraft' becomes
    spawn ENOENT there, and .cmd wrappers cannot forward JSON args. Resolution must be
    deterministic: console script first, venv python second (with OPENDRAFT_BOOTSTRAP)."""
    monkeypatch.delenv("OPENDRAFT_BIN", raising=False)
    monkeypatch.setattr(driver_mod, "_load_engine_dotenv", lambda: None)
    exe = driver_mod.REPO_DIR / ".venv" / "Scripts" / "opendraft.exe"
    py = driver_mod.REPO_DIR / ".venv" / "Scripts" / "python.exe"
    resolved = driver_mod._default_opendraft_bin()
    if exe.exists():
        assert resolved == str(exe)
    elif py.exists():
        assert resolved == str(py)
        driver = PiDriver(tmp_path)
        env = driver._build_env()
        assert "opendraft.cli" in env["OPENDRAFT_BOOTSTRAP"]
    else:
        import pytest as _pt
        _pt.skip("repo venv not present")

"""SystemHarness — the Ф0 skeleton of the v7next deep-integration suite (plan §8).

Not a test module (pytest collects ``test_*.py`` only) — this is the machinery the
``tests/system_e2e/test_*`` scenario modules drive: a KEYLESS isolated real-server
stack (roast F21), a scriptable loopback stub model whose review-organ branch sits
BEFORE the finalization-turn check (roast F22 / plan §8), and readers for the durable
artifacts every scenario asserts against. The direct precedent is
``tests/fixtures_e2e_cancellation.py`` on the ``ouroboros_v7_wip`` reference branch
(same split, same stub idiom, same 0600 settings write); this file generalizes it from
the cancellation protocol to the whole system surface and hardens the egress story.

KEYLESS LANE CONTRACT (F21). The mock lane must be structurally unable to spend money
or leak an operator credential into a child the scenarios do not control:

* the isolated ``settings.json`` is built from scratch (never copied from live
  settings), pins EVERY model-slot key the tree declares
  (``provider_models.ACTIVE_MODEL_SETTING_KEYS`` + legacy) so a new upstream slot is
  pinned by construction, and carries exactly one "credential" — the loopback stub's
  non-secret placeholder pair;
* ``KeylessIsolatedServer`` strips every provider credential the tree knows about
  (``server_runner._PROVIDER_ENV_KEYS`` ∪ ``provider_models.ALL_PROVIDER_CREDENTIAL_KEYS``)
  plus all proxy variables from the child environment, ON TOP of the base
  ``IsolatedServer`` sanitization — the base ``_is_secret_env_key`` deliberately
  EXEMPTS provider keys (benchmark servers need them), which for this lane is exactly
  the ANTHROPIC_API_KEY hole the plan names;
* an un-pinned slot therefore routes to a slug whose provider has no credential and
  fails loudly instead of silently reaching a paid provider.

Full egress interception (socket-level deny + evidence) is Ф4 scope, not Ф0.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from devtools.benchmarks.common.server_runner import (  # noqa: E402
    IsolatedServer,
    _PROVIDER_ENV_KEYS,
    supervisor_state_is_ready,  # noqa: F401 (re-export: THE readiness contract)
)
from ouroboros.provider_models import (  # noqa: E402
    ACTIVE_MODEL_SETTING_KEYS,
    ALL_PROVIDER_CREDENTIAL_KEYS,
    LEGACY_MODEL_SETTING_KEYS,
)
from ouroboros.tools.scope_review_contract import SCOPE_REQUIRED_ITEMS  # noqa: E402

LANE_MOCK = "mock"

# The scenario inventory of this suite (plan §8). The scenario test module fails if an
# id loses its test — a scenario must be retired deliberately, not by deletion.
# Scenarios land WITH their phases (roast F22); Ф0 carries only the two smokes that
# prove the skeleton itself.
SCENARIOS = {
    "S1": ("boot / identity / task contract smoke", LANE_MOCK),
    "S2": ("review-organ smoke: commit_reviewed triad+scope on a doc-only diff", LANE_MOCK),
}

MOCK_SLUG = "openai-compatible::mock-model"

# ---------------------------------------------------------------------------
# Prompt markers the stub classifies review-organ calls by (roast F22).
#
# These are VERBATIM literals from the tree under test and WILL drift with upstream:
#   REVIEWER_SLOT_MARKER   — ouroboros/review_execution.py::_render_prompt_parts
#   ACCEPTANCE_KEYS_MARKER — same function, the task_acceptance criteria_used key list
#   TRIAD_USER_MARKER      — ouroboros/tools/review.py::_dispatch_unified_review
#   SCOPE_USER_MARKER      — ouroboros/tools/scope_review.py::_call_scope_llm
# The default-lane marker-pin test greps them out of the source files so drift is a
# named test failure, not a silently mute stub.
# ---------------------------------------------------------------------------
REVIEWER_SLOT_MARKER = "You are an independent Ouroboros reviewer slot."
ACCEPTANCE_KEYS_MARKER = "criteria_used (the acceptance criteria you re-derived"
TRIAD_USER_MARKER = "Review the staged diff and context provided in the instructions above."
SCOPE_USER_MARKER = "Review the staged change and context above. Output ONLY a JSON array."
FINALIZATION_MARKERS = ("[OWNER_STOP]", "[FINALIZE_NOW]")

MARKER_SOURCES = {
    REVIEWER_SLOT_MARKER: "ouroboros/review_execution.py",
    ACCEPTANCE_KEYS_MARKER: "ouroboros/review_execution.py",
    TRIAD_USER_MARKER: "ouroboros/tools/review.py",
    SCOPE_USER_MARKER: "ouroboros/tools/scope_review.py",
}


# ---------------------------------------------------------------------------
# Opt-in gate
# ---------------------------------------------------------------------------

def lane_enabled(lane: str) -> bool:
    selected = str(os.environ.get("OUROBOROS_E2E_DEEP") or "").strip().lower()
    return selected == lane


def require_lane(lane: str) -> None:
    if not lane_enabled(lane):
        pytest.skip(
            f"set OUROBOROS_E2E_DEEP={lane} to run the {lane} deep-integration lane "
            "(spawns a real isolated server; see tests/system_e2e/)"
        )


# ---------------------------------------------------------------------------
# Message flattening: review prompts arrive as block lists (cached_prompt_blocks),
# agent-loop prompts as plain strings — marker checks must see both.
# ---------------------------------------------------------------------------

def message_text(message) -> str:
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict)
        )
    return ""


def body_text(body: dict) -> str:
    return "\n".join(message_text(m) for m in (body.get("messages") or []))


def classify_call(body: dict) -> str:
    """Name the branch a chat-completion body belongs to.

    Returns one of: ``safety``, ``scope_review``, ``triad_review``, ``acceptance``,
    ``reviewer_slot``, ``finalization``, ``agent``. ORDER MATTERS (roast F22): every
    review-organ branch is checked BEFORE the finalization-turn check, because a
    review packet may quote a transcript that itself contains a finalization marker —
    a stub that answered such a packet with a final chat answer would silently break
    the review organ mid-scenario.
    """
    fmt = body.get("response_format")
    if isinstance(fmt, dict) and fmt.get("type") == "json_object":
        return "safety"
    user_tail = "\n".join(
        message_text(m) for m in (body.get("messages") or [])
        if isinstance(m, dict) and m.get("role") == "user"
    )
    full = body_text(body)
    # Scope before triad: both user messages start with "Review the staged".
    if SCOPE_USER_MARKER in user_tail:
        return "scope_review"
    if TRIAD_USER_MARKER in user_tail:
        return "triad_review"
    if REVIEWER_SLOT_MARKER in full:
        return "acceptance" if ACCEPTANCE_KEYS_MARKER in full else "reviewer_slot"
    if any(marker in full for marker in FINALIZATION_MARKERS):
        return "finalization"
    return "agent"


# ---------------------------------------------------------------------------
# Canned review-organ verdicts (all-clean). Shapes come from the tree's own parsers:
# triad — triad_review.REVIEW_JSON_ARRAY_CONTRACT ([] + NO_FINDINGS sentinel);
# scope — scope_review_contract.normalize_scope_items (required matrix, PASS reasons
# must be non-terse); reviewer slot — review_execution's "Return JSON with keys" list.
# ---------------------------------------------------------------------------

TRIAD_CLEAN_TEXT = "[]\nNO_FINDINGS"


def scope_clean_text() -> str:
    return json.dumps([
        {
            "item": item,
            "verdict": "PASS",
            "severity": "advisory",
            "reason": "Stub scope reviewer: checked and clean for this scripted smoke diff.",
        }
        for item in sorted(SCOPE_REQUIRED_ITEMS)
    ])


def reviewer_slot_clean_text(kind: str) -> str:
    verdict = {"verdict": "PASS", "findings": [], "summary": "stub reviewer slot: clean."}
    if kind == "acceptance":
        verdict["outcome_tier"] = "solved"
        verdict["dialogue_status"] = "continue_actionable"
        verdict["criteria_used"] = []
    return json.dumps(verdict)


def scripted_completion(body: dict, seq: int, script_next, final_answer: str) -> tuple[str, dict]:
    """The stub's whole decision function, pure so the default lane can pin it.

    ``script_next`` is a callable returning the next scripted tool step (or None when
    the script is exhausted); it is only consulted on plain agent turns. Returns
    ``(kind, message)`` where message is the OpenAI-style assistant message.
    """
    kind = classify_call(body)
    if kind == "safety":
        return kind, {"role": "assistant",
                      "content": json.dumps({"status": "SAFE", "reason": "stub"})}
    if kind == "scope_review":
        return kind, {"role": "assistant", "content": scope_clean_text()}
    if kind == "triad_review":
        return kind, {"role": "assistant", "content": TRIAD_CLEAN_TEXT}
    if kind in ("acceptance", "reviewer_slot"):
        return kind, {"role": "assistant", "content": reviewer_slot_clean_text(kind)}
    if kind == "finalization":
        return kind, {"role": "assistant", "content": final_answer}
    step = script_next(body) if body.get("tools") else None
    if step is None:
        return "final", {"role": "assistant", "content": final_answer}
    call = {"name": str(step["tool"]),
            "arguments": json.dumps(step.get("arguments") or {})}
    return "agent", {
        "role": "assistant", "content": "still working",
        "tool_calls": [{"id": f"call_{seq}", "type": "function", "function": call}],
    }


class ScriptedStubModel:
    """Keep-alive OpenAI-compatible stub model with an ordered per-scenario script.

    Extends the ``StubModelServer`` idiom of the cancellation harness: instead of one
    fixed keepalive tool, a scenario hands the stub an ORDERED list of tool steps
    (``{"tool": name, "arguments": {...}}``); each plain agent turn consumes one step,
    and an exhausted script yields the tool-less final answer. Review-organ calls
    (triad / scope / reviewer-slot / acceptance) NEVER consume script steps — they are
    classified by prompt markers and answered with canned all-clean verdicts, and that
    classification runs BEFORE the finalization-turn check (roast F22). Safety
    supervisor calls (json_object response_format) always get a SAFE verdict.

    Every call is recorded as ``(kind, body)`` in ``self.calls``; ``self.kinds()``
    gives the observed branch sequence a scenario asserts against.
    """

    def __init__(self, script=None, *, final_answer: str = "Final answer: scripted scenario complete.",
                 latency_sec: float = 0.0) -> None:
        self.script = list(script or [])
        self.final_answer = final_answer
        self.latency_sec = latency_sec
        self.calls: list = []          # (kind, body) in arrival order
        self._script_index = 0
        self._lock = threading.Lock()
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - stdlib callback name
                if self.path.rstrip("/").endswith("/models"):
                    # >=1M ON PURPOSE, and it is load-bearing twice: the capability-
                    # evidence /models probe stores this as a CONFIRMED window, which
                    # (a) sizes the triad fit budget (a 400K window under the cold
                    # 1.65 density floor caps input at ~202K — BELOW the ~226K
                    # governance pack, blocking every commit_reviewed before
                    # dispatch), and (b) satisfies the BIBLE P3 >=1M floor that
                    # scope review's BLOCKING authority requires.
                    return self._send({"data": [{"id": "mock-model", "max_model_len": 2_000_000}]})
                self.send_error(404)

            def do_POST(self):  # noqa: N802 - stdlib callback name
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads((self.rfile.read(length) or b"{}").decode("utf-8"))
                except ValueError:
                    body = {}
                if not isinstance(body, dict):
                    body = {}
                if outer.latency_sec:
                    time.sleep(outer.latency_sec)
                return self._send(outer._completion(body))

            def _send(self, payload):
                data = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args):
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _next_step(self, _body) -> dict | None:
        if self._script_index >= len(self.script):
            return None
        step = self.script[self._script_index]
        self._script_index += 1
        return step

    def _completion(self, body: dict) -> dict:
        with self._lock:
            seq = len(self.calls) + 1
            kind, message = scripted_completion(body, seq, self._next_step, self.final_answer)
            self.calls.append((kind, body))
        return {
            "id": f"stub-{seq}",
            "object": "chat.completion",
            "model": str(body.get("model") or "mock-model"),
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    def kinds(self) -> list[str]:
        with self._lock:
            return [kind for kind, _ in self.calls]

    def script_consumed(self) -> bool:
        with self._lock:
            return self._script_index >= len(self.script)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def __enter__(self) -> "ScriptedStubModel":
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._server.shutdown()
        self._server.server_close()


# ---------------------------------------------------------------------------
# Keyless isolated server (roast F21)
# ---------------------------------------------------------------------------

# Everything the child environment must NOT carry in the keyless lane. The env union
# closes the documented hole: IsolatedServer._is_secret_env_key EXEMPTS provider keys,
# so an inherited ANTHROPIC_API_KEY survives the base sanitization by design.
STRIPPED_PROVIDER_ENV_KEYS = frozenset(_PROVIDER_ENV_KEYS) | frozenset(ALL_PROVIDER_CREDENTIAL_KEYS)
PROXY_ENV_KEYS = frozenset({
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
})


class KeylessIsolatedServer(IsolatedServer):
    """``IsolatedServer`` whose child env can never carry a provider credential.

    The base class keeps ``_PROVIDER_ENV_KEYS`` in the child on purpose (benchmark
    servers authenticate from them). This lane's contract is the opposite: the ONLY
    provider config a scenario server may see is what the scenario's settings.json
    says, and that file only ever names the loopback stub.
    """

    def _env(self) -> dict:
        env = super()._env()
        for key in list(env):
            if key in STRIPPED_PROVIDER_ENV_KEYS or key in PROXY_ENV_KEYS:
                env.pop(key, None)
        return env


def keyless_settings(stub: ScriptedStubModel, **overrides) -> dict:
    """The isolated settings.json for a keyless scenario server.

    Every model-slot key the TREE declares is pinned — un-listed keys default to the
    empty string (slot disabled / no fallback), the live loop + review slots to the
    stub slug. Deriving the slot list from ``provider_models`` (instead of an
    enumerated literal, as the cancellation-harness precedent did) means an upstream
    slot added tomorrow is pinned by construction rather than silently defaulting to
    a live OpenRouter route. Overrides carrying a real provider credential are a
    scenario bug and are refused loudly.
    """
    stub_pair = {"OPENAI_COMPATIBLE_API_KEY", "OPENAI_COMPATIBLE_BASE_URL"}
    forbidden = (set(ALL_PROVIDER_CREDENTIAL_KEYS) - stub_pair) & set(overrides)
    if forbidden:
        raise ValueError(
            f"keyless lane: overrides must not carry provider credentials: {sorted(forbidden)}"
        )
    cfg: dict = {key: "" for key in (*ACTIVE_MODEL_SETTING_KEYS, *LEGACY_MODEL_SETTING_KEYS)}
    cfg.update({
        # Disk-authored keys: config.apply_settings_to_env cannot author these from
        # the environment, so they have to be in the file, written fresh.
        "OUROBOROS_SAFETY_MODE": "off",
        "OUROBOROS_CONTEXT_MODE": "low",
        "OUROBOROS_RUNTIME_MODE": "light",
        "OUROBOROS_TASK_REVIEW_MODE": "off",
        "OUROBOROS_POST_TASK_EVOLUTION": "false",
        "OUROBOROS_MAX_WORKERS": 4,
        "TOTAL_BUDGET": 10.0,
        "OUROBOROS_PER_TASK_COST_USD": 10.0,
        "OPENAI_COMPATIBLE_BASE_URL": stub.base_url,
        "OPENAI_COMPATIBLE_API_KEY": "stub-key-not-a-credential",
    })
    for slot in ("OUROBOROS_MODEL", "OUROBOROS_MODEL_LIGHT",
                 "OUROBOROS_REVIEW_MODELS", "OUROBOROS_SCOPE_REVIEW_MODELS",
                 "OUROBOROS_SCOPE_REVIEW_MODEL"):
        cfg[slot] = MOCK_SLUG
    cfg.update(overrides)
    return cfg


def assert_settings_keyless(settings: dict) -> None:
    """Fail loudly if a scenario's settings smuggle a provider credential."""
    stub_pair = {"OPENAI_COMPATIBLE_API_KEY", "OPENAI_COMPATIBLE_BASE_URL"}
    offending = sorted(
        key for key in settings
        if key in ALL_PROVIDER_CREDENTIAL_KEYS and key not in stub_pair and str(settings[key] or "").strip()
    )
    assert not offending, f"keyless settings carry provider credentials: {offending}"
    base = str(settings.get("OPENAI_COMPATIBLE_BASE_URL") or "")
    assert base.startswith("http://127.0.0.1:"), f"stub base_url is not loopback: {base!r}"


def clone_repo(destination: pathlib.Path) -> pathlib.Path:
    """One throwaway clone of the checkout under test.

    A clone (not the working tree) is what the runtime is allowed to run against: the
    server owns its repo directory, so an E2E server must never be pointed at a live
    worktree. The commit identity is pinned locally so reviewed-commit scenarios never
    depend on the operator's global git config.
    """
    clone = pathlib.Path(destination) / "clone"
    subprocess.run(["git", "clone", "--no-hardlinks", "-q", str(REPO_ROOT), str(clone)],
                   check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-B", "ouroboros"], cwd=str(clone),
                   check=True, capture_output=True)
    subprocess.run(["git", "remote", "remove", "origin"], cwd=str(clone),
                   check=False, capture_output=True)
    subprocess.run(["git", "config", "user.name", "SystemHarness"], cwd=str(clone),
                   check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "system-harness@e2e.invalid"],
                   cwd=str(clone), check=True, capture_output=True)
    return clone


def write_settings_file(settings_path: pathlib.Path, settings: dict) -> None:
    """0600-before-content settings write (carried over from the v7_wip harness: a
    default-umask write_text once briefly published a live key world-readable; this
    lane never holds a live key, but the shape must not regress when a paid lane
    reuses it)."""
    fd = os.open(settings_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    if hasattr(os, "fchmod"):
        os.fchmod(fd, 0o600)  # O_CREAT's mode only applies on creation
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(settings, indent=2))
    if not hasattr(os, "fchmod"):
        os.chmod(settings_path, 0o600)


def start_server(clone, root, settings: dict, *, ready_timeout: float = 300) -> KeylessIsolatedServer:
    assert_settings_keyless(settings)
    data_root = pathlib.Path(root) / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    settings_path = data_root / "settings.json"
    write_settings_file(settings_path, settings)
    server = KeylessIsolatedServer(clone, data_root, settings_path)
    server.start(ready_timeout=ready_timeout)
    return server


# ---------------------------------------------------------------------------
# ArtifactOracle: readers of the durable artifacts every scenario asserts against.
# Never an HTTP 200 on its own, never a harness exit code (AGENTS.md: the exit code
# is not the run status) — scenarios read back what the owner and watchdog read.
# ---------------------------------------------------------------------------

class ArtifactOracle:
    def __init__(self, data_root) -> None:
        self.data_root = pathlib.Path(data_root)

    def task_drive(self, task_id: str) -> "ArtifactOracle":
        """The oracle for a HEADLESS task's forked drive root.

        On this tree a headless task's ToolContext drive root is
        ``state/headless_tasks/<task_id>/data`` under the server's data root, so the
        durable review evidence (state/advisory_review.json, the
        advisory_review_bypassed / scope_review_complete events) lands THERE, not in
        the server-level files. Falls back to the server root when the task has no
        forked drive (e.g. a direct-chat turn)."""
        forked = self.data_root / "state" / "headless_tasks" / str(task_id) / "data"
        return ArtifactOracle(forked) if forked.is_dir() else self

    # -- json state files ---------------------------------------------------

    def _json(self, relpath: str) -> dict:
        path = self.data_root / relpath
        if not path.exists():
            return {}
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}

    def queue_snapshot(self) -> dict:
        return self._json("state/queue_snapshot.json")

    def state(self) -> dict:
        return self._json("state/state.json")

    def advisory_review(self) -> dict:
        return self._json("state/advisory_review.json")

    def cancel_intents(self) -> dict:
        blob = self._json("state/cancel_intents.json")
        return blob.get("intents") if isinstance(blob.get("intents"), dict) else {}

    def task_result(self, task_id: str) -> dict:
        return self._json(f"task_results/{task_id}.json")

    def task_result_bytes(self, task_id: str) -> bytes:
        return (self.data_root / "task_results" / f"{task_id}.json").read_bytes()

    # -- jsonl logs -----------------------------------------------------------

    def _jsonl(self, relpath: str, *, type_filter: str = "") -> list:
        path = self.data_root / relpath
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            if type_filter and type_filter not in line:
                continue  # cheap pre-filter, exact check below
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            if type_filter and str(row.get("type") or "") != type_filter:
                continue
            rows.append(row)
        return rows

    def events(self, event_type: str = "") -> list:
        return self._jsonl("logs/events.jsonl", type_filter=event_type)

    def supervisor_rows(self, row_type: str = "") -> list:
        return self._jsonl("logs/supervisor.jsonl", type_filter=row_type)

    def tools_rows(self) -> list:
        return self._jsonl("logs/tools.jsonl")

    def chat_bytes(self) -> bytes:
        path = self.data_root / "logs" / "chat.jsonl"
        return path.read_bytes() if path.exists() else b""

    def running_ids(self) -> set:
        return {
            str(row.get("id") or "")
            for row in (self.queue_snapshot().get("running") or [])
            if isinstance(row, dict)
        }


# ---------------------------------------------------------------------------
# Small drivers
# ---------------------------------------------------------------------------

def wait_until(predicate, timeout: float, interval: float = 0.5):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    return last


def submit_running(server: IsolatedServer, description: str, *, timeout: float = 120) -> str:
    """Submit a task and wait until the supervisor actually has it RUNNING."""
    task_id = server.submit(description)
    assert task_id, "submit returned no task id"
    oracle = ArtifactOracle(server.data_root)
    running = wait_until(lambda: task_id in oracle.running_ids(), timeout)
    assert running, f"task {task_id} never reached the RUNNING set"
    return task_id


def wait_durable_result(oracle: ArtifactOracle, task_id: str, *, timeout: float = 180) -> dict:
    """Wait for ``task_results/<id>.json`` to reach a TERMINAL status and return it.

    The HTTP task view can report ``completed`` while the durable terminal write is
    still in flight behind post-task processing (observed live on this tree: the
    stored row said ``scheduled`` seconds after the API said ``completed``). A
    scenario that asserts the durable record must wait for the record, not for the
    HTTP answer.
    """
    terminal = {"completed", "failed", "cancelled", "rejected_duplicate"}
    stored = wait_until(
        lambda: (
            oracle.task_result(task_id)
            if str(oracle.task_result(task_id).get("status") or "") in terminal
            else None
        ),
        timeout,
    )
    assert stored, (
        f"task {task_id} durable result never reached a terminal status: "
        f"{oracle.task_result(task_id)!r}"
    )
    return stored

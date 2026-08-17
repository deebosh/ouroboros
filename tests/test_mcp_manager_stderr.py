"""Regression tests for the MCP discovery stderr-loss class-fix (ibl-mcp-discovery-stderr-loss).

Before the fix, ``/api/mcp/refresh`` returned the SDK's outer exception string
("``ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)``") without
unwrapping the underlying cause or capturing subprocess stderr. Owners could
not diagnose handshake failures (missing API keys, missing executables, etc.)
because the surfaced message was structurally un-diagnosable.

The fix adds ``_stringify_mcp_failure`` which walks ``exc.exceptions`` /
``__cause__``, and per-call stderr capture via ``stdio_client(errlog=...)``.
These tests assert each of the design's four acceptance claims end-to-end:

  - claim_1: TaskGroup handshake error → response carries underlying class+message
  - claim_2: failing stdio subprocess wrote to stderr → response carries stderr_tail
  - claim_3: successful refresh shape stays byte-compatible except for OPTIONAL keys
  - claim_4: SDK without errlog= support → degraded mode runs without crash
"""

from __future__ import annotations

import io

import pytest

from ouroboros import mcp_client


# ---------------------------------------------------------------------------
# Fixtures + helpers (parallel lane — pure mocks, no real subprocess / network)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_manager():
    """Reset module-level singleton between tests (mirrors tests/test_mcp_client.py)."""
    mcp_client.reset_manager_for_tests()
    yield
    mcp_client.reset_manager_for_tests()


def _settings(*servers: dict, enabled: bool = True, timeout: int = 60) -> dict:
    return {
        "MCP_ENABLED": enabled,
        "MCP_TOOL_TIMEOUT_SEC": timeout,
        "MCP_SERVERS": list(servers),
    }


def _good_server(**overrides) -> dict:
    base = {
        "id": "demo",
        "name": "Demo",
        "enabled": True,
        "transport": "streamable_http",
        "url": "https://example.com/mcp",
        "auth_header": "Authorization",
        "auth_token": "Bearer secret-1234",
        "allowed_tools": [],
    }
    base.update(overrides)
    return base


class _StderrAwareFake:
    """Fake ``_async_list_tools`` that writes to ``stderr_buffer`` then raises.

    Mirrors the production path where ``stdio_client(errlog=...)`` captures
    the subprocess's stderr and the SDK raises ``BaseExceptionGroup`` around
    the real handshake error.
    """

    def __init__(self, exc_to_raise: BaseException, stderr_text: str = ""):
        self.exc_to_raise = exc_to_raise
        self.stderr_text = stderr_text

    async def __call__(self, cfg, timeout, stderr_buffer=None):
        if stderr_buffer is not None and self.stderr_text:
            stderr_buffer.write(self.stderr_text)
        raise self.exc_to_raise


class _OkFake:
    """Successful ``_async_list_tools`` returning a single tool descriptor."""

    async def __call__(self, cfg, timeout, stderr_buffer=None):
        return [
            {
                "name": "echo",
                "description": "",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]


def _wire(mgr, fake):
    """Bind a callable fake as the manager's ``_async_list_tools`` hook."""
    mgr._async_list_tools = fake
    mgr._async_call_tool = fake  # symmetry; not exercised by refresh_server


def _wrap_in_task_group(inner: BaseException) -> BaseExceptionGroup:
    """Return a real ``BaseExceptionGroup`` wrapping ``inner``.

    A bare ``BaseExceptionGroup(...)`` instance created via the constructor
    is sometimes classified as non-raisable; raising it through a ``try`` /
    ``except`` block keeps it a real exception object the manager can catch.
    """
    try:
        raise BaseExceptionGroup("sdk-wrapper", [inner])
    except BaseExceptionGroup as exc:
        return exc


# ---------------------------------------------------------------------------
# claim_1: TaskGroup handshake → underlying class + message, no opaque string
# ---------------------------------------------------------------------------


def test_refresh_surfaces_underlying_exception_in_error_message():
    """End-to-end: BaseExceptionGroup(ValueError) unwraps in the response."""
    inner = ValueError("handshake failed: protocol mismatch")
    wrapped = _wrap_in_task_group(inner)
    mgr = mcp_client.MCPManager()
    _wire(mgr, _StderrAwareFake(wrapped, stderr_text=""))
    mgr.reconfigure(_settings(_good_server(id="failing")))
    outcome = mgr.refresh_server("failing")

    assert outcome["ok"] is False
    # The opaque SDK outer string MUST NOT leak through.
    assert "1 sub-exception" not in outcome["error"], outcome["error"]
    # The underlying class + message MUST surface.
    assert "ValueError" in outcome["error"]
    assert "protocol mismatch" in outcome["error"]
    # Structured kind surfaces for routing/UI consumers.
    assert outcome["error_kind"] == "task_group_failure"
    # Server runtime stores the structured kind for status payload consumers.
    rt_status = mgr.status_payload()["servers"][0]
    assert rt_status["last_error_kind"] == "task_group_failure"


# ---------------------------------------------------------------------------
# claim_2: failing stdio subprocess wrote to stderr → stderr_tail surfaces
# ---------------------------------------------------------------------------


def test_refresh_includes_stderr_tail_when_stdio_fails():
    """Subprocess stderr written before failure surfaces as a bounded tail."""
    inner = ValueError("subprocess exit 1")
    mgr = mcp_client.MCPManager()
    fake = _StderrAwareFake(
        inner,
        stderr_text="Error: MINIMAX_API_KEY is required\nSet it in env and retry\n",
    )
    _wire(mgr, fake)
    mgr.reconfigure(_settings(_good_server(id="failing")))
    outcome = mgr.refresh_server("failing")

    assert outcome["ok"] is False
    assert "stderr_tail" in outcome, outcome
    # The actual subprocess diagnostic line is in the surfaced tail.
    assert "MINIMAX_API_KEY is required" in outcome["stderr_tail"]
    assert "Set it in env and retry" in outcome["stderr_tail"]


def test_stringify_omission_note_when_stderr_exceeds_bound():
    """Bounded tail carries an explicit ⚠️ OMISSION NOTE when the buffer clipped."""
    inner = ValueError("x")
    buf = io.StringIO()
    # 200 lines * 80 chars ≈ 16 KB > 4 KB bound; will be truncated.
    buf.write("\n".join(["x" * 80 for _ in range(200)]))
    _, _, tail = mcp_client._stringify_mcp_failure(inner, stderr_buffer=buf)
    assert "OMISSION NOTE" in tail


# ---------------------------------------------------------------------------
# claim_3: success shape stays byte-compatible except for OPTIONAL new keys
# ---------------------------------------------------------------------------


def test_refresh_backward_compat_on_success():
    """Successful refresh returns the pre-fix shape; no new required keys appear."""
    mgr = mcp_client.MCPManager()
    _wire(mgr, _OkFake())
    mgr.reconfigure(_settings(_good_server(id="svc")))
    outcome = mgr.refresh_server("svc")

    assert outcome["ok"] is True
    # Pre-fix required keys still present.
    assert outcome["server_id"] == "svc"
    assert outcome["tool_count"] == 1
    assert isinstance(outcome["tools"], list) and len(outcome["tools"]) == 1
    # Pre-fix required top-level keys accounted for: ok / server_id / tool_count / tools.
    # New failure-only keys MUST NOT appear on success.
    assert "error" not in outcome
    assert "error_kind" not in outcome
    assert "stderr_tail" not in outcome


# ---------------------------------------------------------------------------
# claim_4: SDK without errlog= support → degraded mode, no crash
# ---------------------------------------------------------------------------


def test_transport_factory_falls_back_when_sdk_lacks_errlog_kwarg(monkeypatch):
    """``stdio_client(errlog=...)`` raising TypeError falls back to ``stdio_client(params)``.

    This is the SDK-degraded-mode claim: older SDKs without ``errlog=``
    must not crash refresh — they run with the SDK's default stderr
    (lost to /dev/null) and refresh proceeds without a ``stderr_tail``.
    """
    cfg = mcp_client.MCPServerConfig(
        id="x",
        name="x",
        enabled=True,
        transport="stdio",
        command="python3",
        args=["-c", "pass"],
        auth_header="",
        auth_token="",
        url="",
        allowed_tools=[],
    )

    captured: dict = {}

    class _NoopCM:
        async def __aenter__(self):
            return ("read", "write")

        async def __aexit__(self, *exc):
            return False

    def fake_stdio(params, **kwargs):
        if "errlog" in kwargs:
            captured["errlog_attempted"] = True
            raise TypeError("unexpected keyword argument 'errlog'")
        captured["called_without_errlog"] = True
        return _NoopCM()

    monkeypatch.setattr(mcp_client, "stdio_client", fake_stdio)
    ctx = mcp_client._transport_factory(cfg)

    assert captured["errlog_attempted"] is True
    assert captured["called_without_errlog"] is True
    # Non-SDK attribute attached for diagnostic surfacing (the buffer we
    # allocated still exists; it just received nothing because the SDK
    # fell back to its default stderr handler).
    assert hasattr(ctx, "_ouroboros_stderr_capture")
    assert isinstance(ctx._ouroboros_stderr_capture, io.StringIO)


# ---------------------------------------------------------------------------
# Helpers — classification smoke tests (keep the table honest)
# ---------------------------------------------------------------------------


def test_classify_handles_timeout_and_broken_pipe():
    import asyncio
    assert mcp_client._classify_mcp_error(asyncio.TimeoutError()) == "timeout"
    assert mcp_client._classify_mcp_error(BrokenPipeError(32, "broken pipe")) == "broken_pipe"
    assert mcp_client._classify_mcp_error(ConnectionRefusedError(111, "refused")) == "connection_refused"
    assert mcp_client._classify_mcp_error(PermissionError(13, "denied")) == "permission_denied"
    assert mcp_client._classify_mcp_error(FileNotFoundError(2, "no such file")) == "missing_executable"
    assert mcp_client._classify_mcp_error(RuntimeError("anything")) == "unknown"


def test_stringify_no_stderr_tail_when_buffer_empty():
    """Empty buffer produces no stderr_tail in the diagnostic."""
    inner = ValueError("plain value error")
    msg, kind, tail = mcp_client._stringify_mcp_failure(inner, stderr_buffer=io.StringIO())
    assert "ValueError" in msg
    assert "plain value error" in msg
    assert kind == "unknown"
    assert tail == ""
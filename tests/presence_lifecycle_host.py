"""Child-only TCP fixture; never point its root or control routes at an installation."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import socket
import sys
import threading


def seed(data):
    from ouroboros.presence_bindings import PresenceBinding, PresenceEndpoint, save_presence_binding
    from ouroboros.presence_capabilities import (
        PresenceSelection, PresenceState, PresenceToolTarget, presence_state_fingerprint, save_presence_state)
    from ouroboros.presence_profile import parse_presence_profile, presence_request_fingerprint
    from ouroboros.skill_loader import load_skill, save_enabled, save_review_state, SkillReviewState
    from tests.test_host_service_api import _seed_token
    from tests.test_presence_continuation_process import TOKEN

    marker = data / "fixture-binding.json"
    if marker.exists():
        return json.loads(marker.read_text())["binding"]
    _seed_token(data, skill="telegram-bot", token=TOKEN,
                permissions=["presence"], manifest_permissions=["presence"])
    folder = data / "skills" / "external" / "lifecycle-helper"
    folder.mkdir(parents=True)
    names = ["chat_history", "task_acceptance_review", "get_task_result"]
    (folder / "SKILL.md").write_text(
        "---\nname: lifecycle-helper\ndescription: Isolated report helper.\nversion: 0.1\n"
        "type: instruction\npresence:\n  instructions: Prepare reports and explicitly review substantive results.\n"
        "  capability_requests:\n" + "".join(
            f"    - id: {name}\n      kind: tool\n      required: true\n      purpose: Isolated test.\n"
            for name in names) + "---\n# Isolated helper\n")
    loaded = load_skill(folder, data)
    save_enabled(data, loaded.name, True)
    save_review_state(data, loaded.name, SkillReviewState(status="pass", content_hash=loaded.content_hash))
    profile = parse_presence_profile(loaded.manifest, folder)
    state = PresenceState(tuple(PresenceSelection(presence_request_fingerprint(request),
                                                 PresenceToolTarget("builtin", name))
                                for request, name in zip(profile.capability_requests, names)))
    save_presence_state(data, loaded.name, state, expected_state_fingerprint=presence_state_fingerprint(PresenceState()))
    binding = save_presence_binding(data, PresenceBinding(
        "b" * 32, "telegram-bot", loaded.name, PresenceEndpoint("telegram", "bot-1", "room-1", "topic-1"),
        PresenceEndpoint("telegram", "bot-1", "room-1", "topic-1"))).binding_id
    marker.write_text(json.dumps({"binding": binding}))
    return binding


def main(root, generation, mode, scenario="incoming"):
    import pytest
    import uvicorn
    from starlette.background import BackgroundTask
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from ouroboros import loop
    from ouroboros.gateway.host_service import create_host_service_app
    from ouroboros.presence_runner import run_presence_turn
    from ouroboros.task_status import reconcile_orphaned_running_tasks
    from supervisor import queue as _task_queue  # noqa: F401 -- normal supervisor import order for Panic
    from tests.test_presence_continuation_bootstrap import install_bootstrap_harness, finish, nominate
    from tests.test_presence_continuation_process import ROOT, ANSWER, MANUAL

    assert Path(os.environ["OUROBOROS_DATA_DIR"]).resolve() == (root / "data").resolve()
    assert Path(os.environ["HOME"]).resolve() == (root / "home").resolve()
    patch = pytest.MonkeyPatch()
    h = install_bootstrap_harness(patch, root / "data", repo=ROOT, enforcement=mode)
    patch.setenv("OUROBOROS_PRESENCE_MAX_ACTIVE", "1")
    binding = seed(h.data)
    reconciled = reconcile_orphaned_running_tasks(h.data)
    inner_inference = loop.call_llm_with_retry
    inference_release = threading.Event()

    def inference(llm, messages, *args, **kwargs):
        tools = args[1] if len(args) > 1 else []
        with (root / "model-inputs.jsonl").open("a") as stream:
            stream.write(json.dumps({"pid": os.getpid(), "messages": messages,
                                     "tools": [tool["function"]["name"] for tool in tools]}) + "\n")
        if scenario == "disconnect" and h.calls == 0:
            (root / "before-first-inference.json").write_text(json.dumps({"pid": os.getpid()}))
            assert inference_release.wait(30), "test failed to release first inference"
        return inner_inference(llm, messages, *args, **kwargs)

    patch.setattr(loop, "call_llm_with_retry", inference)

    def script(messages):
        if generation > 1:
            assert MANUAL in str(messages), "restart attempted inference without a fresh manual event"
            return finish("manual", message="Continued after the explicit request.")
        if h.calls > 1:
            candidate = h.agents[0].tools._ctx._delivery_candidate
            return finish("select", outcome="tool_delivered" if scenario == "tool_delivered" else "message",
                          answer_sha256=candidate.content_sha256,
                          **({"pending_review": "finish"} if mode == "advisory" else {}))
        result = nominate(ANSWER)
        if scenario == "tool_delivered":
            arguments = json.loads(result["tool_calls"][-1]["function"]["arguments"])
            arguments["outcome"] = "tool_delivered"
            result["tool_calls"][-1]["function"]["arguments"] = json.dumps(arguments)
        if mode == "advisory":
            arguments = json.loads(result["tool_calls"][-1]["function"]["arguments"])
            arguments["pending_review"] = "finish"
            result["tool_calls"][-1]["function"]["arguments"] = json.dumps(arguments)
        return result

    h.script = script

    def runner(**kwargs):
        return run_presence_turn(repo_dir=ROOT, drive_root=h.data, agent_factory=h.factory, **kwargs)

    # The proactive tool calls the module owner directly; keep its actual execution
    # manager and admission while observing the same production agent constructor.
    if scenario == "proactive":
        import functools
        patch.setattr("ouroboros.presence_runner.run_presence_turn",
                      functools.partial(run_presence_turn, agent_factory=h.factory))

    app = create_host_service_app(h.data, presence_runner=runner)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    patch.setenv("OUROBOROS_HOST_SERVICE_PORT", str(port))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))

    async def stop(request):
        from ouroboros.cancel_intents import request_cancel, STOP_POLICY_IMMEDIATE
        body = await request.json()
        request_cancel(h.data, body["task_id"], source="isolated-owner", reason="Stop parked author",
                       requested_stop_policy=STOP_POLICY_IMMEDIATE)
        return JSONResponse({"requested": True})

    async def panic(_request):
        from ouroboros.server_control import execute_panic_stop, PanicIngress
        from ouroboros import platform_layer
        # Panic's installation-wide port sweep is deliberately excluded. All other
        # owners see only this isolated child's roots; its real hard exit remains.
        def own_port_only(requested):
            assert requested == port
        patch.setattr(platform_layer, "kill_process_on_port", own_port_only)
        ingress = PanicIngress(lambda: execute_panic_stop(None, lambda: None, data_dir=h.data,
            panic_exit_code=73, log=logging.getLogger("isolated-panic"), bound_port=port))
        assert ingress.request("/panic", source="web")
        return JSONResponse({"requested": True})

    async def live(_request):
        return JSONResponse([agent.tools._ctx.task_id for agent in h.agents
                             if getattr(agent.tools._ctx, "model_wait_context", None) is not None
                             and not agent.tools._ctx.model_wait_context.closed])

    async def shutdown(_request):
        inference_release.set()
        h.release.set()
        server.should_exit = True
        return JSONResponse({"stopping": True})

    async def restart(_request):
        from ouroboros.server_control import restart_current_process

        # The real direct-server transfer replaces this image while the author
        # and review are still parked. Do not release either before exec.
        patch.setenv("OUROBOROS_SERVER_REEXEC_ARGV_JSON", json.dumps([
            "-m", "tests.presence_lifecycle_host", str(root), str(generation + 1), mode, scenario]))
        return JSONResponse({"restarting": True}, background=BackgroundTask(
            restart_current_process, "127.0.0.1", port, repo_dir=ROOT,
            log=logging.getLogger("isolated-restart"), owner_initiated=True))

    async def release_inference(_request):
        inference_release.set()
        return JSONResponse({"released": True})

    async def release_review(_request):
        h.release.set()
        return JSONResponse({"released": True})

    def initiate():
        from ouroboros.tools.presence import _initiate_presence
        from ouroboros.tools.registry import ToolContext
        # Same stable caller/dedupe identity across process recycle.
        return json.loads(_initiate_presence(
            ToolContext(repo_dir=ROOT, drive_root=h.data, task_id="isolated-proactive-caller"),
            binding, "Prepare the report.", "same-proactive-request"))

    async def proactive(_request):
        import asyncio
        return JSONResponse(await asyncio.to_thread(initiate))

    app.routes.extend([Route("/fixture/stop", stop, methods=["POST"]),
                       Route("/fixture/panic", panic, methods=["POST"]),
                       Route("/fixture/release-inference", release_inference, methods=["POST"]),
                       Route("/fixture/release-review", release_review, methods=["POST"]),
                       Route("/fixture/proactive", proactive, methods=["POST"]),
                       Route("/fixture/restart", restart, methods=["POST"]),
                       Route("/fixture/live", live), Route("/fixture/shutdown", shutdown, methods=["POST"])])
    from ouroboros.review_operation import controller_identity
    (root / f"ready-{generation}.json").write_text(json.dumps({
        "url": f"http://127.0.0.1:{port}", "binding": binding, "pid": os.getpid(),
        "controller": controller_identity(), "reconciled": reconciled}))
    try:
        server.run(sockets=[sock])
    finally:
        inference_release.set()
        h.release.set()
        if h.entered.is_set():
            assert h.settled.wait(15)
        sock.close()
        print(json.dumps({"threads": [thread.name for thread in threading.enumerate()],
                          "model_calls": h.calls, "review_calls": len(h.reviews)}), flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "incoming")

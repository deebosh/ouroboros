"""ABI 7.0 (ABI-3): per-alias removal pins for the five gateway compat aliases.

One test class per alias (F11 axes: declaration / producer / stored tolerance /
migration surface), per docs/v7next/ABI3_GATEWAY_ALIAS_INVENTORY.md. These pins
are the REMOVAL side; the read-tolerance side lives in
tests/test_cost_projection.py and the endpoint behavior in
tests/test_ui_preferences_api.py / tests/test_gateway_history.py.
"""

from __future__ import annotations

import json
from typing import get_type_hints

import pytest


class TestCostAliasRemoval:
    def test_declaration_gone_from_chat_outbound(self):
        from ouroboros.gateway.contracts import ChatOutbound

        hints = set(get_type_hints(ChatOutbound, include_extras=True))
        assert "cost_usd" not in hints
        assert "cost_usd_with_children" not in hints
        assert {"accounted_upper_bound_usd",
                "accounted_upper_bound_usd_with_children"} <= hints

    def test_ssot_emitters_never_emit_the_alias(self):
        from ouroboros.cost_projection import (
            carry_cost_meta,
            cost_projection,
            with_cost_aliases,
        )

        legacy_source = {"cost_usd": 1.0, "cost_usd_with_children": 2.0,
                         "cost_final": True}
        for out in (with_cost_aliases(legacy_source),
                    carry_cost_meta(legacy_source),
                    cost_projection(legacy_source)):
            assert "cost_usd" not in out and "cost_usd_with_children" not in out
            assert out["accounted_upper_bound_usd"] == 1.0

    def test_live_root_projection_emits_honest_names_only(self, tmp_path):
        from ouroboros.cost_projection import live_root_cost_projection

        out = live_root_cost_projection(
            "t1", {"metadata": {}}, {}, tmp_path)
        # Root with an empty ledger returns {}; a non-root returns {} — either
        # way the alias never appears. Exercise the unavailable branch too.
        assert "cost_usd" not in out and "cost_usd_with_children" not in out

    def test_admission_failure_record_stamps_the_honest_name(self):
        # gateway/tasks.py admission-failure producer switched off the alias.
        import inspect

        from ouroboros.gateway import tasks as gateway_tasks

        source = inspect.getsource(gateway_tasks)
        assert "cost_usd=0.0" not in source
        assert "accounted_upper_bound_usd=0.0" in source

    def test_stored_legacy_record_still_reads(self, tmp_path):
        from ouroboros.cost_projection import cost_projection
        from ouroboros.task_results import load_task_result, write_task_result

        # A record authored by an older release (raw legacy field passthrough).
        write_task_result(tmp_path, "legacy", "completed",
                          cost_usd=1.25, cost_final=True)
        stored = load_task_result(tmp_path, "legacy")
        assert cost_projection(stored)["accounted_upper_bound_usd"] == 1.25


class TestTelegramChatIdRemoval:
    def test_declaration_gone_from_all_outbound_frames(self):
        from ouroboros.gateway.contracts import (
            ChatOutbound,
            DocumentOutbound,
            PhotoOutbound,
            VideoOutbound,
        )

        for cls in (ChatOutbound, PhotoOutbound, VideoOutbound, DocumentOutbound):
            hints = get_type_hints(cls, include_extras=True)
            assert "telegram_chat_id" not in hints, cls.__name__
            assert "transport" in hints, cls.__name__

    def test_no_runtime_producer_left(self):
        # The history mapper was the ONLY emitter; grep-level absence pin.
        import inspect

        from ouroboros.gateway import history as gateway_history

        source = inspect.getsource(gateway_history)
        assert '"telegram_chat_id": ' not in source

    def test_legacy_stored_row_replays_without_reemitting_the_key(self, tmp_path):
        from ouroboros.gateway.history import _collect_chat_rows

        chat = tmp_path / "logs" / "chat.jsonl"
        chat.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": "2026-01-01T00:00:00+00:00", "direction": "in",
            "text": "legacy row", "type": "", "telegram_chat_id": 5,
            "chat_id": 1,
        }
        chat.write_text(json.dumps(row) + "\n", encoding="utf-8")
        rows, _quota = _collect_chat_rows(
            chat, tmp_path / "logs" / "archive", 10,
            lambda entry_chat, entry=None: True, {})
        assert len(rows) == 1
        assert rows[0]["text"] == "legacy row"
        # Stored tolerance: the legacy key is read-and-ignored — the outbound
        # history record never re-emits it (ABI-3), and replay is not rejected.
        assert "telegram_chat_id" not in rows[0]


class TestUiPreferenceAliasRemoval:
    def test_declaration_gone_from_response_contract(self):
        from ouroboros.gateway.contracts import UiPreferencesResponse

        hints = set(get_type_hints(UiPreferencesResponse, include_extras=True))
        assert "project_last_viewed" not in hints
        assert "project_hidden" not in hints
        assert "project_seen_revision" in hints

    def test_defaults_and_known_keys_dropped_the_aliases(self):
        from ouroboros.gateway.ui_preferences import DEFAULT_UI_PREFERENCES

        assert "project_last_viewed" not in DEFAULT_UI_PREFERENCES
        assert "project_hidden" not in DEFAULT_UI_PREFERENCES

    def test_stored_legacy_keys_are_ignored_not_fatal(self, tmp_path):
        from ouroboros.gateway.ui_preferences import _normalize_preferences

        prefs = _normalize_preferences({
            "widget_order": ["a"],
            "project_last_viewed": {"p": "2026-01-01T00:00:00Z"},
            "project_hidden": {"p": True},
        })
        assert prefs["widget_order"] == ["a"]
        assert "project_last_viewed" not in prefs
        assert "project_hidden" not in prefs


class TestAliasProducerFanOutSweep:
    """Ф3.1 fix-round: fan-out-complete producer pin over the WHOLE runtime
    tree (every ``ouroboros/**/*.py`` and ``supervisor/**/*.py``).

    No production code emits a retired gateway alias key in an emission-shaped
    AST position — a dict-literal key, a subscript assignment, or a keyword
    argument on a ``write_task_result`` call (the durable ABI-3 store).
    Legacy READS stay legal and are naturally invisible to this scan:
    ``resolve_cost_pair``/``.get``/``in``/``.pop`` never author a key.

    The allowlist names the ONLY surviving dict-key/subscript occurrences:
    fields of INTERNAL non-gateway planes that merely share a spelling with
    the retired ChatOutbound/task-result aliases (physical usage ledger rows,
    llm/usage observability events, review/evidence receipts, subagent
    envelope, evolution campaign state, custody settlement events, reflection
    records). None of them is a task-result, chat-frame or gateway-response
    producer, and the ``write_task_result`` kwarg check has NO allowlist at
    all. A stale allowlist entry (nothing matches it any more) FAILS the test,
    so the list can only shrink honestly.
    """

    RETIRED_ALIASES = frozenset({
        "cost_usd", "cost_usd_with_children", "telegram_chat_id",
        "project_last_viewed", "project_hidden",
    })
    # (posix path, alias) -> why this INTERNAL plane legitimately keeps the spelling.
    INTERNAL_PLANE_ALLOWLIST = {
        # physical usage ledger rows / legacy usage import (P7 monetary authority)
        ("ouroboros/usage_accounting.py", "cost_usd"): "ledger settlement row schema",
        ("ouroboros/usage_legacy_import.py", "cost_usd"): "legacy usage.json ledger import rows",
        # usage/observability event streams (events.jsonl, live log frames)
        ("ouroboros/loop_llm_call.py", "cost_usd"): "llm_round usage event rows",
        ("ouroboros/outcomes.py", "cost_usd"): "nested usage sub-dict (usage-row schema)",
        ("ouroboros/post_task_synthesis.py", "cost_usd"): "chat_block_consolidation event row",
        ("ouroboros/consciousness.py", "cost_usd"): "consciousness thought receipt row",
        ("supervisor/events_evolution_done.py", "cost_usd"): "supervisor.jsonl observability row",
        # review/evidence receipt schemas (internal review plane)
        ("ouroboros/triad_review.py", "cost_usd"): "triad review receipt",
        ("ouroboros/skill_loader.py", "cost_usd"): "skill review outcome receipt",
        ("ouroboros/tools/delegate_terminal_evidence.py", "cost_usd"): "delegate terminal evidence rows",
        ("ouroboros/tools/preflight_review_run.py", "cost_usd"): "advisory preflight receipts",
        ("ouroboros/tools/review_admission.py", "cost_usd"): "review admission receipt",
        ("ouroboros/tools/review_helpers.py", "cost_usd"): "review usage receipt",
        ("ouroboros/tools/scope_review.py", "cost_usd"): "scope review receipt",
        # subagent envelope (nested schema, its own producer/reader pair)
        ("ouroboros/subagents.py", "cost_usd"): "subagent envelope field",
        ("ouroboros/agent_task_pipeline.py", "cost_usd"): "subagent envelope patch",
        # evolution campaign/checkpoint plane
        ("ouroboros/evolution_checkpoints.py", "cost_usd"): "evolution checkpoint records",
        ("supervisor/evolution_lifecycle.py", "cost_usd"): "evolution campaign history rows",
        # custody settlement events
        ("ouroboros/delegate_custody.py", "cost_usd"): "custody SETTLED event row",
        # reflection records
        ("ouroboros/reflection.py", "cost_usd"): "task reflection record",
    }

    @staticmethod
    def _emission_hits():
        import ast
        import pathlib

        repo_root = pathlib.Path(__file__).resolve().parents[1]
        aliases = TestAliasProducerFanOutSweep.RETIRED_ALIASES
        dict_hits: list = []
        writer_kwarg_hits: list = []
        for package in ("ouroboros", "supervisor"):
            for path in sorted((repo_root / package).rglob("*.py")):
                rel = path.relative_to(repo_root).as_posix()
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Dict):
                        for key in node.keys:
                            if isinstance(key, ast.Constant) and key.value in aliases:
                                dict_hits.append((rel, key.value, key.lineno))
                    elif isinstance(node, (ast.Assign, ast.AugAssign)):
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                        for target in targets:
                            if (
                                isinstance(target, ast.Subscript)
                                and isinstance(target.slice, ast.Constant)
                                and target.slice.value in aliases
                            ):
                                dict_hits.append((rel, target.slice.value, target.lineno))
                    elif isinstance(node, ast.Call):
                        func = node.func
                        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                        if name != "write_task_result":
                            continue
                        for kw in node.keywords:
                            if kw.arg in aliases:
                                writer_kwarg_hits.append((rel, kw.arg, node.lineno))
                        for arg in [kw.value for kw in node.keywords if kw.arg is None] + list(node.args):
                            if isinstance(arg, ast.Dict):
                                for key in arg.keys:
                                    if isinstance(key, ast.Constant) and key.value in aliases:
                                        writer_kwarg_hits.append((rel, key.value, node.lineno))
        return dict_hits, writer_kwarg_hits

    def test_no_task_result_writer_passes_a_retired_alias(self):
        _, writer_kwarg_hits = self._emission_hits()
        assert writer_kwarg_hits == [], (
            "write_task_result call sites must stamp honest names only "
            f"(ABI-3); offending sites: {writer_kwarg_hits!r}"
        )

    def test_every_alias_key_emission_is_an_allowlisted_internal_plane(self):
        dict_hits, _ = self._emission_hits()
        unexpected = [
            hit for hit in dict_hits
            if (hit[0], hit[1]) not in self.INTERNAL_PLANE_ALLOWLIST
        ]
        assert unexpected == [], (
            "new emission-shaped occurrence of a retired gateway alias; either "
            "cut the producer over to the honest name or (only for a genuinely "
            f"internal non-gateway plane) extend the allowlist: {unexpected!r}"
        )
        matched = {(rel, alias) for rel, alias, _lineno in dict_hits}
        stale = sorted(set(self.INTERNAL_PLANE_ALLOWLIST) - matched)
        assert stale == [], (
            f"stale allowlist rows (no emission matches them any more): {stale!r}"
        )

    def test_no_gateway_alias_survives_outside_the_cost_pair(self):
        """The three non-cost aliases have zero emission-shaped occurrences at
        all — no allowlist, no exceptions."""
        dict_hits, _ = self._emission_hits()
        non_cost = [
            hit for hit in dict_hits
            if hit[1] in {"telegram_chat_id", "project_last_viewed", "project_hidden"}
        ]
        assert non_cost == []


class TestApiV1ShimRemoval:
    def test_module_is_gone(self):
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("ouroboros.contracts.api_v1")

    def test_gateway_contracts_is_the_sole_ssot(self):
        from ouroboros.gateway import contracts

        assert "ChatOutbound" in contracts.__all__
        assert "HTTP_ENDPOINTS" in contracts.__all__

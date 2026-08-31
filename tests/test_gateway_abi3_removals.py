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


class TestApiV1ShimRemoval:
    def test_module_is_gone(self):
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("ouroboros.contracts.api_v1")

    def test_gateway_contracts_is_the_sole_ssot(self):
        from ouroboros.gateway import contracts

        assert "ChatOutbound" in contracts.__all__
        assert "HTTP_ENDPOINTS" in contracts.__all__

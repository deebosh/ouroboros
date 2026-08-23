"""Tests for ouroboros.deep_self_review module."""

from __future__ import annotations

import os
import pathlib
from unittest import mock

import pytest

from ouroboros.provider_models import OPENAI_DIRECT_DEFAULTS
from ouroboros.deep_self_review import (
    build_review_pack,
    is_review_available,
    run_deep_self_review,
)
from ouroboros.tools.review_helpers import _is_probably_binary


def _make_dulwich_mock(file_list: list[str]):
    """Return a mock for dulwich.repo.Repo that yields the given file list from open_index()."""
    mock_index = mock.Mock()
    mock_index.__iter__ = mock.Mock(return_value=iter(f.encode() for f in file_list))
    mock_repo = mock.Mock()
    mock_repo.open_index.return_value = mock_index
    mock_repo_cls = mock.Mock(return_value=mock_repo)
    return mock_repo_cls


@pytest.fixture
def tmp_repo(tmp_path):
    """Create a minimal git repo with tracked files."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (repo / "lib.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    return repo


@pytest.fixture
def tmp_drive(tmp_path):
    """Create a drive root with some memory files."""
    drive = tmp_path / "drive"
    drive.mkdir()
    mem = drive / "memory"
    mem.mkdir()
    (mem / "identity.md").write_text("I am Ouroboros.\n", encoding="utf-8")
    (mem / "scratchpad.md").write_text("Working notes.\n", encoding="utf-8")
    know = mem / "knowledge"
    know.mkdir()
    (know / "patterns.md").write_text("## Patterns\n- Error class A\n", encoding="utf-8")
    return drive


class TestBuildReviewPack:
    def test_reads_tracked_files(self, tmp_repo, tmp_drive):
        """git ls-files output determines which repo files are included."""
        with mock.patch("dulwich.repo.Repo", _make_dulwich_mock(["main.py", "lib.py"])):
            pack, stats = build_review_pack(tmp_repo, tmp_drive)

        assert "### main.py" in pack
        assert "### lib.py" in pack
        assert "print('hello')" in pack
        assert stats["file_count"] >= 2

        atlas = mock.Mock(
            status="budget_exceeded",
            manifest={"estimated_total_tokens": 950_000},
            omitted=(),
            selected=(),
            text="small atlas",
        )
        with (
            mock.patch("dulwich.repo.Repo", _make_dulwich_mock(["main.py"])),
            mock.patch("ouroboros.deep_self_review.compile_review_context_atlas", return_value=atlas),
        ):
            pack, stats = build_review_pack(tmp_repo, tmp_drive)
        assert pack == ""
        assert "exceeded hard budget" in stats["skipped"][0]
        assert stats["context_manifest"]["estimated_total_tokens"] == 950_000

    def test_includes_memory_whitelist(self, tmp_repo, tmp_drive):
        """Memory whitelist files from drive_root are included."""
        with mock.patch("dulwich.repo.Repo", _make_dulwich_mock(["main.py"])):
            pack, stats = build_review_pack(tmp_repo, tmp_drive)

        assert "## FILE: drive/memory/identity.md" in pack
        assert "I am Ouroboros." in pack
        assert "## FILE: drive/memory/scratchpad.md" in pack
        assert "## FILE: drive/memory/knowledge/patterns.md" in pack

    def test_includes_improvement_backlog_when_present(self, tmp_repo, tmp_drive):
        (tmp_drive / "memory" / "knowledge" / "improvement-backlog.md").write_text(
            "# Improvement Backlog\n\n### ibl-1\n- summary: Fix recurring review blocker\n",
            encoding="utf-8",
        )
        with mock.patch("dulwich.repo.Repo", _make_dulwich_mock(["main.py"])):
            pack, _stats = build_review_pack(tmp_repo, tmp_drive)

        assert "## FILE: drive/memory/knowledge/improvement-backlog.md" in pack
        assert "Fix recurring review blocker" in pack

    def test_skips_missing_memory(self, tmp_repo, tmp_drive):
        """Missing memory files are silently skipped."""
        with mock.patch("dulwich.repo.Repo", _make_dulwich_mock(["main.py"])):
            pack, stats = build_review_pack(tmp_repo, tmp_drive)

        # registry.md, WORLD.md, index-full.md don't exist — should not appear
        assert "registry.md" not in pack
        assert "WORLD.md" not in pack
        assert "index-full.md" not in pack


class TestIsReviewAvailable:
    def test_openrouter(self):
        with (
            mock.patch("ouroboros.deep_self_review.get_deep_self_review_model", return_value="openai/gpt-5.5-pro"),
            mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test"}, clear=True),
        ):
            available, model = is_review_available()
        assert available is True
        assert model == "openai/gpt-5.5-pro"

    def test_openai(self):
        env = {"OPENAI_API_KEY": "sk-test"}
        with mock.patch.dict(os.environ, env, clear=False):
            # Ensure OPENROUTER_API_KEY and OPENAI_BASE_URL are not set
            os.environ.pop("OPENROUTER_API_KEY", None)
            os.environ.pop("OPENAI_BASE_URL", None)
            available, model = is_review_available()
        assert available is True
        # The direct route lands on the PROVIDER default, not a mechanical
        # `openai::` + router-slug rewrite: `-pro` is an OpenRouter routing slug
        # that 404s on api.openai.com (live-probed 2026-07-29).
        assert model == OPENAI_DIRECT_DEFAULTS["deep_self_review"]
        assert not model.endswith("-pro")

    def test_none(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            available, model = is_review_available()
        assert available is False
        assert model is None

    def test_direct_provider_prefix_requires_matching_key_even_with_openrouter(self):
        with (
            mock.patch("ouroboros.deep_self_review.get_deep_self_review_model", return_value="anthropic::claude-opus-4.8"),
            mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test"}, clear=True),
        ):
            available, model = is_review_available()

        assert available is False
        assert model is None

    def test_direct_provider_prefix_available_with_matching_key(self):
        with (
            mock.patch("ouroboros.deep_self_review.get_deep_self_review_model", return_value="anthropic::claude-opus-4.8"),
            mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}, clear=True),
        ):
            available, model = is_review_available()

        assert available is True
        assert model == "anthropic::claude-opus-4.8"

    def test_openai_direct_preferred_when_both_keys_set(self):
        """Regression for ibl-ad4731a2f03e: when both OPENAI_API_KEY and
        OPENROUTER_API_KEY are set, an OpenRouter cascade must NOT shadow a
        working direct-OpenAI route (the openrouter credit cascade is silent
        at the provider level — openrouter looks healthy then 402s mid-call).
        """
        with (
            mock.patch("ouroboros.deep_self_review.get_deep_self_review_model", return_value="openai/gpt-5.5-pro"),
            mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test", "OPENROUTER_API_KEY": "sk-or-test"}, clear=True),
        ):
            available, model = is_review_available()
        assert available is True
        # The direct-OpenAI rewrite path wins; the result must NOT be the
        # raw OpenRouter route (`openai/gpt-5.5-pro`) that the bug returned.
        assert model != "openai/gpt-5.5-pro"
        assert model == OPENAI_DIRECT_DEFAULTS["deep_self_review"]

    def test_openrouter_used_when_openai_direct_unavailable(self):
        """The fix must preserve the openrouter fallback when openai direct is
        NOT available (no OPENAI_API_KEY) — openrouter is the only path.
        """
        with (
            mock.patch("ouroboros.deep_self_review.get_deep_self_review_model", return_value="openai/gpt-5.5"),
            mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test"}, clear=True),
        ):
            available, model = is_review_available()
        assert available is True
        # Falls through to openrouter since openai direct is unreachable.
        assert model == "openai/gpt-5.5"


class TestRequestToolEmitsEvent:
    def test_emits_correct_event(self):
        """_request_deep_self_review emits a deep_self_review_request event."""
        from ouroboros.tools.control import _request_deep_self_review

        class FakeCtx:
            pending_events = []

        ctx = FakeCtx()
        with mock.patch(
            "ouroboros.deep_self_review.is_review_available",
            return_value=(True, "openai/gpt-5.5-pro"),
        ):
            result = _request_deep_self_review(ctx, "test reason")
        assert len(ctx.pending_events) == 1
        evt = ctx.pending_events[0]
        assert evt["type"] == "deep_self_review_request"
        assert evt["reason"] == "test reason"
        assert evt["model"] == "openai/gpt-5.5-pro"
        assert "Deep self-review" in result

    def test_unavailable_returns_error(self):
        """When no API key is available, returns error without emitting event."""
        from ouroboros.tools.control import _request_deep_self_review

        class FakeCtx:
            pending_events = []

        ctx = FakeCtx()
        with mock.patch(
            "ouroboros.deep_self_review.is_review_available",
            return_value=(False, None),
        ):
            result = _request_deep_self_review(ctx, "test reason")
        assert len(ctx.pending_events) == 0
        assert "unavailable" in result


class TestVendoredFilesExcluded:
    def test_minified_js_skipped(self, tmp_repo, tmp_drive):
        """Files with .min.js suffix are excluded from the review pack."""
        (tmp_repo / "lib.min.js").write_text("!function(){var a=1;}()\n")
        with mock.patch("dulwich.repo.Repo", _make_dulwich_mock(["main.py", "lib.min.js"])):
            pack, stats = build_review_pack(tmp_repo, tmp_drive)

        assert "vendored_minified" in str(stats["skipped"])
        assert "## FILE: lib.min.js" not in pack

    def test_chart_umd_skipped(self, tmp_repo, tmp_drive):
        """chart.umd.min.js (vendored Chart.js) is excluded by name and appears in OMITTED section."""
        (tmp_repo / "chart.umd.min.js").write_text("!function(t,e){/* chart.js minified */}()\n")
        with mock.patch("dulwich.repo.Repo", _make_dulwich_mock(["main.py", "chart.umd.min.js"])):
            pack, stats = build_review_pack(tmp_repo, tmp_drive)

        assert "## FILE: chart.umd.min.js" not in pack
        assert any("chart.umd.min.js" in s for s in stats["skipped"])
        # Omission section must be present and mention the file
        assert "## OMITTED FILES" in pack
        assert "chart.umd.min.js" in pack

    def test_min_css_skipped(self, tmp_repo, tmp_drive):
        """Files with .min.css suffix are excluded."""
        (tmp_repo / "style.min.css").write_text("body{margin:0}a{color:red}\n")
        with mock.patch("dulwich.repo.Repo", _make_dulwich_mock(["main.py", "style.min.css"])):
            pack, stats = build_review_pack(tmp_repo, tmp_drive)

        assert "## FILE: style.min.css" not in pack
        assert any("style.min.css" in s for s in stats["skipped"])

    def test_regular_js_included(self, tmp_repo, tmp_drive):
        """Regular (non-minified) JS files are NOT excluded."""
        (tmp_repo / "app.js").write_text("console.log('hello');\n")
        with mock.patch("dulwich.repo.Repo", _make_dulwich_mock(["main.py", "app.js"])):
            pack, stats = build_review_pack(tmp_repo, tmp_drive)

        assert "### app.js" in pack
        assert "console.log('hello');" in pack
        assert not any("app.js" in s for s in stats["skipped"])

    def test_omission_section_after_memory_whitelist(self, tmp_repo, tmp_drive):
        """OMITTED FILES section is appended after both repo and memory passes, capturing all skips.

        Simulates a memory-whitelist read error by patching pathlib.Path.read_text so that
        identity.md raises PermissionError, ensuring it lands in skipped and the OMITTED section.
        """
        (tmp_repo / "lib.min.js").write_text("minified\n")
        (tmp_drive / "memory" / "identity.md").write_text("I am Ouroboros.\n")
        target_path = str(tmp_drive / "memory" / "identity.md")

        original_read_text = pathlib.Path.read_text

        def patched_read_text(self, encoding="utf-8", errors="replace"):
            if str(self) == target_path:
                raise PermissionError("mocked read error")
            return original_read_text(self, encoding=encoding, errors=errors)

        with mock.patch("dulwich.repo.Repo", _make_dulwich_mock(["main.py", "lib.min.js"])):
            with mock.patch("pathlib.Path.read_text", patched_read_text):
                pack, stats = build_review_pack(tmp_repo, tmp_drive)

        assert "## OMITTED FILES" in pack
        omitted_section_pos = pack.index("## OMITTED FILES")
        # Vendored file listed in omitted section
        assert "lib.min.js" in pack[omitted_section_pos:]
        # Memory read error captured in skipped
        memory_errors = [s for s in stats["skipped"] if "identity.md" in s and "read error" in s]
        assert memory_errors, "identity.md read error should appear in skipped"
        # And it appears in the OMITTED section too
        assert "identity.md" in pack[omitted_section_pos:]


class TestIsProbablyBinary:
    def test_nul_byte_is_binary(self, tmp_path):
        """File containing a NUL byte is detected as binary."""
        f = tmp_path / "blob.bin"
        f.write_bytes(b"some text\x00more text")
        assert _is_probably_binary(f) is True

    def test_plain_text_is_not_binary(self, tmp_path):
        """Plain text file is not detected as binary."""
        f = tmp_path / "script.py"
        f.write_text("def hello():\n    return 'world'\n")
        assert _is_probably_binary(f) is False

    def test_high_non_printable_ratio_is_binary(self, tmp_path):
        """File with >30% non-printable bytes (ASCII control range) is detected as binary."""
        # 40% non-printable (bytes 1–8 range, ASCII control chars)
        payload = bytes(range(1, 9)) * 10 + b"normal text" * 3
        f = tmp_path / "data.unknown"
        f.write_bytes(payload)
        assert _is_probably_binary(f) is True

    def test_high_byte_ratio_is_binary(self, tmp_path):
        """File with invalid UTF-8 high bytes (no NUL) is detected as binary.

        bytes >= 128 alone are safe for valid UTF-8 (Cyrillic, CJK), but
        invalid UTF-8 sequences (e.g. raw Latin-1 bytes 0x80-0xFF) must still
        be caught by the incremental UTF-8 decode check.
        """
        # Raw Latin-1 bytes 0x80-0xFF: invalid UTF-8, no NUL, few control chars
        payload = bytes(range(128, 256)) * 5 + b"ascii text" * 5
        f = tmp_path / "data.blob"
        f.write_bytes(payload)
        assert _is_probably_binary(f) is True

    def test_only_first_sniff_bytes_read(self, tmp_path):
        """_is_probably_binary only reads _BINARY_SNIFF_BYTES bytes, not the whole file."""
        from ouroboros.tools.review_helpers import _BINARY_SNIFF_BYTES
        # File is mostly text but has NUL in the first 8KB window
        payload = b"text data\x00more" + b"a" * (_BINARY_SNIFF_BYTES * 2)
        f = tmp_path / "big.bin"
        f.write_bytes(payload)
        # Should detect NUL in the first chunk and return True
        assert _is_probably_binary(f) is True

    def test_empty_file_is_not_binary(self, tmp_path):
        """Empty file does not crash and returns False."""
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        assert _is_probably_binary(f) is False

    def test_missing_file_returns_false(self, tmp_path):
        """Missing file returns False (let caller handle read failure)."""
        f = tmp_path / "does_not_exist.bin"
        assert _is_probably_binary(f) is False

    def test_unlisted_extension_binary_excluded_from_pack(self, tmp_repo, tmp_drive):
        """Binary file with unlisted extension (.bin) is excluded via content sniffer."""
        (tmp_repo / "model.bin").write_bytes(b"GGUF\x00" + b"\x00\xff" * 100)
        with mock.patch("dulwich.repo.Repo", _make_dulwich_mock(["main.py", "model.bin"])):
            pack, stats = build_review_pack(tmp_repo, tmp_drive)

        assert "## FILE: model.bin" not in pack
        assert any("model.bin" in s for s in stats["skipped"])


class TestBinaryFilesExcluded:
    def test_png_skipped(self, tmp_repo, tmp_drive):
        """PNG images are excluded — reading them produces garbage replacement chars."""
        (tmp_repo / "screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        with mock.patch("dulwich.repo.Repo", _make_dulwich_mock(["main.py", "screenshot.png"])):
            pack, stats = build_review_pack(tmp_repo, tmp_drive)

        assert "## FILE: screenshot.png" not in pack
        assert any("screenshot.png" in s for s in stats["skipped"])

    def test_jpg_skipped(self, tmp_repo, tmp_drive):
        """JPEG images are excluded."""
        (tmp_repo / "logo.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)
        with mock.patch("dulwich.repo.Repo", _make_dulwich_mock(["main.py", "logo.jpg"])):
            pack, stats = build_review_pack(tmp_repo, tmp_drive)

        assert "## FILE: logo.jpg" not in pack
        assert any("logo.jpg" in s for s in stats["skipped"])

    def test_svg_skipped(self, tmp_repo, tmp_drive):
        """SVG files are excluded (provider icons can be large XML)."""
        (tmp_repo / "icon.svg").write_text("<svg><circle r='10'/></svg>\n")
        with mock.patch("dulwich.repo.Repo", _make_dulwich_mock(["main.py", "icon.svg"])):
            pack, stats = build_review_pack(tmp_repo, tmp_drive)

        assert "## FILE: icon.svg" not in pack
        assert any("icon.svg" in s for s in stats["skipped"])

    def test_ico_skipped(self, tmp_repo, tmp_drive):
        """ICO files are excluded."""
        (tmp_repo / "favicon.ico").write_bytes(b"\x00\x00\x01\x00" + b"\x00" * 50)
        with mock.patch("dulwich.repo.Repo", _make_dulwich_mock(["main.py", "favicon.ico"])):
            pack, stats = build_review_pack(tmp_repo, tmp_drive)

        assert "## FILE: favicon.ico" not in pack
        assert any("favicon.ico" in s for s in stats["skipped"])

    def test_python_source_not_skipped(self, tmp_repo, tmp_drive):
        """Python source files (.py) are NOT excluded by the binary filter."""
        with mock.patch("dulwich.repo.Repo", _make_dulwich_mock(["main.py"])):
            pack, stats = build_review_pack(tmp_repo, tmp_drive)

        assert "### main.py" in pack


class TestSkipDirPrefixes:
    def test_assets_dir_excluded(self, tmp_repo, tmp_drive):
        """Files under assets/ are excluded (README screenshots, app icons)."""
        assets = tmp_repo / "assets"
        assets.mkdir()
        (assets / "chat.png").write_bytes(b"\x89PNG\r\n" + b"\x00" * 100)
        (assets / "logo.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)
        with mock.patch("dulwich.repo.Repo", _make_dulwich_mock(["main.py", "assets/chat.png", "assets/logo.jpg"])):
            pack, stats = build_review_pack(tmp_repo, tmp_drive)

        assert "## FILE: assets/chat.png" not in pack
        assert "## FILE: assets/logo.jpg" not in pack
        assert any("assets/chat.png" in s for s in stats["skipped"])
        assert any("assets/logo.jpg" in s for s in stats["skipped"])
        assert "### main.py" in pack  # non-assets file still present

    def test_web_dir_not_excluded(self, tmp_repo, tmp_drive):
        """Files under web/ (SPA modules) are NOT excluded."""
        web = tmp_repo / "web" / "modules"
        web.mkdir(parents=True)
        (web / "chat.js").write_text("// chat module\n")
        with mock.patch("dulwich.repo.Repo", _make_dulwich_mock(["main.py", "web/modules/chat.js"])):
            pack, stats = build_review_pack(tmp_repo, tmp_drive)

        assert "### web/modules/chat.js" in pack
        assert not any("web/modules/chat.js" in s for s in stats["skipped"])


class TestNoProxyLlmChat:
    """LLMClient.chat(no_proxy=True) — proxy-free httpx transport for macOS fork-safety."""

    def test_chat_no_proxy_uses_trust_env_false(self):
        """chat(no_proxy=True) builds an httpx.Client with trust_env=False and mounts={}."""
        import httpx
        from ouroboros.llm import LLMClient

        captured_clients = []

        real_httpx_client = httpx.Client

        def capturing_httpx_client(*args, **kwargs):
            c = real_httpx_client(*args, **kwargs)
            captured_clients.append(c)
            return c

        llm = LLMClient()
        mock_resp = mock.Mock()
        mock_resp.model_dump.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

        with mock.patch("httpx.Client", side_effect=capturing_httpx_client):
            with mock.patch("openai.OpenAI") as mock_openai_cls:
                mock_oa = mock.Mock()
                mock_oa.chat.completions.create.return_value = mock_resp
                mock_openai_cls.return_value = mock_oa

                with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test"}, clear=False):
                    llm.chat(
                        messages=[{"role": "user", "content": "hi"}],
                        model="openai/gpt-5.5-pro",
                        max_tokens=8,
                        no_proxy=True,
                    )

        # At least one httpx.Client was created
        assert len(captured_clients) >= 1
        created = captured_clients[0]
        # trust_env=False and mounts={} are the key invariants
        assert created._mounts == {} or not created._mounts

    def test_chat_no_proxy_closes_http_client(self):
        """chat(no_proxy=True) closes the one-shot httpx.Client after the call."""
        import httpx
        from ouroboros.llm import LLMClient

        closed_clients = []
        real_httpx_client = httpx.Client

        class TrackingClient(real_httpx_client):
            def close(self):
                closed_clients.append(self)
                super().close()

        llm = LLMClient()
        mock_resp = mock.Mock()
        mock_resp.model_dump.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

        with mock.patch("httpx.Client", TrackingClient):
            with mock.patch("openai.OpenAI") as mock_openai_cls:
                mock_oa = mock.Mock()
                mock_oa.chat.completions.create.return_value = mock_resp
                mock_openai_cls.return_value = mock_oa

                with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test"}, clear=False):
                    llm.chat(
                        messages=[{"role": "user", "content": "hi"}],
                        model="openai/gpt-5.5-pro",
                        max_tokens=8,
                        no_proxy=True,
                    )

        assert len(closed_clients) >= 1, "httpx.Client must be closed after no_proxy call"

    def test_chat_no_proxy_closes_on_exception(self):
        """chat(no_proxy=True) closes the http client even when the API call raises."""
        import httpx
        from ouroboros.llm import LLMClient

        closed_clients = []
        real_httpx_client = httpx.Client

        class TrackingClient(real_httpx_client):
            def close(self):
                closed_clients.append(self)
                super().close()

        llm = LLMClient()

        with mock.patch("httpx.Client", TrackingClient):
            with mock.patch("openai.OpenAI") as mock_openai_cls:
                mock_oa = mock.Mock()
                mock_oa.chat.completions.create.side_effect = RuntimeError("boom")
                mock_openai_cls.return_value = mock_oa

                with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test"}, clear=False):
                    with pytest.raises(RuntimeError, match="boom"):
                        llm.chat(
                            messages=[{"role": "user", "content": "hi"}],
                            model="openai/gpt-5.5-pro",
                            max_tokens=8,
                            no_proxy=True,
                        )

        assert len(closed_clients) >= 1, "httpx.Client must be closed even after exception"

    def test_chat_no_proxy_skips_generation_cost_fetch(self):
        """chat(no_proxy=True) does not call _fetch_generation_cost (proxy/OS path)."""
        from ouroboros.llm import LLMClient

        llm = LLMClient()
        mock_resp = mock.Mock()
        mock_resp.model_dump.return_value = {
            "id": "gen-abc123",  # has a generation id — would trigger cost fetch normally
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

        with mock.patch("httpx.Client") as mock_httpx_cls:
            mock_http = mock.Mock()
            mock_httpx_cls.return_value = mock_http
            with mock.patch("openai.OpenAI") as mock_openai_cls:
                mock_oa = mock.Mock()
                mock_oa.chat.completions.create.return_value = mock_resp
                mock_openai_cls.return_value = mock_oa
                with mock.patch.object(llm, "_fetch_generation_cost") as mock_cost:
                    with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test"}, clear=False):
                        llm.chat(
                            messages=[{"role": "user", "content": "hi"}],
                            model="openai/gpt-5.5-pro",
                            max_tokens=8,
                            no_proxy=True,
                        )
                    mock_cost.assert_not_called()

    def test_chat_no_proxy_false_uses_cached_client(self):
        """chat(no_proxy=False, default) uses the shared cached client, not a new one."""
        import httpx
        from ouroboros.llm import LLMClient

        new_clients = []
        real_httpx_client = httpx.Client

        def counting_httpx_client(*args, **kwargs):
            c = real_httpx_client(*args, **kwargs)
            new_clients.append(c)
            return c

        llm = LLMClient()
        mock_resp = mock.Mock()
        mock_resp.model_dump.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

        with mock.patch("httpx.Client", side_effect=counting_httpx_client):
            with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test"}, clear=False):
                with mock.patch.object(llm, "_get_remote_client") as mock_get:
                    mock_oa = mock.Mock()
                    mock_oa.chat.completions.create.return_value = mock_resp
                    mock_get.return_value = mock_oa
                    llm.chat(
                        messages=[{"role": "user", "content": "hi"}],
                        model="openai/gpt-5.5-pro",
                        max_tokens=8,
                        no_proxy=False,
                    )
                    mock_get.assert_called_once()

        # no_proxy=False must not construct a new httpx.Client
        assert len(new_clients) == 0

    def test_run_deep_self_review_calls_llm_with_no_proxy_and_configured_effort(self, tmp_repo, tmp_drive, monkeypatch):
        """run_deep_self_review passes no_proxy=True to llm.chat."""
        from ouroboros.deep_self_review import run_deep_self_review
        small_pack = "x" * 50_000
        manifest = {"status": "ok", "selected_count": 1}
        mock_llm = mock.Mock()
        mock_llm.chat.return_value = ({"content": "Review result. See memory/identity.md, memory/scratchpad.md, memory/registry.md."}, {"cost": 0.01})
        monkeypatch.setenv("OUROBOROS_EFFORT_DEEP_SELF_REVIEW", "medium")

        with mock.patch(
            "ouroboros.deep_self_review.build_review_pack",
            return_value=(
                small_pack,
                {
                    "file_count": 5,
                    "memory_count": 3,
                    "total_chars": len(small_pack),
                    "skipped": [],
                    "context_manifest": manifest,
                },
            ),
        ):
            result, usage = run_deep_self_review(
                repo_dir=tmp_repo,
                drive_root=tmp_drive,
                llm=mock_llm,
                emit_progress=lambda x: None,
                event_queue=None,
                model="openai/gpt-5.5-pro",
            )

        assert result == "Review result. See memory/identity.md, memory/scratchpad.md, memory/registry.md."
        mock_llm.chat.assert_called_once()
        _, kwargs = mock_llm.chat.call_args
        assert kwargs.get("no_proxy") is True, "llm.chat must be called with no_proxy=True"
        assert kwargs.get("reasoning_effort") == "medium"
        sidecar = tmp_drive / "state" / "deep_self_review_context.json"
        assert sidecar.is_file()
        assert '"context_manifest"' in sidecar.read_text(encoding="utf-8")
        assert '"selected_count": 1' in sidecar.read_text(encoding="utf-8")


class TestReviewPackOverflow:
    def test_overflow_shrinks_and_proceeds(self, tmp_repo, tmp_drive):
        """An estimator-drift overshoot triggers ONE tighter rebuild, then the
        review proceeds — the historical '+853 tokens' fatal error class."""
        huge_pack = "x" * 4_000_000  # > 745K-token gate
        small_pack = "y" * 4_000     # comfortably under
        mock_llm = mock.Mock()
        mock_llm.chat.return_value = ({"content": "Review result. See memory/identity.md, memory/scratchpad.md, memory/registry.md."}, {"cost": 0.0})
        build_calls = []

        def fake_build(repo_dir, drive_root, fixed_prompt_tokens=0, hard_budget_reduction=0, input_token_limit=0):
            build_calls.append(hard_budget_reduction)
            if hard_budget_reduction:
                return small_pack, {"file_count": 5, "memory_count": 3, "total_chars": len(small_pack), "skipped": []}
            return huge_pack, {"file_count": 100, "memory_count": 3, "total_chars": len(huge_pack), "skipped": []}

        with (
            mock.patch("ouroboros.deep_self_review.build_review_pack", side_effect=fake_build),
            mock.patch(
                "ouroboros.llm_observability.chat_observed",
                return_value=({"content": "Review result. See memory/identity.md, memory/scratchpad.md, memory/registry.md."}, {"cost": 0.0}),
            ),
        ):
            result, _usage = run_deep_self_review(
                repo_dir=tmp_repo,
                drive_root=tmp_drive,
                llm=mock_llm,
                emit_progress=lambda x: None,
                event_queue=None,
                model="test-model",
            )

        assert result == "Review result. See memory/identity.md, memory/scratchpad.md, memory/registry.md."
        assert len(build_calls) == 2, "must rebuild once with a tighter budget"
        assert build_calls[1] > 0, "retry must reduce the atlas hard budget"

    def test_explicit_error_when_shrink_cannot_fit(self, tmp_repo, tmp_drive):
        """If even the tighter rebuild stays over the gate, fail closed with the
        explicit error (the pinned last-resort assertion)."""
        huge_pack = "x" * 4_000_000
        mock_llm = mock.Mock()

        with mock.patch(
            "ouroboros.deep_self_review.build_review_pack",
            return_value=(huge_pack, {"file_count": 100, "memory_count": 3, "total_chars": 4_000_000, "skipped": []}),
        ):
            result, usage = run_deep_self_review(
                repo_dir=tmp_repo,
                drive_root=tmp_drive,
                llm=mock_llm,
                emit_progress=lambda x: None,
                event_queue=None,
                model="test-model",
            )

        assert "too large" in result
        # v6.80.0: the deep reviewer's cap is DENSITY-calibrated per model at call
        # time (the module constant is only the uncalibrated window arithmetic), so
        # the message must quote the calibrated number actually enforced.
        # v6.87.9: the window itself is resolved from Capability Evidence per
        # reviewer (an unknown route keeps the full-window assumption; a KNOWN
        # sub-1M one shrinks) and the reserves scale to it, so the quoted number
        # follows the same resolution instead of a hardcoded 1M.
        from ouroboros.reviewer_window import (
            reviewer_context_window,
            window_scaled_reserves,
        )
        from ouroboros.tools.review_helpers import calibrated_input_token_limit
        from ouroboros.deep_self_review import (
            _DEEP_MAX_OUTPUT_TOKENS, _DEEP_OUTPUT_MARGIN_TOKENS,
        )
        window = reviewer_context_window("test-model")
        output_reserve, margin = window_scaled_reserves(
            window,
            output_reserve=_DEEP_MAX_OUTPUT_TOKENS,
            tokenizer_margin=_DEEP_OUTPUT_MARGIN_TOKENS,
        )
        enforced = calibrated_input_token_limit(
            "test-model",
            context_window=window,
            output_reserve=output_reserve,
            tokenizer_margin=margin,
        )
        assert f"{enforced:,}" in result
        assert usage == {}
        mock_llm.chat.assert_not_called()


class TestOmissionSectionBound:
    def test_omission_section_stays_within_reserved_budget(self):
        """The in-prompt omission summary is bounded + reserved; a huge skipped
        list (the +853 root cause) can no longer push the assembled pack over
        the budget the atlas filled to."""
        from ouroboros.deep_self_review import (
            _OMISSION_SECTION_RESERVE_TOKENS,
            _append_omission_section,
        )
        from ouroboros.utils import estimate_tokens

        skipped = [
            f"some/very/long/path/segment_{i}/deeply/nested/file_{i}.py (excluded_test: wider tests excluded)"
            for i in range(500)
        ]
        parts: list[str] = []
        _append_omission_section(parts, skipped)

        assert len(parts) == 1
        section = parts[0]
        assert estimate_tokens(section) <= _OMISSION_SECTION_RESERVE_TOKENS
        assert "Omitted counts by reason" in section
        assert "excluded_test=500" in section
        assert "coverage manifest" in section  # explicit pointer, not silent truncation

    def test_omission_section_small_list_lists_everything(self):
        from ouroboros.deep_self_review import _append_omission_section

        skipped = ["a.py (oversized: >1MB)", "b.bin (binary/media: binary)"]
        parts: list[str] = []
        _append_omission_section(parts, skipped)
        assert "a.py (oversized: >1MB)" in parts[0]
        assert "b.bin (binary/media: binary)" in parts[0]
        assert "oversized=1" in parts[0]


def test_direct_openai_deep_review_sends_a_real_openai_model_id():
    """PHYSICAL-PAYLOAD proof, not a defaults-table assertion.

    The OpenRouter default is the slug `openai/gpt-5.6-sol-pro`. That `-pro`
    suffix is an OpenRouter routing slug, NOT an OpenAI model id: live-probed
    2026-07-29, `gpt-5.6-sol-pro` on api.openai.com /v1/chat/completions returns
    404, while pro reasoning exists only on /v1/responses as
    `reasoning.mode="pro"` (200) — and /v1/chat/completions rejects a `reasoning`
    parameter outright (400 "Unknown parameter"). Every LLM call in llm.py is a
    chat.completions call, so the direct-OpenAI deep-review slot ships plain Sol
    and this test pins what actually reaches the wire.
    """
    import os
    from unittest import mock

    from ouroboros.llm import LLMClient
    from ouroboros.provider_models import OPENAI_DIRECT_DEFAULTS

    slot = OPENAI_DIRECT_DEFAULTS["deep_self_review"]
    assert slot.startswith("openai::"), slot

    mock_resp = mock.Mock()
    mock_resp.model_dump.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    with mock.patch("openai.OpenAI") as mock_openai_cls:
        mock_oa = mock.Mock()
        mock_oa.chat.completions.create.return_value = mock_resp
        mock_openai_cls.return_value = mock_oa
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-direct-test"}, clear=False):
            LLMClient().chat(
                messages=[{"role": "user", "content": "hi"}],
                model=slot, max_tokens=8, no_proxy=True,
            )
        assert mock_oa.chat.completions.create.called
        payload = mock_oa.chat.completions.create.call_args.kwargs

    # The id on the wire is a REAL OpenAI model, never the OpenRouter slug.
    assert payload["model"] == "gpt-5.6-sol"
    assert not payload["model"].endswith("-pro")
    # ...and no `reasoning` object is smuggled onto a chat.completions call, which
    # the live API rejects with 400 (the only pro carrier is the Responses API).
    assert "reasoning" not in payload
    assert "reasoning" not in (payload.get("extra_body") or {})


def test_direct_fallback_preserves_an_explicit_real_model_pin():
    """Only router-only `-pro` slugs are substituted by the provider default; an
    owner's explicit pin of a REAL OpenAI model keeps the mechanical rewrite."""
    import os
    from unittest import mock

    from ouroboros.provider_models import OPENAI_DIRECT_DEFAULTS

    env = {"OPENAI_API_KEY": "sk-test"}
    with mock.patch.dict(os.environ, env, clear=False):
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ.pop("OPENAI_BASE_URL", None)
        with mock.patch(
            "ouroboros.deep_self_review.get_deep_self_review_model",
            return_value="openai/gpt-5.5",
        ):
            available, model = is_review_available()
        assert available is True
        assert model == "openai::gpt-5.5", "an explicit real-model pin survives"
        with mock.patch(
            "ouroboros.deep_self_review.get_deep_self_review_model",
            return_value="openai/gpt-5.5-pro",
        ):
            available, model = is_review_available()
        assert available is True
        assert model == OPENAI_DIRECT_DEFAULTS["deep_self_review"], (
            "a router-only -pro slug lands on the provider default"
        )


# Shared manifest fixture for Gate A and Gate B tests. The pack_path set is
# built from this manifest + _MEMORY_WHITELIST at the time _ground_response_in_pack
# runs; tests that need different selected/omitted rows construct their own.
# ``secret_module.py`` is intentionally absent from both selected and omitted —
# a fabricated path the model has no way to know about, used by the
# "response_refs_paths_not_in_pack" test as a true non-groundable reference.
_MANIFEST_REVIEW_TOOLS = {
    "status": "ok",
    "selected": [
        {"rel_path": "ouroboros/deep_self_review.py", "disposition": "selected"},
        {"rel_path": "ouroboros/tools/review_helpers.py", "disposition": "selected"},
        {"rel_path": "ouroboros/loop.py", "disposition": "selected"},
    ],
    "omitted": [],
}


class TestPackIntegrityGate:
    """Gate A — refuses to send a pathologically small pack to the reviewer.

    Catches the structural-hallucination class: a 1-2 file pack invites the
    reviewer to invent project context. Three sub-conditions, any of which
    fails the gate; the gate fires BEFORE ``chat_observed`` is invoked so no
    review tokens are spent on the rejected attempt.
    """

    def _sufficient_pack_stats(self):
        return {
            "file_count": 6,
            "memory_count": 3,
            "total_chars": 60_000,
            "skipped": [],
            "context_manifest": _MANIFEST_REVIEW_TOOLS,
        }

    def _build_with_stats(self, stats):
        pack = "x" * stats["total_chars"]
        return pack, stats

    def test_gate_a_fires_when_total_chars_below_minimum(self, tmp_repo, tmp_drive):
        stats = self._sufficient_pack_stats()
        stats["total_chars"] = 49_999  # one below the 50_000 floor
        mock_llm = mock.Mock()

        with mock.patch(
            "ouroboros.deep_self_review.build_review_pack",
            return_value=self._build_with_stats(stats),
        ):
            result, usage = run_deep_self_review(
                repo_dir=tmp_repo,
                drive_root=tmp_drive,
                llm=mock_llm,
                emit_progress=lambda x: None,
                event_queue=None,
                model="test-model",
            )

        assert "pack integrity gate failed" in result
        assert "total_chars=49,999" in result
        assert "min 50,000" in result
        assert usage == {}
        mock_llm.chat.assert_not_called()

    def test_gate_a_fires_when_memory_count_below_minimum_despite_adequate_file_count(
        self, tmp_repo, tmp_drive
    ):
        """At file_count=5 (at the threshold, NOT below), memory_count=1 below.

        The fixture has 3 atlas selected + 2 memory files = file_count=5, which
        satisfies the file_count sub-condition. The ``memory_count`` sub-condition
        is the actual firing trigger — the test name is intentionally specific
        to avoid future-maintainer confusion about which sub-check fired.
        """
        stats = self._sufficient_pack_stats()
        stats["file_count"] = 5  # at threshold, not below
        stats["memory_count"] = 1  # below threshold
        mock_llm = mock.Mock()

        with mock.patch(
            "ouroboros.deep_self_review.build_review_pack",
            return_value=self._build_with_stats(stats),
        ):
            result, usage = run_deep_self_review(
                repo_dir=tmp_repo,
                drive_root=tmp_drive,
                llm=mock_llm,
                emit_progress=lambda x: None,
                event_queue=None,
                model="test-model",
            )

        assert "pack integrity gate failed" in result
        assert "memory_count=1" in result
        assert "min 3" in result
        mock_llm.chat.assert_not_called()

    def test_gate_a_passes_when_pack_meets_all_thresholds(self, tmp_repo, tmp_drive):
        """At file_count=6, memory_count=3, total_chars=60_000, gate A passes,
        chat_observed IS called, and Gate B passes too (3 grounded refs)."""
        mock_llm = mock.Mock()

        with (
            mock.patch(
                "ouroboros.deep_self_review.build_review_pack",
                return_value=self._build_with_stats(self._sufficient_pack_stats()),
            ),
            mock.patch(
                "ouroboros.llm_observability.chat_observed",
                return_value=(
                    {
                        "content": (
                            "Reviewed ouroboros/deep_self_review.py, "
                            "ouroboros/loop.py, and ouroboros/tools/review_helpers.py. "
                            "Findings follow."
                        )
                    },
                    {"cost": 0.01},
                ),
            ),
        ):
            result, usage = run_deep_self_review(
                repo_dir=tmp_repo,
                drive_root=tmp_drive,
                llm=mock_llm,
                emit_progress=lambda x: None,
                event_queue=None,
                model="test-model",
            )

        assert "Reviewed ouroboros" in result
        assert "pack integrity gate failed" not in result
        assert "response ungrounded" not in result
        assert usage == {"cost": 0.01}

    def test_gate_a_does_not_fire_when_atlas_assembly_fails_first(
        self, tmp_repo, tmp_drive
    ):
        """The pre-existing ``pack_text == ""`` + skipped-fatal path takes
        precedence over Gate A — preserves the existing error verbatim and
        does not double-report."""
        mock_llm = mock.Mock()

        with mock.patch(
            "ouroboros.deep_self_review.build_review_pack",
            return_value=("", {"file_count": 0, "total_chars": 0,
                               "skipped": ["FATAL: atlas exploded"]}),
        ):
            result, usage = run_deep_self_review(
                repo_dir=tmp_repo,
                drive_root=tmp_drive,
                llm=mock_llm,
                emit_progress=lambda x: None,
                event_queue=None,
                model="test-model",
            )

        assert "Failed to build review pack" in result
        assert "atlas exploded" in result
        assert "pack integrity gate failed" not in result
        mock_llm.chat.assert_not_called()

    def test_gate_a_at_exact_minimums_passes(self, tmp_repo, tmp_drive):
        """Boundary case: file_count=5, memory_count=3, total_chars=50_000.

        Gate A uses ``<`` (strict), not ``<=`` — equal-to-minimum passes. Catches
        a regression where someone might tighten to ``<=`` and silently reject
        valid packs at the floor.
        """
        stats = self._sufficient_pack_stats()
        stats["file_count"] = 5
        stats["memory_count"] = 3
        stats["total_chars"] = 50_000
        mock_llm = mock.Mock()

        with (
            mock.patch(
                "ouroboros.deep_self_review.build_review_pack",
                return_value=self._build_with_stats(stats),
            ),
            mock.patch(
                "ouroboros.llm_observability.chat_observed",
                return_value=(
                    {
                        "content": (
                            "Grounded review of ouroboros/deep_self_review.py, "
                            "ouroboros/loop.py, and ouroboros/tools/review_helpers.py."
                        )
                    },
                    {"cost": 0.01},
                ),
            ),
        ):
            result, usage = run_deep_self_review(
                repo_dir=tmp_repo,
                drive_root=tmp_drive,
                llm=mock_llm,
                emit_progress=lambda x: None,
                event_queue=None,
                model="test-model",
            )

        assert "Grounded review" in result
        assert "pack integrity gate failed" not in result
        assert "response ungrounded" not in result
        assert usage == {"cost": 0.01}

    def test_gate_a_fires_when_memory_count_key_absent(self, tmp_repo, tmp_drive):
        """Regression guard: stats dict missing ``memory_count`` key (older
        build_review_pack_re_repack pre-fix) defaults to 0, which fails the memory
        sub-condition. Without ``memory_count`` exposure, Gate A becomes a
        permanent failure even on structurally complete packs.
        """
        stats = {
            "file_count": 100,  # well above floor
            # no memory_count key
            "total_chars": 200_000,  # well above floor
            "skipped": [],
            "context_manifest": _MANIFEST_REVIEW_TOOLS,
        }
        mock_llm = mock.Mock()

        with mock.patch(
            "ouroboros.deep_self_review.build_review_pack",
            return_value=self._build_with_stats(stats),
        ):
            result, usage = run_deep_self_review(
                repo_dir=tmp_repo,
                drive_root=tmp_drive,
                llm=mock_llm,
                emit_progress=lambda x: None,
                event_queue=None,
                model="test-model",
            )

        assert "pack integrity gate failed" in result
        assert "memory_count=0" in result
        mock_llm.chat.assert_not_called()


class TestResponseGroundingGate:
    """Gate B — refuses to publish a review whose findings cannot be tied to
    pack artifacts.

    Catches the structural-hallucination class at the OTHER end of the
    pipeline: a model that was given a real pack but ignored it (or copied a
    prior review from training data, or fabricated findings about files that
    don't exist). Failures here preserve ``usage`` because review tokens WERE
    spent — this is not a pre-flight check.
    """

    def _build_with_pack(self, manifest=None):
        manifest = manifest if manifest is not None else _MANIFEST_REVIEW_TOOLS
        stats = {
            "file_count": 6,
            "memory_count": 3,
            "total_chars": 60_000,
            "skipped": [],
            "context_manifest": manifest,
        }
        return "x" * 60_000, stats

    def test_gate_b_fires_when_response_has_no_path_refs(self, tmp_repo, tmp_drive):
        """Pure prose with no path-like tokens: gate fires."""
        mock_llm = mock.Mock()

        with (
            mock.patch(
                "ouroboros.deep_self_review.build_review_pack",
                return_value=self._build_with_pack(),
            ),
            mock.patch(
                "ouroboros.llm_observability.chat_observed",
                return_value=(
                    {
                        "content": (
                            "This is a deep review of the agent system. The architecture "
                            "is generally sound but the documentation could be improved. "
                            "Several edge cases in error handling warrant attention."
                        )
                    },
                    {"cost": 0.42},
                ),
            ),
        ):
            result, usage = run_deep_self_review(
                repo_dir=tmp_repo,
                drive_root=tmp_drive,
                llm=mock_llm,
                emit_progress=lambda x: None,
                event_queue=None,
                model="test-model",
            )

        assert "response ungrounded" in result
        assert "0 distinct path references" in result
        assert usage == {"cost": 0.42}, "usage preserved: review tokens were spent"

    def test_gate_b_fires_when_response_refs_paths_not_in_pack(self, tmp_repo, tmp_drive):
        """Response mentions fabricated paths that are NOT in the manifest."""
        mock_llm = mock.Mock()

        with (
            mock.patch(
                "ouroboros.deep_self_review.build_review_pack",
                return_value=self._build_with_pack(),
            ),
            mock.patch(
                "ouroboros.llm_observability.chat_observed",
                return_value=(
                    {
                        "content": (
                            "Issues found in ouroboros/nonexistent.py and "
                            "ouroboros/loop.py, also some.py and other.py. "
                            "More on ouroboros/tools/review_helpers.py."
                        )
                    },
                    {"cost": 0.42},
                ),
            ),
        ):
            result, usage = run_deep_self_review(
                repo_dir=tmp_repo,
                drive_root=tmp_drive,
                llm=mock_llm,
                emit_progress=lambda x: None,
                event_queue=None,
                model="test-model",
            )

        assert "response ungrounded" in result
        assert "2 distinct path references" in result  # loop.py + review_helpers.py
        assert usage == {"cost": 0.42}

    def test_gate_b_fires_when_only_one_path_ref_intersects(self, tmp_repo, tmp_drive):
        """5 path mentions but only 1 in pack: gate fires (count < 3)."""
        mock_llm = mock.Mock()

        with (
            mock.patch(
                "ouroboros.deep_self_review.build_review_pack",
                return_value=self._build_with_pack(),
            ),
            mock.patch(
                "ouroboros.llm_observability.chat_observed",
                return_value=(
                    {
                        "content": (
                            "Reviewed ouroboros/loop.py, some.py, other.py, "
                            "another.py, last.py. Findings: none."
                        )
                    },
                    {"cost": 0.42},
                ),
            ),
        ):
            result, usage = run_deep_self_review(
                repo_dir=tmp_repo,
                drive_root=tmp_drive,
                llm=mock_llm,
                emit_progress=lambda x: None,
                event_queue=None,
                model="test-model",
            )

        assert "response ungrounded" in result
        assert "1 distinct path references" in result
        assert usage == {"cost": 0.42}

    def test_gate_b_passes_with_three_distinct_grounded_refs(self, tmp_repo, tmp_drive):
        """Response mentions 3 distinct pack paths → gate passes."""
        mock_llm = mock.Mock()

        with (
            mock.patch(
                "ouroboros.deep_self_review.build_review_pack",
                return_value=self._build_with_pack(),
            ),
            mock.patch(
                "ouroboros.llm_observability.chat_observed",
                return_value=(
                    {
                        "content": (
                            "Reviewed ouroboros/deep_self_review.py for the gates, "
                            "ouroboros/loop.py for the task loop, and "
                            "ouroboros/tools/review_helpers.py for the prompt pack. "
                            "All look sound."
                        )
                    },
                    {"cost": 0.42},
                ),
            ),
        ):
            result, usage = run_deep_self_review(
                repo_dir=tmp_repo,
                drive_root=tmp_drive,
                llm=mock_llm,
                emit_progress=lambda x: None,
                event_queue=None,
                model="test-model",
            )

        assert "Reviewed ouroboros" in result
        assert "response ungrounded" not in result
        assert usage == {"cost": 0.42}

    def test_gate_b_strips_url_paths_before_checking(self, tmp_repo, tmp_drive):
        """A URL whose leaf path IS in the pack must NOT ground via the leaf.

        Without the URL-stripping pre-pass, ``https://example.com/ouroboros/deep_self_review.py``
        would parse as ``example.com/ouroboros/deep_self_review.py`` and fail
        suffix-matching (no pack_path ends with that string), so the test passes
        for the wrong reason. The strip-then-extract implementation removes
        the URL substring entirely, leaving zero path refs in this response, so
        the gate fires (0 grounded).
        """
        mock_llm = mock.Mock()

        with (
            mock.patch(
                "ouroboros.deep_self_review.build_review_pack",
                return_value=self._build_with_pack(),
            ),
            mock.patch(
                "ouroboros.llm_observability.chat_observed",
                return_value=(
                    {
                        "content": (
                            "See https://example.com/ouroboros/deep_self_review.py "
                            "for the canonical review-pipeline documentation."
                        )
                    },
                    {"cost": 0.42},
                ),
            ),
        ):
            result, usage = run_deep_self_review(
                repo_dir=tmp_repo,
                drive_root=tmp_drive,
                llm=mock_llm,
                emit_progress=lambda x: None,
                event_queue=None,
                model="test-model",
            )

        assert "response ungrounded" in result
        assert "0 distinct path references" in result

    def test_gate_b_handles_basename_only_paths(self, tmp_repo, tmp_drive):
        """Response references ``deep_self_review.py`` (basename) — suffix-match
        against ``ouroboros/deep_self_review.py`` grounds it."""
        mock_llm = mock.Mock()

        with (
            mock.patch(
                "ouroboros.deep_self_review.build_review_pack",
                return_value=self._build_with_pack(),
            ),
            mock.patch(
                "ouroboros.llm_observability.chat_observed",
                return_value=(
                    {
                        "content": (
                            "deep_self_review.py, loop.py, review_helpers.py — all fine."
                        )
                    },
                    {"cost": 0.42},
                ),
            ),
        ):
            result, usage = run_deep_self_review(
                repo_dir=tmp_repo,
                drive_root=tmp_drive,
                llm=mock_llm,
                emit_progress=lambda x: None,
                event_queue=None,
                model="test-model",
            )

        assert "Reviewed ouroboros" in result.lower() or "deep_self_review.py" in result
        assert "response ungrounded" not in result
        assert usage == {"cost": 0.42}

    def test_gate_b_includes_memory_whitelist_paths(self, tmp_repo, tmp_drive):
        """Response references memory/identity.md, memory/scratchpad.md, and
        memory/knowledge/patterns.md (all in _MEMORY_WHITELIST) — must ground
        via the helper, even though they are not in atlas.selected."""
        mock_llm = mock.Mock()

        with (
            mock.patch(
                "ouroboros.deep_self_review.build_review_pack",
                return_value=self._build_with_pack(),
            ),
            mock.patch(
                "ouroboros.llm_observability.chat_observed",
                return_value=(
                    {
                        "content": (
                            "Reviewed memory/identity.md for the agent's self-model, "
                            "memory/scratchpad.md for working context, and "
                            "memory/knowledge/patterns.md for the pattern register. "
                            "All current."
                        )
                    },
                    {"cost": 0.42},
                ),
            ),
        ):
            result, usage = run_deep_self_review(
                repo_dir=tmp_repo,
                drive_root=tmp_drive,
                llm=mock_llm,
                emit_progress=lambda x: None,
                event_queue=None,
                model="test-model",
            )

        assert "Reviewed memory" in result
        assert "response ungrounded" not in result
        assert usage == {"cost": 0.42}

    def test_gate_b_passes_usage_accounting_preserved(self, tmp_repo, tmp_drive):
        """When Gate B fires, usage is preserved (we spent review tokens)."""
        mock_llm = mock.Mock()

        with (
            mock.patch(
                "ouroboros.deep_self_review.build_review_pack",
                return_value=self._build_with_pack(),
            ),
            mock.patch(
                "ouroboros.llm_observability.chat_observed",
                return_value=(
                    {"content": "No code references here, just prose."},
                    {"cost": 0.99},
                ),
            ),
        ):
            result, usage = run_deep_self_review(
                repo_dir=tmp_repo,
                drive_root=tmp_drive,
                llm=mock_llm,
                emit_progress=lambda x: None,
                event_queue=None,
                model="test-model",
            )

        assert "response ungrounded" in result
        assert usage == {"cost": 0.99}, "usage preserved when Gate B rejects"


def test_deep_max_output_tokens_cap_prevents_100k_402_trap():
    """Regression: ibl-be9ba2d99b25 (task c7862982).

    Pre-fix: deep_self_review called chat_observed with max_tokens=100_000,
    causing an OpenRouter account-level 402 ("requested up to 100000 tokens,
    but can only afford 56") and an $11.00 charge for a zero-tool-call
    review. The fix landed in 8506304f (v6.103.9) by lowering the constant
    to 10_000; this test locks that cap so the rot cannot return via a
    tempting "just bump it back up" edit. Raise WITH a backpressure
    rationale (and rotate this test accordingly), not by accident.
    """
    from ouroboros.deep_self_review import _DEEP_MAX_OUTPUT_TOKENS

    assert _DEEP_MAX_OUTPUT_TOKENS <= 10_000, (
        f"_DEEP_MAX_OUTPUT_TOKENS={_DEEP_MAX_OUTPUT_TOKENS} exceeds the 10k cap. "
        "The 100k default was the rot captured by ibl-be9ba2d99b25 ($11.00 wasted "
        "on task c7862982). If a future review genuinely needs more output, raise "
        "the cap WITH a backpressure rationale, not by editing this constant."
    )

    # Belt-and-braces: the constant must be >0 (a zero would silently under-deliver
    # the review just as effectively as a 100k default would over-charge). The 10k
    # ceiling implies a minimum of roughly 1k for any honest findings list.
    assert _DEEP_MAX_OUTPUT_TOKENS >= 1_000, (
        f"_DEEP_MAX_OUTPUT_TOKENS={_DEEP_MAX_OUTPUT_TOKENS} is suspiciously small. "
        "The review's findings section needs at least a few hundred tokens; below "
        "1k the structural-utility of the review collapses."
    )

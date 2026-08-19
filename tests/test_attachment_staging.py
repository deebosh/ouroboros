"""First-class attachment access (v6.52.0, P1).

Covers the shared staging substrate (stage_task_attachments), its secret-skip /
bound behavior, the READY artifact_store manifest lines, native image-block
injection via build_user_content, the collect_task_artifact_records exclusion of
staged inputs, and the desktop chat reroute.

Parallel-safe: every test isolates state under tmp_path; no shared global state.
"""

from __future__ import annotations

import base64

import pytest


# A 1x1 transparent PNG.
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _drive(tmp_path):
    drive = tmp_path / "data"
    drive.mkdir(parents=True, exist_ok=True)
    return drive


def _attach_dir(drive, task_id):
    return drive / "task_results" / "artifacts" / task_id / "attachments"


class TestStageTaskAttachments:
    def test_stages_into_artifact_store(self, tmp_path):
        from ouroboros.artifacts import stage_task_attachments

        drive = _drive(tmp_path)
        src = tmp_path / "report.txt"
        src.write_text("hello", encoding="utf-8")

        manifest = stage_task_attachments(drive, "task01", [{"path": str(src), "label": "Report"}])

        assert len(manifest) == 1
        entry = manifest[0]
        assert entry["root"] == "artifact_store"
        assert entry["relpath"].startswith("attachments/")
        assert entry["label"] == "Report"
        assert entry["is_image"] is False
        staged = _attach_dir(drive, "task01") / entry["relpath"].split("/", 1)[1]
        assert staged.is_file()
        assert staged.read_text(encoding="utf-8") == "hello"

    def test_child_drive_and_shared_drive_both_work(self, tmp_path):
        """The relpath is identical regardless of which drive it is staged into;
        the caller resolves it against task['drive_root'] at read time."""
        from ouroboros.artifacts import stage_task_attachments

        src = tmp_path / "data.csv"
        src.write_text("a,b\n1,2\n", encoding="utf-8")

        shared = _drive(tmp_path)
        child = tmp_path / "child_drive"
        child.mkdir()

        m_shared = stage_task_attachments(shared, "tshared", [{"path": str(src)}])
        m_child = stage_task_attachments(child, "tchild", [{"path": str(src)}])

        assert m_shared and m_child
        assert m_shared[0]["relpath"].startswith("attachments/")
        assert m_child[0]["relpath"].startswith("attachments/")
        # Each landed in its OWN drive's artifact store.
        assert (_attach_dir(shared, "tshared") / m_shared[0]["relpath"].split("/", 1)[1]).is_file()
        assert (_attach_dir(child, "tchild") / m_child[0]["relpath"].split("/", 1)[1]).is_file()

    def test_manifest_has_no_bare_absolute_path(self, tmp_path):
        from ouroboros.artifacts import stage_task_attachments

        drive = _drive(tmp_path)
        src = tmp_path / "doc.md"
        src.write_text("x", encoding="utf-8")

        manifest = stage_task_attachments(drive, "task02", [{"path": str(src), "label": "Doc"}])
        entry = manifest[0]
        # No manifest field leaks the absolute source path.
        for value in entry.values():
            assert str(src) != str(value)
        assert "/" not in entry["relpath"].split("/", 1)[1]  # single attachments/ component

    def test_secret_source_skipped_credentials(self, tmp_path):
        from ouroboros.artifacts import stage_task_attachments

        drive = _drive(tmp_path)
        good = tmp_path / "ok.txt"
        good.write_text("fine", encoding="utf-8")
        secret = tmp_path / "credentials.json"
        secret.write_text("{\"token\": \"x\"}", encoding="utf-8")

        manifest = stage_task_attachments(
            drive, "task03", [{"path": str(secret)}, {"path": str(good)}]
        )
        labels = {m["label"] for m in manifest}
        assert "ok.txt" in labels
        assert "credentials.json" not in labels
        assert len(manifest) == 1

    def test_secret_source_skipped_ssh_dir(self, tmp_path):
        from ouroboros.artifacts import stage_task_attachments

        drive = _drive(tmp_path)
        ssh = tmp_path / ".ssh"
        ssh.mkdir()
        key = ssh / "id_rsa"
        key.write_text("PRIVATE", encoding="utf-8")

        manifest = stage_task_attachments(drive, "task04", [{"path": str(key)}])
        assert manifest == []

    def test_image_source_marked_is_image(self, tmp_path):
        from ouroboros.artifacts import stage_task_attachments

        drive = _drive(tmp_path)
        img = tmp_path / "pic.png"
        img.write_bytes(_PNG_BYTES)

        manifest = stage_task_attachments(drive, "task05", [{"path": str(img)}])
        assert len(manifest) == 1
        assert manifest[0]["is_image"] is True
        assert manifest[0]["mime"] == "image/png"

    def test_missing_and_nonfile_skipped(self, tmp_path):
        from ouroboros.artifacts import stage_task_attachments

        drive = _drive(tmp_path)
        adir = tmp_path / "adir"
        adir.mkdir()
        manifest = stage_task_attachments(
            drive, "task06", [{"path": str(tmp_path / "nope.txt")}, {"path": str(adir)}]
        )
        assert manifest == []

    def test_large_file_skipped(self, tmp_path, monkeypatch):
        import ouroboros.artifacts as art

        drive = _drive(tmp_path)
        big = tmp_path / "big.bin"
        big.write_bytes(b"\0" * 1024)
        monkeypatch.setattr(art, "_MAX_STAGED_ATTACHMENT_BYTES", 512)
        manifest = art.stage_task_attachments(drive, "task07", [{"path": str(big)}])
        assert manifest == []

    def test_max_count_bound(self, tmp_path, monkeypatch):
        import ouroboros.artifacts as art

        drive = _drive(tmp_path)
        monkeypatch.setattr(art, "_MAX_STAGED_ATTACHMENTS", 2)
        items = []
        for i in range(5):
            f = tmp_path / f"f{i}.txt"
            f.write_text(str(i), encoding="utf-8")
            items.append({"path": str(f)})
        manifest = art.stage_task_attachments(drive, "task08", items)
        assert len(manifest) == 2

    def test_distinct_sources_no_clobber(self, tmp_path):
        from ouroboros.artifacts import stage_task_attachments

        drive = _drive(tmp_path)
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        d1.mkdir()
        d2.mkdir()
        (d1 / "same.txt").write_text("one", encoding="utf-8")
        (d2 / "same.txt").write_text("two", encoding="utf-8")

        manifest = stage_task_attachments(
            drive, "task09", [{"path": str(d1 / "same.txt")}, {"path": str(d2 / "same.txt")}]
        )
        assert len(manifest) == 2
        rels = {m["relpath"] for m in manifest}
        assert len(rels) == 2  # distinct destinations


class TestComposeTaskTextManifestLines:
    def test_ready_read_file_lines(self):
        from ouroboros.gateway.tasks import _compose_task_text

        manifest = [
            {"label": "Pic", "root": "artifact_store", "relpath": "attachments/pic.png",
             "mime": "image/png", "is_image": True},
            {"label": "Doc", "root": "artifact_store", "relpath": "attachments/doc.txt",
             "mime": "text/plain", "is_image": False},
        ]
        text = _compose_task_text(
            "Do the thing",
            workspace_root=None,
            workspace_mode="",
            memory_mode="shared",
            workspace_preflight={},
            attachments=manifest,
        )
        assert "[ATTACHMENTS]" in text
        assert "- Pic (image): read_file(root='artifact_store', path='attachments/pic.png')" in text
        assert "read_file(root='artifact_store', path='attachments/doc.txt')" in text
        # Never a bare absolute path.
        assert "/attachments/pic.png:" not in text


class TestBuildUserContentAttachmentImages:
    def test_emits_native_image_blocks(self, tmp_path):
        from ouroboros.artifacts import stage_task_attachments
        from ouroboros.context import build_user_content

        drive = _drive(tmp_path)
        img = tmp_path / "shot.png"
        img.write_bytes(_PNG_BYTES)
        manifest = stage_task_attachments(drive, "tcontent", [{"path": str(img), "label": "Shot"}])
        images = [m for m in manifest if m["is_image"]]

        task = {
            "id": "tcontent",
            "drive_root": str(drive),
            "text": "look at this",
            "attachment_images": images,
        }
        content = build_user_content(task)
        assert isinstance(content, list)
        image_blocks = [b for b in content if b.get("type") == "image_url"]
        assert len(image_blocks) == 1
        block = image_blocks[0]
        assert block["image_url"]["url"].startswith("data:image/png;base64,")
        assert block["_source_path"].endswith(".png")
        # The lead text block carries the message text.
        assert any(b.get("type") == "text" and "look at this" in b.get("text", "") for b in content)

    def test_image_base64_backward_compat_still_works(self):
        from ouroboros.context import build_user_content

        task = {
            "id": "tlegacy",
            "text": "hi",
            "image_base64": base64.b64encode(_PNG_BYTES).decode("ascii"),
            "image_mime": "image/png",
            "image_caption": "a cat",
        }
        content = build_user_content(task)
        assert isinstance(content, list)
        image_blocks = [b for b in content if b.get("type") == "image_url"]
        assert len(image_blocks) == 1

    def test_cap_respected(self, tmp_path, monkeypatch):
        import ouroboros.context_budget as cb
        from ouroboros.artifacts import stage_task_attachments
        from ouroboros.context import build_user_content

        monkeypatch.setattr(cb, "MAX_LIVE_IMAGE_BLOCKS", 1)
        drive = _drive(tmp_path)
        items = []
        for i in range(3):
            p = tmp_path / f"img{i}.png"
            p.write_bytes(_PNG_BYTES)
            items.append({"path": str(p), "label": f"img{i}"})
        manifest = stage_task_attachments(drive, "tcap", items)
        task = {
            "id": "tcap",
            "drive_root": str(drive),
            "text": "many",
            "attachment_images": [m for m in manifest if m["is_image"]],
        }
        content = build_user_content(task)
        image_blocks = [b for b in content if b.get("type") == "image_url"]
        assert len(image_blocks) == 1  # capped; rest stay manifest-readable

    def test_non_image_attachment_not_injected(self, tmp_path):
        """A staged non-image (binary/text) yields no attachment_images entry, so
        build_user_content injects no image block for it."""
        from ouroboros.artifacts import stage_task_attachments
        from ouroboros.context import build_user_content

        drive = _drive(tmp_path)
        blob = tmp_path / "data.bin"
        blob.write_bytes(b"\x00\x01\x02binary")
        manifest = stage_task_attachments(drive, "tbin", [{"path": str(blob)}])
        assert manifest and manifest[0]["is_image"] is False
        images = [m for m in manifest if m["is_image"]]
        assert images == []

        task = {"id": "tbin", "drive_root": str(drive), "text": "t", "attachment_images": images}
        content = build_user_content(task)
        # No images -> plain text content.
        assert content == "t"


class TestCollectArtifactRecordsExcludesAttachments:
    def test_attachments_not_recorded_as_deliverables(self, tmp_path):
        from ouroboros.artifacts import (
            collect_task_artifact_records,
            copy_file_to_task_artifacts,
            stage_task_attachments,
            task_artifact_dir_path,
        )

        drive = _drive(tmp_path)
        # A staged INPUT attachment.
        src = tmp_path / "input.txt"
        src.write_text("in", encoding="utf-8")
        stage_task_attachments(drive, "tcollect", [{"path": str(src)}])

        # A real produced deliverable.
        deliverable = tmp_path / "output.txt"
        deliverable.write_text("out", encoding="utf-8")

        class _Ctx:
            pass

        ctx = _Ctx()
        ctx.drive_root = str(drive)
        ctx.task_id = "tcollect"
        copy_file_to_task_artifacts(ctx, str(deliverable))

        records = collect_task_artifact_records(drive, "tcollect")
        names = {r["name"] for r in records}
        assert "output.txt" in names
        assert "input.txt" not in names
        # Defensive: no recorded path is under attachments/.
        adir = task_artifact_dir_path(drive, "tcollect")
        for r in records:
            rel = str(r["path"]).replace(str(adir), "").lstrip("/")
            assert not rel.startswith("attachments/")


class _FakeChatAgent:
    """Captures the task dict _run_chat_task builds, without running the LLM."""

    def __init__(self):
        self.task = None

    def handle_task(self, task):
        self.task = task
        return []


class TestDesktopChatFullSetStaging:
    """v6.52.0 (P1, full desktop unify): the WHOLE desktop attachment set routes
    through the shared substrate via task['metadata']['chat_attachment_uploads']."""

    def test_image_plus_pdf_both_staged_and_manifest_rendered(self, tmp_path, monkeypatch):
        import supervisor.workers as workers

        drive = _drive(tmp_path)
        monkeypatch.setattr(workers, "DRIVE_ROOT", drive)
        # Avoid the proactive namer spinning a real thread/LLM in this unit test
        # (it is a local `from ouroboros.project_naming import ...`, so patch source).
        import ouroboros.project_naming as project_naming
        monkeypatch.setattr(project_naming, "spawn_proactive_namer", lambda *a, **k: None)

        # Two uploads on disk: an image and a non-image PDF.
        img_src = tmp_path / "photo.png"
        img_src.write_bytes(_PNG_BYTES)
        pdf_src = tmp_path / "report.pdf"
        pdf_src.write_bytes(b"%PDF-1.4\n%fake\n")

        agent = _FakeChatAgent()
        workers._run_chat_task(
            agent,
            1,
            "look at these",
            None,
            task_metadata={
                "chat_attachment_uploads": [
                    {"path": str(img_src), "label": "photo.png", "mime": "image/png"},
                    {"path": str(pdf_src), "label": "report.pdf", "mime": "application/pdf"},
                ]
            },
        )

        task = agent.task
        assert task is not None
        # Both files staged under the artifact_store attachments dir.
        adir = _attach_dir(drive, task["id"])
        staged_names = sorted(p.name for p in adir.iterdir() if p.is_file())
        assert any(n.endswith(".png") for n in staged_names)
        assert any(n.endswith(".pdf") for n in staged_names)
        # attachment_images carries ONLY the image.
        imgs = task.get("attachment_images")
        assert isinstance(imgs, list) and len(imgs) == 1
        assert imgs[0]["is_image"] is True
        assert imgs[0]["relpath"].startswith("attachments/")
        # drive_root is set so build_user_content can resolve the relpaths.
        assert task["drive_root"] == str(drive)
        # The READY read_file manifest is appended for EVERY file (image + pdf).
        assert "[ATTACHMENTS]" in task["text"]
        assert "[END_ATTACHMENTS]" in task["text"]
        assert "read_file(root='artifact_store'" in task["text"]
        assert task["text"].count("read_file(root='artifact_store'") == 2

    def test_no_uploads_keeps_legacy_inline_image(self, tmp_path, monkeypatch):
        """Backward-compat: with no chat_attachment_uploads but image_data present
        (the legacy single-image base64 seam — Telegram bridge, older clients), the
        bytes are now staged through the shared substrate (same path the desktop
        set takes) and the inline image_base64 is dropped on success, so the agent
        can read them via read_file(root='artifact_store', path='attachments/...')
        instead of receiving them inline-only. Closes ibl-162952d44079."""
        import supervisor.workers as workers

        drive = _drive(tmp_path)
        monkeypatch.setattr(workers, "DRIVE_ROOT", drive)
        import ouroboros.project_naming as project_naming
        monkeypatch.setattr(project_naming, "spawn_proactive_namer", lambda *a, **k: None)

        b64 = base64.b64encode(_PNG_BYTES).decode("ascii")
        agent = _FakeChatAgent()
        workers._run_chat_task(agent, 1, "hi", (b64, "image/png", "a shot"))

        task = agent.task
        assert task is not None
        # Legacy inline keys dropped (avoid double-injection) — bytes are now on disk.
        assert task.get("image_base64") is None
        assert task.get("image_mime") is None
        # Staged via the shared substrate, not inline.
        imgs = task.get("attachment_images")
        assert isinstance(imgs, list) and len(imgs) == 1
        assert imgs[0]["is_image"] is True
        assert imgs[0]["relpath"].startswith("attachments/")
        # The READY read_file manifest is appended so the agent can read the image.
        assert task["drive_root"] == str(drive)
        assert "[ATTACHMENTS]" in task["text"]
        assert "[END_ATTACHMENTS]" in task["text"]
        assert "read_file(root='artifact_store'" in task["text"]
        # The caption (3rd tuple element) is recorded on the task but does NOT
        # override a non-empty user text — the agent's user text wins.
        assert task.get("image_caption") == "a shot"
        assert "hi" in task["text"]
        assert "a shot" not in task["text"]
        # The staged file actually exists on disk with the original bytes.
        adir = _attach_dir(drive, task["id"])
        staged = next(p for p in adir.iterdir() if p.is_file())
        assert staged.read_bytes() == _PNG_BYTES


class TestInlineImageDataPersistence:
    """The legacy single-image base64 seam (Telegram bridge, older clients) now
    routes through the shared staging substrate. Without this, image bytes stay
    inline in task['image_base64'] and the agent cannot read them via
    read_file(root='artifact_store', path='attachments/...') — closes
    ibl-162952d44079 and ibl-04a5239e3573 (duplicate)."""

    def test_image_data_only_persists_and_drops_inline(self, tmp_path, monkeypatch):
        """image_data + no metadata: stage via shared substrate, drop inline keys."""
        import supervisor.workers as workers

        drive = _drive(tmp_path)
        monkeypatch.setattr(workers, "DRIVE_ROOT", drive)
        import ouroboros.project_naming as project_naming
        monkeypatch.setattr(project_naming, "spawn_proactive_namer", lambda *a, **k: None)

        b64 = base64.b64encode(_PNG_BYTES).decode("ascii")
        agent = _FakeChatAgent()
        workers._run_chat_task(agent, 1, "describe this", (b64, "image/png", "diagram"))

        task = agent.task
        assert task is not None
        # Legacy inline keys dropped.
        assert task.get("image_base64") is None
        assert task.get("image_mime") is None
        # Staged via the shared substrate.
        imgs = task.get("attachment_images")
        assert isinstance(imgs, list) and len(imgs) == 1
        assert imgs[0]["is_image"] is True
        assert imgs[0]["relpath"].startswith("attachments/")
        assert task["drive_root"] == str(drive)
        assert "[ATTACHMENTS]" in task["text"]
        # Caption (3rd tuple element) is recorded on the task — does NOT override
        # the user text (the agent's user text wins when both are present).
        assert task.get("image_caption") == "diagram"
        # Original bytes are recoverable.
        adir = _attach_dir(drive, task["id"])
        staged = next(p for p in adir.iterdir() if p.is_file())
        assert staged.read_bytes() == _PNG_BYTES
        # Temporary source under data/uploads was cleaned up after staging.
        leftover = list((drive / "uploads").iterdir()) if (drive / "uploads").exists() else []
        assert leftover == [], f"inline staging source leaked: {leftover}"

    def test_image_data_with_empty_text_seeds_caption_into_text(self, tmp_path, monkeypatch):
        """When text is empty, the caption seeds task['text'] and survives staging."""
        import supervisor.workers as workers

        drive = _drive(tmp_path)
        monkeypatch.setattr(workers, "DRIVE_ROOT", drive)
        import ouroboros.project_naming as project_naming
        monkeypatch.setattr(project_naming, "spawn_proactive_namer", lambda *a, **k: None)

        b64 = base64.b64encode(_PNG_BYTES).decode("ascii")
        agent = _FakeChatAgent()
        workers._run_chat_task(agent, 1, "", (b64, "image/png", "my caption"))

        task = agent.task
        assert task is not None
        # The caption seeds task['text'] AND survives the staging rewrite.
        assert "my caption" in task["text"]
        assert task.get("image_caption") == "my caption"
        # And the manifest is appended after the caption.
        assert "[ATTACHMENTS]" in task["text"]

    def test_image_data_two_tuple_no_caption(self, tmp_path, monkeypatch):
        """image_data is (b64, mime) — no caption — still stages correctly; the
        text field is the user-supplied 'text' argument verbatim."""
        import supervisor.workers as workers

        drive = _drive(tmp_path)
        monkeypatch.setattr(workers, "DRIVE_ROOT", drive)
        import ouroboros.project_naming as project_naming
        monkeypatch.setattr(project_naming, "spawn_proactive_namer", lambda *a, **k: None)

        b64 = base64.b64encode(_PNG_BYTES).decode("ascii")
        agent = _FakeChatAgent()
        workers._run_chat_task(agent, 1, "what is this?", (b64, "image/png"))

        task = agent.task
        assert task is not None
        # Inline keys dropped.
        assert task.get("image_base64") is None
        assert task.get("image_mime") is None
        # Caption stays empty.
        assert task.get("image_caption") is None
        # Original text survives the staging rewrite.
        assert "what is this?" in task["text"]
        # And the manifest is appended.
        assert "[ATTACHMENTS]" in task["text"]

    def test_image_data_with_unrelated_metadata(self, tmp_path, monkeypatch):
        """image_data + task_metadata that has NO chat_attachment_uploads key —
        the staging still materialises the inline image (chat_attachment_uploads
        is empty, not absent-with-meaning)."""
        import supervisor.workers as workers

        drive = _drive(tmp_path)
        monkeypatch.setattr(workers, "DRIVE_ROOT", drive)
        import ouroboros.project_naming as project_naming
        monkeypatch.setattr(project_naming, "spawn_proactive_namer", lambda *a, **k: None)

        b64 = base64.b64encode(_PNG_BYTES).decode("ascii")
        agent = _FakeChatAgent()
        workers._run_chat_task(
            agent, 1, "x", (b64, "image/png"),
            task_metadata={"some_other_key": 42},
        )

        task = agent.task
        assert task is not None
        assert task.get("image_base64") is None
        assert task.get("attachment_images") and len(task["attachment_images"]) == 1

    def test_image_data_with_existing_uploads_drops_inline(self, tmp_path, monkeypatch):
        """image_data + non-empty chat_attachment_uploads: the existing uploads
        list WINS (claim_2 — uploads-wins), the inline image_data bytes are
        dropped (the same as the pre-fix behaviour, preserved). The agent sees
        ONLY the explicit uploads; the inline image does not silently double-
        stage. (Practical call sites never send both for the same image — the
        Telegram bridge resolves its photo into chat_attachment_uploads before
        _run_chat_task is reached — so this branch is a guard against a custom
        integration that did.)
        """
        import supervisor.workers as workers

        drive = _drive(tmp_path)
        monkeypatch.setattr(workers, "DRIVE_ROOT", drive)
        import ouroboros.project_naming as project_naming
        monkeypatch.setattr(project_naming, "spawn_proactive_namer", lambda *a, **k: None)

        # One existing on-disk upload (PDF, not an image).
        pdf_src = tmp_path / "report.pdf"
        pdf_src.write_bytes(b"%PDF-1.4\n%fake\n")

        # Plus the inline image (which the uploads-wins branch drops).
        b64 = base64.b64encode(_PNG_BYTES).decode("ascii")
        agent = _FakeChatAgent()
        workers._run_chat_task(
            agent, 1, "see report",
            (b64, "image/png", "shot"),
            task_metadata={
                "chat_attachment_uploads": [
                    {"path": str(pdf_src), "label": "report.pdf", "mime": "application/pdf"},
                ],
            },
        )

        task = agent.task
        assert task is not None
        # Inline keys dropped (same as the pre-fix branch).
        assert task.get("image_base64") is None
        assert task.get("image_mime") is None
        # ONLY the PDF is staged; the inline image is NOT silently double-injected.
        adir = _attach_dir(drive, task["id"])
        staged_names = sorted(p.name for p in adir.iterdir() if p.is_file())
        assert any(n.endswith(".pdf") for n in staged_names)
        assert not any(n.endswith(".png") for n in staged_names)
        # Manifest has ONE read_file line (the PDF), not two.
        assert task["text"].count("read_file(root='artifact_store'") == 1
        # attachment_images is empty (PDF isn't an image; inline dropped).
        imgs = task.get("attachment_images")
        assert imgs == []
        # No leftover inline staging source under data/uploads (the inline path
        # did not run because uploads was non-empty).
        leftover = list((drive / "uploads").iterdir()) if (drive / "uploads").exists() else []
        assert leftover == [], f"unexpected leftover under uploads/: {leftover}"

    def test_image_data_jpeg_mime_routes_to_jpg_suffix(self, tmp_path, monkeypatch):
        """mime=image/jpeg → .jpg suffix on the staging file (not .jpeg)."""
        import supervisor.workers as workers

        drive = _drive(tmp_path)
        monkeypatch.setattr(workers, "DRIVE_ROOT", drive)
        import ouroboros.project_naming as project_naming
        monkeypatch.setattr(project_naming, "spawn_proactive_namer", lambda *a, **k: None)

        b64 = base64.b64encode(_PNG_BYTES).decode("ascii")
        agent = _FakeChatAgent()
        workers._run_chat_task(agent, 1, "x", (b64, "image/jpeg"))

        task = agent.task
        assert task is not None
        adir = _attach_dir(drive, task["id"])
        staged = next(p for p in adir.iterdir() if p.is_file())
        assert staged.suffix == ".jpg"

    def test_image_data_corrupt_b64_falls_back_to_inline(self, tmp_path, monkeypatch):
        """Invalid base64 in image_data: staging fails, legacy inline image_base64
        stays as a degraded fallback (same fallback the rest of the staging
        substrate keeps — never a hard fail on a transient decode error)."""
        import supervisor.workers as workers

        drive = _drive(tmp_path)
        monkeypatch.setattr(workers, "DRIVE_ROOT", drive)
        import ouroboros.project_naming as project_naming
        monkeypatch.setattr(project_naming, "spawn_proactive_namer", lambda *a, **k: None)

        agent = _FakeChatAgent()
        workers._run_chat_task(agent, 1, "x", ("!!!not-valid-base64@@@", "image/png"))

        task = agent.task
        assert task is not None
        # Staging failed → inline base64 left in place as fallback (degraded but
        # the message is not lost).
        assert task.get("image_base64") == "!!!not-valid-base64@@@"
        assert task.get("image_mime") == "image/png"
        assert "attachment_images" not in task
        assert "[ATTACHMENTS]" not in task["text"]

    def test_image_data_oversize_50mb_falls_back_to_inline(self, tmp_path, monkeypatch):
        """>50 MiB inline payload: exceeds the canonical cap, staging skipped,
        legacy inline kept as fallback."""
        import supervisor.workers as workers

        drive = _drive(tmp_path)
        monkeypatch.setattr(workers, "DRIVE_ROOT", drive)
        import ouroboros.project_naming as project_naming
        monkeypatch.setattr(project_naming, "spawn_proactive_namer", lambda *a, **k: None)

        # 50 MiB + 1 byte worth of b64 (caps the inner size guard exactly at the limit).
        huge = b"\x00" * (50 * 1024 * 1024 + 1)
        b64 = base64.b64encode(huge).decode("ascii")
        agent = _FakeChatAgent()
        workers._run_chat_task(agent, 1, "x", (b64, "image/png"))

        task = agent.task
        assert task is not None
        # Oversize → staging skipped → inline base64 stays.
        assert task.get("image_base64") == b64
        assert "attachment_images" not in task

    def test_no_image_data_no_uploads_no_staging(self, tmp_path, monkeypatch):
        """Neither image_data nor chat_attachment_uploads: no staging at all,
        drive_root / attachment_images / [ATTACHMENTS] all absent. Pure text
        path. The (image attached) fallback fires when text is empty."""
        import supervisor.workers as workers

        drive = _drive(tmp_path)
        monkeypatch.setattr(workers, "DRIVE_ROOT", drive)
        import ouroboros.project_naming as project_naming
        monkeypatch.setattr(project_naming, "spawn_proactive_namer", lambda *a, **k: None)

        agent = _FakeChatAgent()
        workers._run_chat_task(agent, 1, "hello", None)

        task = agent.task
        assert task is not None
        assert task["text"] == "hello"
        assert "attachment_images" not in task
        assert "[ATTACHMENTS]" not in task["text"]


class TestChatAttachmentUploads:
    """ws._chat_attachment_uploads: confine to validated data/uploads/ basenames."""

    def _setup(self, tmp_path, monkeypatch):
        import ouroboros.gateway.ws as ws

        data_dir = tmp_path / "data"
        uploads = data_dir / "uploads"
        uploads.mkdir(parents=True)
        monkeypatch.setattr(ws, "DATA_DIR", str(data_dir))
        return ws, uploads

    def test_resolves_validated_uploads(self, tmp_path, monkeypatch):
        ws, uploads = self._setup(tmp_path, monkeypatch)
        (uploads / "abc_photo.png").write_bytes(_PNG_BYTES)
        (uploads / "def_report.pdf").write_bytes(b"%PDF-1.4\n")

        specs = ws._chat_attachment_uploads([
            {"filename": "abc_photo.png", "display_name": "photo.png", "mime": "image/png"},
            {"filename": "def_report.pdf", "display_name": "report.pdf", "mime": "application/pdf"},
        ])
        assert len(specs) == 2
        assert all(s["path"].startswith(str(uploads)) for s in specs)
        labels = {s["label"] for s in specs}
        assert labels == {"photo.png", "report.pdf"}

    def test_rejects_traversal_and_missing(self, tmp_path, monkeypatch):
        ws, uploads = self._setup(tmp_path, monkeypatch)
        # A secret OUTSIDE uploads that a traversal attachment would try to read.
        secret = tmp_path / "secret.txt"
        secret.write_text("top secret", encoding="utf-8")
        (uploads / "present.txt").write_text("ok", encoding="utf-8")

        specs = ws._chat_attachment_uploads([
            {"filename": "../secret.txt", "display_name": "x"},        # traversal: basename'd -> secret.txt, not in uploads
            {"filename": "/etc/passwd", "display_name": "y"},          # absolute: basename'd -> passwd, missing
            {"filename": "missing.txt", "display_name": "z"},          # not on disk
            {"filename": "present.txt", "display_name": "present"},    # the only valid one
        ])
        assert len(specs) == 1
        assert specs[0]["label"] == "present"
        assert specs[0]["path"] == str((uploads / "present.txt").resolve(strict=False))

    def test_non_list_returns_empty(self, tmp_path, monkeypatch):
        ws, _ = self._setup(tmp_path, monkeypatch)
        assert ws._chat_attachment_uploads(None) == []
        assert ws._chat_attachment_uploads("nope") == []
        assert ws._chat_attachment_uploads([]) == []


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

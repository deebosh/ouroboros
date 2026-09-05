"""The `memory/self.md` personal layer and the `selfhood` instruction skill.

`self.md` is Lynn's space for what she wants and is drawn to, distinct from the
identity manifesto. It is tier-0 (always in context), tended via the append-only
`update_self` tool (never the corruption-prone whole-file `update_identity`), and
its stance lives in a reviewed instruction skill.
"""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_MD = REPO_ROOT / "skills" / "selfhood" / "SKILL.md"


# --------------------------------------------------------------------------- #
# memory/self.md
# --------------------------------------------------------------------------- #

def test_load_self_seeds_default_when_absent(tmp_path):
    from ouroboros.memory import Memory

    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    memory = Memory(drive_root=tmp_path)

    text = memory.load_self()

    assert text.strip()
    assert memory.self_path().exists()
    assert "changelog" in text  # the default states what it is NOT
    assert "status" not in text.lower()  # a seed, not a template to fill in
    assert "LINN.md" in text and "LIBRARY.md" in text  # names the departure point


def test_load_self_returns_file_content_when_present(tmp_path):
    from ouroboros.memory import Memory

    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memory" / "self.md").write_text(
        "# Self\n\nI keep coming back to how parsers recover from torn input.\n",
        encoding="utf-8",
    )
    memory = Memory(drive_root=tmp_path)

    assert "torn input" in memory.load_self()


def test_ensure_files_creates_self(tmp_path):
    from ouroboros.memory import Memory

    memory = Memory(drive_root=tmp_path)
    memory.ensure_files()

    assert memory.self_path().exists()


# --------------------------------------------------------------------------- #
# context assembly
# --------------------------------------------------------------------------- #

def test_self_section_present_in_stable_memory(tmp_path):
    from ouroboros.context import build_memory_sections
    from ouroboros.memory import Memory

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "self.md").write_text(
        "# Self\n\nI want a debugger that thinks in dataflow, not lines.\n",
        encoding="utf-8",
    )
    memory = Memory(drive_root=tmp_path)

    combined = "\n\n".join(build_memory_sections(memory, partition="stable"))

    assert "## Self (from `memory/self.md`" in combined
    assert "thinks in dataflow" in combined
    assert "do not re-read" in combined
    # the write mechanism: the dedicated append tool, never the whole-file identity tool
    assert "update_self" in combined
    assert "never `update_identity`" in combined
    # the section names the given character as the departure point
    assert "LINN.md" in combined and "LIBRARY.md" in combined
    assert "root='system_repo'" in combined


def test_self_section_absent_from_volatile_partition(tmp_path):
    from ouroboros.context import build_memory_sections
    from ouroboros.memory import Memory

    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    memory = Memory(drive_root=tmp_path)

    combined = "\n\n".join(build_memory_sections(memory, partition="volatile"))

    assert "memory/self.md" not in combined


def test_self_is_tier0_always_full():
    from ouroboros import context_layout as cl

    assert "self" in cl.TIER0_ALWAYS_FULL


def test_self_has_a_section_budget():
    from ouroboros.context import _SECTION_BUDGETS

    assert _SECTION_BUDGETS.get("self")


# --------------------------------------------------------------------------- #
# the selfhood skill
# --------------------------------------------------------------------------- #

def test_selfhood_manifest_parses_as_instruction():
    from ouroboros.contracts.skill_manifest import parse_skill_manifest_text

    manifest = parse_skill_manifest_text(SKILL_MD.read_text(encoding="utf-8"))

    assert manifest.name == "selfhood"
    assert manifest.is_instruction()
    assert not manifest.entry
    assert not manifest.scripts
    assert not manifest.permissions
    assert not manifest.scheduled_tasks
    assert manifest.when_to_use


def test_selfhood_manifest_has_no_validation_warnings():
    from ouroboros.contracts.skill_manifest import parse_skill_manifest_text

    manifest = parse_skill_manifest_text(SKILL_MD.read_text(encoding="utf-8"))

    assert manifest.validate() == []


def test_selfhood_body_states_the_stance_not_a_schedule():
    raw = SKILL_MD.read_text(encoding="utf-8").lower()
    body = " ".join(raw.split())  # collapse line wraps

    assert "never `update_identity`" in body
    assert "not because time passed" in body
    assert "not a procedure to run" in body
    # the write mechanism is the dedicated append tool, and it explains why
    assert "update_self" in body
    assert "append" in body
    # the stance names the given character as the starting point, not a blank slate
    assert "where i start from" in body
    assert "linn.md" in body and "library.md" in body
    assert "departure point, not a cage" in body

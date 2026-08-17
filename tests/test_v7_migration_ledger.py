"""Migration-ledger membership: every extraction the v7 branch performed is a row.

Split out of tests/test_v7_prologue_evidence.py so that module stays inside the size
ratchet as the ledger grows stream by stream. The assertions are unchanged.
"""

from __future__ import annotations

import importlib.util
import pathlib
import tests._v7_ledger_inventories as _inv

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO / "scripts" / "v7_evidence.py"
SPEC = importlib.util.spec_from_file_location("v7_evidence", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
v7_evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(v7_evidence)
v7_migration = v7_evidence._migration


def test_migration_table_is_valid_and_uses_only_spec_approved_pending_owners():
    assert v7_evidence.validate_migration(REPO) == []
    rows = v7_evidence._parse_migration(REPO / "MIGRATION_v7.md")
    assert len({row["old path/symbol"] for row in rows}) == len(rows)
    implemented = {
        "ouroboros/tools/registry.py::BrowserState": "ouroboros/tools/tool_context.py::BrowserState",
        "ouroboros/tools/registry.py::ToolContext": "ouroboros/tools/tool_context.py::ToolContext",
        "ouroboros/tools/registry.py::ToolEntry": "ouroboros/tools/tool_catalog.py::ToolEntry",
        "ouroboros/tools/registry.py::_compose_execute_result":
            "ouroboros/tools/tool_result.py::_compose_execute_result",
        "ouroboros/tools/registry.py::_coerce_real_path":
            "ouroboros/tools/tool_resolution.py::_coerce_real_path",
        "ouroboros/tools/registry.py::active_repo_dir_for":
            "ouroboros/tools/tool_resolution.py::active_repo_dir_for",
        "ouroboros/tools/registry.py::system_repo_dir_for":
            "ouroboros/tools/tool_resolution.py::system_repo_dir_for",
        "ouroboros/tools/registry.py::_PATH_NORMALIZED_TOOLS":
            "ouroboros/tools/tool_resolution.py::_PATH_NORMALIZED_TOOLS",
        "ouroboros/tools/registry.py::_normalize_dispatch_path_args":
            "ouroboros/tools/tool_resolution.py::_normalize_dispatch_path_args",
        "ouroboros/tools/registry.py::_GENERIC_VCS_TARGET_TOOLS":
            "ouroboros/tools/tool_resolution.py::_GENERIC_VCS_TARGET_TOOLS",
        "ouroboros/tools/registry.py::_TARGET_BINDING_OPERATIONS":
            "ouroboros/tools/tool_resolution.py::_TARGET_BINDING_OPERATIONS",
        "ouroboros/tools/registry.py::_SKILL_LIFECYCLE_TARGET_TOOLS":
            "ouroboros/tools/tool_resolution.py::_SKILL_LIFECYCLE_TARGET_TOOLS",
        "ouroboros/tools/registry.py::_PROCESS_TARGET_TOOLS":
            "ouroboros/tools/tool_resolution.py::_PROCESS_TARGET_TOOLS",
        "ouroboros/tools/registry.py::_VERIFY_RUN_KINDS":
            "ouroboros/tools/tool_resolution.py::_VERIFY_RUN_KINDS",
        "ouroboros/tools/registry.py::_target_binding_operation":
            "ouroboros/tools/tool_resolution.py::_target_binding_operation",
        "ouroboros/tools/registry.py::_build_builtin_target_binding":
            "ouroboros/tools/tool_resolution.py::_build_builtin_target_binding",
        "ouroboros/tools/registry.py::_binding_items":
            "ouroboros/tools/tool_resolution.py::_binding_items",
        "ouroboros/tools/registry.py::_binding_set_targets_system_repo":
            "ouroboros/tools/tool_resolution.py::_binding_set_targets_system_repo",
        "ouroboros/tools/registry.py::_binding_set_is_light_restricted":
            "ouroboros/tools/tool_resolution.py::_binding_set_is_light_restricted",
        "ouroboros/tools/registry.py::_binding_state_drive_root":
            "ouroboros/tools/tool_resolution.py::_binding_state_drive_root",
        "ouroboros/tools/registry.py::_detect_runtime_mode_elevation":
            "ouroboros/tools/registry_guard_process.py::_detect_runtime_mode_elevation",
        "ouroboros/tools/registry.py::_SUBAGENT_SHELL_SECRET_MARKERS":
            "ouroboros/tools/registry_guard_process.py::_SUBAGENT_SHELL_SECRET_MARKERS",
        "ouroboros/tools/registry.py::_subagent_shell_targets_secret":
            "ouroboros/tools/registry_guard_process.py::_subagent_shell_targets_secret",
        "ouroboros/tools/registry.py::_detect_mutative_toggle_self_change":
            "ouroboros/tools/registry_guard_process.py::_detect_mutative_toggle_self_change",
        "ouroboros/tools/registry.py::_detect_evolution_owner_control_self_change":
            "ouroboros/tools/registry_guard_process.py::_detect_evolution_owner_control_self_change",
        "ouroboros/tools/registry.py::_detect_context_mode_self_lowering":
            "ouroboros/tools/registry_guard_process.py::_detect_context_mode_self_lowering",
        "ouroboros/tools/registry.py::_READ_ONLY_INSPECTION_COMMANDS":
            "ouroboros/tools/registry_guard_process.py::_READ_ONLY_INSPECTION_COMMANDS",
        "ouroboros/tools/registry.py::_COMMAND_HEAD_WRAPPERS":
            "ouroboros/tools/registry_guard_process.py::_COMMAND_HEAD_WRAPPERS",
        "ouroboros/tools/registry.py::_READ_ONLY_GIT_SUBCOMMANDS":
            "ouroboros/tools/registry_guard_process.py::_READ_ONLY_GIT_SUBCOMMANDS",
        "ouroboros/tools/registry.py::_SEARCH_TOOL_EXEC_OPTIONS":
            "ouroboros/tools/registry_guard_process.py::_SEARCH_TOOL_EXEC_OPTIONS",
        "ouroboros/tools/registry.py::_DENIED_READ_OPTIONS":
            "ouroboros/tools/registry_guard_process.py::_DENIED_READ_OPTIONS",
        "ouroboros/tools/registry.py::_TRUSTED_EXECUTABLE_DIRS":
            "ouroboros/tools/registry_guard_process.py::_TRUSTED_EXECUTABLE_DIRS",
        "ouroboros/tools/registry.py::_trusted_read_head":
            "ouroboros/tools/registry_guard_process.py::_trusted_read_head",
        "ouroboros/tools/registry.py::_denied_read_option":
            "ouroboros/tools/registry_guard_process.py::_denied_read_option",
        "ouroboros/tools/registry.py::_NESTED_EXECUTION_MARKERS":
            "ouroboros/tools/registry_guard_process.py::_NESTED_EXECUTION_MARKERS",
        "ouroboros/tools/registry.py::_NESTED_EXECUTION_TOKENS":
            "ouroboros/tools/registry_guard_process.py::_NESTED_EXECUTION_TOKENS",
        "ouroboros/tools/registry.py::_is_pure_read_inspection":
            "ouroboros/tools/registry_guard_process.py::_is_pure_read_inspection",
        "ouroboros/tools/registry.py::_detect_scope_review_floor_self_lowering":
            "ouroboros/tools/registry_guard_process.py::_detect_scope_review_floor_self_lowering",
        "ouroboros/tools/registry.py::_detect_safety_mode_self_lowering":
            "ouroboros/tools/registry_guard_process.py::_detect_safety_mode_self_lowering",
        "ouroboros/tools/registry.py::_detect_owner_skill_attest_self_call":
            "ouroboros/tools/registry_guard_process.py::_detect_owner_skill_attest_self_call",
        "ouroboros/tools/registry.py::_SKILL_OWNER_STATE_STEMS":
            "ouroboros/tools/registry_guard_process.py::_SKILL_OWNER_STATE_STEMS",
        "ouroboros/tools/registry.py::_DETACHED_PROCESS_MARKERS":
            "ouroboros/tools/registry_guard_process.py::_DETACHED_PROCESS_MARKERS",
        "ouroboros/tools/registry.py::_mentions_skill_owner_state":
            "ouroboros/tools/registry_guard_process.py::_mentions_skill_owner_state",
        "ouroboros/tools/registry.py::_mentions_detached_process":
            "ouroboros/tools/registry_guard_process.py::_mentions_detached_process",
        "ouroboros/tools/registry.py::ToolRegistry._run_shell_safety_check":
            "ouroboros/tools/registry_guard_process.py::_run_shell_safety_check",
        "ouroboros/tools/registry.py::_light_repo_snapshot":
            "ouroboros/tools/registry_guard_process.py::_light_repo_snapshot",
        "ouroboros/tools/registry.py::_format_light_repo_write_block":
            "ouroboros/tools/registry_guard_process.py::_format_light_repo_write_block",
        "ouroboros/tools/registry.py::_git_ref_snapshot":
            "ouroboros/tools/registry_guard_process.py::_git_ref_snapshot",
        "ouroboros/tools/registry.py::ToolRegistry._snapshot_owner_files":
            "ouroboros/tools/registry_guard_process.py::_snapshot_owner_files",
        "ouroboros/tools/registry.py::ToolRegistry._restore_owner_files":
            "ouroboros/tools/registry_guard_process.py::_restore_owner_files",
        "ouroboros/tools/registry.py::ToolRegistry._run_shell_post_checks":
            "ouroboros/tools/registry_guard_process.py::_run_shell_post_checks",
        "tests/test_skill_exec.py::test_run_shell_restores_obfuscated_self_authored_state_marker":
            "tests/test_registry_guard_process.py::test_run_shell_restores_obfuscated_self_authored_state_marker",
        "ouroboros/tools/registry.py::SKILL_OWNER_STATE_FILENAMES":
            "ouroboros/contracts/skill_payload_policy.py::SKILL_OWNER_STATE_FILENAMES",
        "ouroboros/tools/registry.py::parse_porcelain_paths":
            "ouroboros/tools/shell_guards.py::parse_porcelain_paths",
        "ouroboros/tools/registry.py::safe_relpath":
            "ouroboros/utils.py::safe_relpath",
        "ouroboros/tools/registry.py::LIGHT_SHELL_WRITER_COMMANDS":
            "ouroboros/tools/shell_guards.py::LIGHT_SHELL_WRITER_COMMANDS",
        "ouroboros/tools/registry.py::SKILL_OWNER_STATE_STEMS":
            "ouroboros/contracts/skill_payload_policy.py::SKILL_OWNER_STATE_STEMS",
        "ouroboros/tools/registry.py::build_resolved_resource_binding":
            "ouroboros/tool_access.py::build_resolved_resource_binding",
        "ouroboros/tools/registry.py::interpreter_family":
            "ouroboros/tools/shell_guards.py::interpreter_family",
        "ouroboros/tools/registry.py::light_shell_repo_mutation":
            "ouroboros/tools/shell_guards.py::light_shell_repo_mutation",
        "ouroboros/tools/registry.py::protected_artifact_shell_block_reason":
            "ouroboros/protected_artifacts.py::shell_block_reason",
        "ouroboros/tools/registry.py::runtime_data_guard_targets":
            "ouroboros/tools/shell_guards.py::runtime_data_guard_targets",
        "ouroboros/tools/registry.py::shell_command_string":
            "ouroboros/shell_parse.py::shell_command_string",
        "ouroboros/tools/registry.py::strip_leading_env_assignments":
            "ouroboros/shell_parse.py::strip_leading_env_assignments",
        "ouroboros/tools/registry.py::sudo_noninteractive_violation":
            "ouroboros/shell_parse.py::sudo_noninteractive_violation",
        "ouroboros/tools/registry.py::unwrap_env_argv":
            "ouroboros/shell_parse.py::unwrap_env_argv",
        "ouroboros/tools/registry.py::workspace_executor_state_write_block":
            "ouroboros/tools/shell_guards.py::workspace_executor_state_write_block",
        "ouroboros/tools/registry.py::writer_target_tokens":
            "ouroboros/tools/shell_guards.py::writer_target_tokens",
        "ouroboros/tools/registry.py::_EPHEMERAL_ALLOWED_TOOLS":
            "ouroboros/tools/registry_guards.py::_EPHEMERAL_ALLOWED_TOOLS",
        "ouroboros/tools/registry.py::_WEB_TOOLS":
            "ouroboros/tools/registry_guards.py::_WEB_TOOLS",
        "ouroboros/tools/registry.py::_resource_allowed":
            "ouroboros/tools/registry_guards.py::_resource_allowed",
        "ouroboros/tools/registry.py::_disabled_tools":
            "ouroboros/tools/registry_guards.py::_disabled_tools",
        "ouroboros/tools/registry.py::_GITHUB_TOKEN_TOOLS":
            "ouroboros/tools/registry_guards.py::_GITHUB_TOKEN_TOOLS",
        "ouroboros/tools/registry.py::_builtin_tool_availability":
            "ouroboros/tools/registry_guards.py::_builtin_tool_availability",
        "ouroboros/tools/registry.py::ToolRegistry._ephemeral_block":
            "ouroboros/tools/registry_guards.py::_ephemeral_block_result",
        "ouroboros/tools/registry.py::ToolRegistry._subagent_and_update_gate":
            "ouroboros/tools/registry_guards.py::_subagent_and_update_guard_result",
        "ouroboros/tools/registry.py::_managed_update_code_tool_block":
            "ouroboros/tools/registry_guards.py::_managed_update_code_tool_block",
        "ouroboros/tools/registry.py::_HEAL_MODE_ALLOWED_TOOLS":
            "ouroboros/tools/registry_guards.py::_HEAL_MODE_ALLOWED_TOOLS",
        "ouroboros/tools/registry.py::_task_constraint_path_allowed":
            "ouroboros/tools/registry_guards.py::_task_constraint_path_allowed",
        "ouroboros/tools/registry.py::_heal_protected_payload_sidecar":
            "ouroboros/tools/registry_guards.py::_heal_protected_payload_sidecar",
        "ouroboros/tools/registry.py::ToolRegistry._heal_mode_block":
            "ouroboros/tools/registry_guards.py::_heal_mode_guard_result",
        "ouroboros/tools/registry.py::_executor_backend_candidate_allowed":
            "ouroboros/tools/registry_guards.py::_executor_backend_candidate_allowed",
        "ouroboros/tools/registry.py::_command_mentions_protected_root":
            "ouroboros/tools/registry_guards.py::_command_mentions_protected_root",
        "ouroboros/tools/registry.py::_authorized_managed_update_resolver":
            "ouroboros/tools/registry_guards.py::_authorized_managed_update_resolver",
        "ouroboros/tools/registry.py::_light_mode_payload_mutation_allowed":
            "ouroboros/tools/registry_guards.py::_light_mode_payload_mutation_allowed",
        "ouroboros/tools/registry.py::ToolRegistry._protected_shell_block":
            "ouroboros/tools/registry_guards.py::_protected_shell_block",
        "ouroboros/tools/registry.py::ToolRegistry._git_protected_roots":
            "ouroboros/tools/registry_guards.py::_git_protected_roots",
        "ouroboros/tools/registry.py::ToolRegistry._resolved_shell_cwd":
            "ouroboros/tools/registry_guards.py::_resolved_shell_cwd",
        "ouroboros/tools/registry.py::ToolRegistry._external_workspace_git_block":
            "ouroboros/tools/registry_guards.py::_external_workspace_git_block",
        "ouroboros/tools/registry.py::ToolRegistry._external_runtime_protected_paths":
            "ouroboros/tools/registry_guards.py::_external_runtime_protected_paths",
        "ouroboros/tools/registry.py::ToolRegistry._external_shell_runtime_or_secret_block":
            "ouroboros/tools/registry_guards.py::_external_shell_runtime_or_secret_block",
        "ouroboros/tools/registry.py::ToolRegistry._workspace_shell_write_block":
            "ouroboros/tools/registry_guards.py::_workspace_shell_write_block",
        "ouroboros/tools/registry.py::ToolRegistry._shell_git_and_runtime_block":
            "ouroboros/tools/registry_guards.py::_shell_git_and_runtime_block",
        "tests/test_external_workspace_access.py::_command_mentions_protected_root":
            "ouroboros/tools/registry_guards.py::_command_mentions_protected_root",
        "ouroboros/tools/registry.py::PROTECTED_RUNTIME_PATHS":
            "ouroboros/runtime_mode_policy.py::PROTECTED_RUNTIME_PATHS",
        "ouroboros/tools/registry.py::task_artifact_dir_path":
            "ouroboros/artifacts.py::task_artifact_dir_path",
        "ouroboros/tools/registry.py::task_id_for_artifacts":
            "ouroboros/artifacts.py::task_id_for_artifacts",
        "ouroboros/tools/registry.py::run_shell_git_block_reason":
            "ouroboros/git_shell_policy.py::run_shell_git_block_reason",
        "ouroboros/tools/registry.py::workspace_git_safety_violation":
            "ouroboros/git_shell_policy.py::workspace_git_safety_violation",
        "ouroboros/tools/registry.py::is_absolute_path_text":
            "ouroboros/shell_parse.py::is_absolute_path_text",
        "ouroboros/tools/registry.py::path_text_is_inside":
            "ouroboros/shell_parse.py::path_text_is_inside",
        "ouroboros/tools/registry.py::shell_argv":
            "ouroboros/shell_parse.py::shell_argv",
        "ouroboros/tools/registry.py::shell_argv_with_path_tokens":
            "ouroboros/shell_parse.py::shell_argv_with_path_tokens",
        "ouroboros/tools/registry.py::PROTECTED_RUNTIME_PATHS_LOWER":
            "ouroboros/tools/shell_guards.py::PROTECTED_RUNTIME_PATHS_LOWER",
        "ouroboros/tools/registry.py::shell_has_write_indicator":
            "ouroboros/tools/shell_guards.py::shell_has_write_indicator",
        "ouroboros/tools/registry.py::shell_writer_targets_protected":
            "ouroboros/tools/shell_guards.py::shell_writer_targets_protected",
        "ouroboros/tools/registry.py::is_external_workspace":
            "ouroboros/tool_access.py::is_external_workspace",
        "ouroboros/tools/registry.py::normalize_root":
            "ouroboros/tool_access.py::normalize_root",
        "ouroboros/tools/registry.py::resolve_shell_cwd":
            "ouroboros/tool_access.py::resolve_shell_cwd",
        "ouroboros/tools/registry.py::SKILL_PAYLOAD_CONTROL_DIRNAMES":
            "ouroboros/contracts/skill_payload_policy.py::SKILL_PAYLOAD_CONTROL_DIRNAMES",
        "ouroboros/tools/registry.py::is_skill_payload_path":
            "ouroboros/contracts/skill_payload_policy.py::is_skill_payload_path",
        "ouroboros/tools/registry.py::resolve_skill_payload_target":
            "ouroboros/contracts/skill_payload_policy.py::resolve_skill_payload_target",
    }
    registry_core_symbols = """ToolRegistry log _PROCESS_COMMAND_TOOLS
        _SHELL_GUARDED_TOOLS _REPO_MUTATION_TOOLS
        _SYSTEM_INTRINSIC_REPO_MUTATION_TOOLS""".split()
    registry_core_rows = {
        f"ouroboros/tools/registry.py::{symbol}":
            f"ouroboros/tools/registry_core.py::{symbol}"
        for symbol in registry_core_symbols
    }
    registry_resolution_symbols = """_ROOT_ARG_REPO_WRITE_TOOLS _payload_write_paths
        _TOOL_ARG_ALIASES _IGNORE_ROOT_ARG_TOOLS _handler_public_params
        _entry_public_params _entry_has_public_param_schema _normalize_tool_call_args
        _prepare_public_builtin_args _light_binding_failure_redirect _binding_error_text
        _format_tool_arg_error""".split()
    registry_resolution_rows = {
        f"ouroboros/tools/registry.py::{symbol}":
            f"ouroboros/tools/tool_resolution.py::{symbol}"
        for symbol in registry_resolution_symbols
    }
    registry_guard_rows = {
        "ouroboros/tools/registry.py::_stray_skill_payload_failsoft":
            "ouroboros/tools/registry_guards.py::_stray_skill_payload_failsoft",
        "ouroboros/tools/registry.py::_payload_dispatch_constraint":
            "ouroboros/tools/registry_guards.py::_payload_dispatch_constraint",
    }
    registry_dispatch_method_rows = {
        "ouroboros/tools/registry.py::ToolRegistry._dispatch_mcp_tool":
            "ouroboros/tools/extension_dispatch.py::_dispatch_mcp_tool_result",
        "ouroboros/tools/registry.py::ToolRegistry._dispatch_extension_tool":
            "ouroboros/tools/extension_dispatch.py::_dispatch_extension_tool_result",
        "ouroboros/tools/registry.py::ToolRegistry._resolve_python_predispatch":
            "ouroboros/tools/tool_resolution.py::_resolve_python_predispatch",
    }
    dependency_symbols_by_owner = {
        "ouroboros/tool_capabilities.py": "ACTING_SUBAGENT_MODE ACTING_SUBAGENT_TOOL_NAMES CORE_TOOL_NAMES LOCAL_READONLY_SUBAGENT_MODE LOCAL_READONLY_SUBAGENT_TOOL_NAMES META_TOOL_NAMES",
        "ouroboros/contracts/skill_payload_policy.py": "SKILL_PAYLOAD_CONTROL_FILENAMES constraint_bucket_skill cross_skill_redirect_error decide_payload_short_form is_skill_payload_control_filename synthesize_payload_constraint",
        "ouroboros/contracts/task_constraint.py": "TaskConstraint VALID_WRITE_SURFACES normalize_task_constraint",
        "ouroboros/tool_access.py": "UserFilesPathBlockedError binding_targets_system_repo canonical_repo_relative_path light_cognitive_or_root_redirect normalize_root_relative shell_cwd_block_message workspace_mode_block_reason",
        "ouroboros/runtime_mode_policy.py": "mode_allows_protected_write protected_paths_in protected_write_block_message",
        "ouroboros/tools/shell_guards.py": "process_shell_guard_args",
        "ouroboros/python_interpreter.py": "record_python_resolution resolve_process_python",
    }
    registry_dependency_owners = {
        f"ouroboros/tools/registry.py::{symbol}": f"{owner}::{symbol}"
        for owner, symbols in dependency_symbols_by_owner.items()
        for symbol in symbols.split()
    }
    core_extraction_rows = {f"ouroboros/tools/core.py::{symbol}": f"ouroboros/tools/{'core_artifacts.py' if symbol in '_MAX_PHOTO_FILE_BYTES _detect_image_mime _send_photo _MAX_VIDEO_FILE_BYTES _detect_video_mime _send_video _MAX_DOCUMENT_FILE_BYTES _detect_document_mime _send_file'.split() else 'core_file_tools.py'}::{symbol}" for symbol in "_SKILL_OWNER_STATE_FILENAMES _direct_resource_binding _render_line_slice _coerce_start_char _coerce_line_window _is_cognitive_data_path _is_skill_owner_state_target _ListingFailure _list_dir _list_user_files_dir _SUBAGENT_SECRET_FILE_NAMES is_restricted_subagent_profile _is_subagent_secret_data_path _is_subagent_secret_repo_path _is_subagent_secret_repo_target _filter_subagent_secret_repo_listing _filter_subagent_secret_listing _MEMORY_AT_DRIVE_MEMORY _repo_read _repo_list _normalize_data_read_path _data_read _data_list _profile_roots_hint _access_or_block _local_readonly_resource_block _root_display_path _annotate_reread _read_file _list_files _MAX_PHOTO_FILE_BYTES _detect_image_mime _send_photo _MAX_VIDEO_FILE_BYTES _detect_video_mime _send_video _MAX_DOCUMENT_FILE_BYTES _detect_document_mime _send_file".split()} | {"ouroboros/tools/core.py::_code_search": "ouroboros/tools/core.py::_code_search"} | {"ouroboros/tools/core.py::_filter_out_project_store": "ouroboros/project_facts.py::filter_out_project_store", "ouroboros/tools/core.py::_policy_is_skill_owner_state_target": "ouroboros/contracts/skill_payload_policy.py::is_skill_owner_state_target", "ouroboros/tools/core.py::active_repo_dir_for": "ouroboros/tools/tool_resolution.py::active_repo_dir_for", "ouroboros/tools/core.py::active_tool_profile": "ouroboros/tool_access.py::active_tool_profile", "ouroboros/tools/core.py::build_resolved_resource_binding": "ouroboros/tool_access.py::build_resolved_resource_binding", "ouroboros/tools/core.py::decide_tool_access": "ouroboros/tool_access.py::decide_tool_access", "ouroboros/tools/core.py::normalize_root": "ouroboros/tool_access.py::normalize_root", "ouroboros/tools/core.py::normalize_runtime_data_path": "ouroboros/tool_access.py::normalize_runtime_data_path", "ouroboros/tools/core.py::read_text": "ouroboros/utils.py::read_text", "ouroboros/tools/core.py::SKILL_OWNER_STATE_FILENAMES": "ouroboros/contracts/skill_payload_policy.py::SKILL_OWNER_STATE_FILENAMES", "ouroboros/tools/browser.py::_readonly_subagent": "ouroboros/tools/core_file_tools.py::is_restricted_subagent_profile", "tests/test_filesystem_root_observability.py::_read_file": "ouroboros/tools/core_file_tools.py::_read_file", "tests/test_headless_cli.py::_repo_read": "ouroboros/tools/core_file_tools.py::_repo_read", "tests/test_send_file.py::_MAX_DOCUMENT_FILE_BYTES": "ouroboros/tools/core_artifacts.py::_MAX_DOCUMENT_FILE_BYTES", "tests/test_send_file.py::_detect_document_mime": "ouroboros/tools/core_artifacts.py::_detect_document_mime", "tests/test_send_file.py::_send_file": "ouroboros/tools/core_artifacts.py::_send_file", "tests/test_send_photo.py::_MAX_PHOTO_FILE_BYTES": "ouroboros/tools/core_artifacts.py::_MAX_PHOTO_FILE_BYTES", "tests/test_send_photo.py::_detect_image_mime": "ouroboros/tools/core_artifacts.py::_detect_image_mime", "tests/test_send_photo.py::_send_photo": "ouroboros/tools/core_artifacts.py::_send_photo", "tests/test_send_video.py::_MAX_VIDEO_FILE_BYTES": "ouroboros/tools/core_artifacts.py::_MAX_VIDEO_FILE_BYTES", "tests/test_send_video.py::_detect_video_mime": "ouroboros/tools/core_artifacts.py::_detect_video_mime", "tests/test_send_video.py::_send_video": "ouroboros/tools/core_artifacts.py::_send_video"}
    git_extraction_symbols_by_owner = {
        "git_plumbing.py": "_current_runtime_mode _protected_paths_block_message _sanitize_git_error _BINARY_EXTENSIONS _ensure_gitignore _unstage_binaries _acquire_git_lock _release_git_lock _binding_repo_rel _binding_targets_system_repo",
        "git_review_cycle.py": "_fingerprint_staged_diff _review_binding_precondition_error _verify_reviewed_commit_binding _handle_revalidation_failure _finalize_blocked_review _DOC_ONLY_EXTENSIONS _diff_is_doc_only _mark_failed_bypass_advisory_stale _refuse_capped_attempt _review_cycle_infra_failure _stage_candidate_for_review _run_reviewed_stage_cycle _run_non_committing_review_cycle",
        "git_evolution.py": "_evolution_commit_authority _check_evolution_commit_stage _preserve_evolution_orphan _record_evolution_commit_receipt _evolution_publication_stopped_result",
        "git_repo_edit.py": "_CONTENT_OMITTED_PREFIX _check_shrink_guard _repo_write _str_replace_editor",
        "git_vcs_ops.py": "_limit_git_output _vcs_binding _vcs_result _binding_relative_path _git_status _git_diff _ff_pull _pull_from_remote _restore_to_head _revert_commit",
    }
    git_extraction_rows = {
        f"ouroboros/tools/git.py::{symbol}": f"ouroboros/tools/{owner}::{symbol}"
        for owner, symbols in git_extraction_symbols_by_owner.items()
        for symbol in symbols.split()
    }
    shell_extraction_symbols_by_owner = {
        "shell_process.py": "_RUN_SHELL_DEFAULT_TIMEOUT_SEC _active_subprocesses _subprocess_lock _tracked_subprocess_run _kill_process_group kill_all_tracked_subprocesses _shell_env_for_cwd _resolve_effective_timeout _describe_returncode _format_process_output _executor_can_run_cwd",
        "shell_outputs.py": "_OUTPUT_DIR_MAX_FILES _OUTPUT_DIR_MAX_BYTES _allowed_output_roots _protected_output_source_reason _changed_path_covers _resolve_declared_output _directory_fingerprint_from_entries _bounded_directory_fingerprint _fingerprint_output _snapshot_declared_outputs _scan_directory_output_members _register_process_outputs _UNDECLARED_OUTPUTS_MARKER _SENSITIVE_OUTPUT_NAMES _SENSITIVE_OUTPUT_SUFFIXES _SENSITIVE_OUTPUT_MARKERS _SENSITIVE_OUTPUT_COMPONENT_NAMES _sensitive_output_component_reason _OUTPUT_CALL_PATH_RE _OUTPUT_REDIRECT_PATH_RE _EMBEDDED_OUTPUT_PATH_RE _USER_FILE_WRITE_CALL_RE _USER_FILE_OPEN_WRITE_CALL_RE _USER_FILE_REDIRECT_RE _OUTPUT_STAT_SLACK_SEC _mentioned_user_file_outputs_without_declaration",
        "shell_effects.py": "_resolve_git_root _status_snapshot _shallow_listing _user_files_run_had_effect _protected_runtime_dirty_paths _restore_protected_runtime_paths _tree_fingerprint _resolve_scratch_abs _scratch_safety_reason _record_scratch_fingerprints _get_changed_files _get_diff_stat",
    }
    shell_extraction_rows = {
        f"ouroboros/tools/shell.py::{symbol}": f"ouroboros/tools/{owner}::{symbol}"
        for owner, symbols in shell_extraction_symbols_by_owner.items()
        for symbol in symbols.split()
    }
    headless_extraction_symbols_by_owner = {
        "headless_status.py": "ARTIFACT_STATUS_PENDING ARTIFACT_STATUS_FINALIZING ARTIFACT_STATUS_READY ARTIFACT_STATUS_READY_WITH_CHANGES ARTIFACT_STATUS_READY_NO_CHANGES ARTIFACT_STATUS_MISSING ARTIFACT_STATUS_FAILED ARTIFACT_TERMINAL_STATUSES _FINAL_STATUSES _LOCAL_READONLY_SUBAGENT_MODE _ARTIFACT_LIFECYCLE_FIELDS",
        "workspace_patch_capture.py": "SCRATCH_MANIFEST_NAME _GIT_UNBORN_HEAD build_workspace_patch write_workspace_patch_artifacts _git_stdout _workspace_patch_base _git_empty_tree_oid _head_reflog_exists _looks_like_git_oid _git_path_list _git_bytes _append_git_output _write_patch_separator _untracked_blob_exclude_reason untracked_capture_veto_reason _preflight_head_from_task _preflight_head_present _acting_constraint_from_task _empty_patch_manifest",
    }
    headless_extraction_rows = {
        f"ouroboros/headless.py::{symbol}": f"ouroboros/{owner}::{symbol}"
        for owner, symbols in headless_extraction_symbols_by_owner.items()
        for symbol in symbols.split()
    }
    tool_access_extraction_symbols_by_owner = {
        "tool_access_types.py": "ToolProfile ResourceRoot Operation SubagentCapability ToolAccessDecision ResolvedResourceBinding _ALL_ROOTS _READONLY_RESOURCE_ROOTS _TOP_LEVEL_PRINCIPAL_PROFILES _READ_OPS _TOP_LEVEL_PRINCIPAL_POLICY _POLICY _SUBAGENT_CAPABILITY_TO_OPERATION SUBAGENT_CAPABILITIES",
        "tool_access_paths.py": "_user_files_root _deliverables_root normalize_root path_is_relative_to normalize_root_relative _path_is_relative_to_casefold paths_overlap_casefold workspace_mode_block_reason canonical_data_root normalize_runtime_data_path",
        "tool_access_roots.py": "_is_subagent_ctx is_external_workspace active_tool_profile predicted_subagent_profile project_room_lens_dir load_bound_skill _skill_payload_base resource_root_path binding_targets_system_repo",
        "tool_access_user_files.py": "_USER_FILES_SECRET_COMPONENTS _USER_FILES_SECRET_NAMES _USER_FILES_SECRET_RE _USER_FILES_ALLOWED_DOTNAMES _subagent_projects_read_hint user_files_path_block_reason UserFilesPathBlockedError resolve_user_file_path",
    }
    tool_access_extraction_rows = {
        f"ouroboros/tool_access.py::{symbol}": f"ouroboros/{owner}::{symbol}"
        for owner, symbols in tool_access_extraction_symbols_by_owner.items()
        for symbol in symbols.split()
    }
    # v7 stream S, lane S1: config.py splits into the settings vocabulary, the closed
    # scales, the model slots, the reviewer routes and the numeric knobs. The parent keeps
    # the settings-file lifecycle, the path roots and the owner-only ratchets.
    config_extraction_symbols_by_owner = {
        "settings_defaults.py": "FINALIZATION_GRACE_DEFAULT_SEC OWNER_STOP_OUTER_CAP_SEC PACING_INTERVAL_DEFAULT_SEC SUPERVISOR_LIVENESS_DEADLINE_DEFAULT_SEC SETTINGS_DEFAULTS RETIRED_SETTING_KEYS _DISK_AUTHORED_SETTINGS ENDPOINT_AUTHORED_SETTINGS SETTINGS_KEYS_NOT_EXPORTED_TO_ENV settings_env_keys",
        "settings_scales.py": "EFFORT_SCALE effort_rank clamp_effort_to effort_one_step_down resolve_effort PROMPT_CACHE_TTL_SCALE resolve_prompt_cache_ttl VALID_RUNTIME_MODES _RUNTIME_MODE_RANK normalize_runtime_mode VALID_SAFETY_MODES normalize_safety_mode _SAFETY_MODE_RANK",
        "model_slots.py": "_parse_model_list _main_model get_light_model get_heavy_model get_vision_model get_image_input_mode parse_fallback_chain get_fallback_models _LEGACY_SLOT_RENAMES migrate_legacy_slot_keys get_consciousness_model get_deep_self_review_model",
        "review_model_routes.py": "_DIRECT_PROVIDER_REVIEW_RUNS _exclusive_direct_remote_provider_env direct_provider_review_models_fallback adaptive_quorum get_review_models get_review_enforcement get_scope_review_models",
        "runtime_limits.py": "_clamped_number_setting _bounded_positive_int_setting get_max_workers get_task_idle_timeout_sec get_task_abs_ceiling_sec get_per_call_timeout_ceiling_sec get_plan_task_swarm_timeout_sec get_plan_task_swarm_max_wait_sec get_restart_drain_max_sec get_safety_max_tokens get_safety_call_timeout_sec get_websearch_timeout_sec get_llm_transport_read_timeout_sec get_acceptance_review_est_sec get_acceptance_max_improvement_passes get_acceptance_reserve_pct get_plan_task_deadline_min_sec get_vision_caption_timeout_sec get_pacing_interval_sec get_supervisor_liveness_deadline_sec get_post_task_evolution_budget_usd MAX_ACTIVE_SUBAGENTS_HARD_CAP get_max_active_subagents_per_root get_max_subagent_depth DELEGATE_WAIT_CEILING_SEC DELEGATE_WAIT_WINDOW_MAX_SEC get_delegate_wait_max_sec get_delegate_wait_sec get_search_code_wall_sec",
    }
    config_extraction_rows = {
        f"ouroboros/config.py::{symbol}": f"ouroboros/{owner}::{symbol}"
        for owner, symbols in config_extraction_symbols_by_owner.items()
        for symbol in symbols.split()
    }
    # v7 stream S lane S2: the server.py composition split. Every moved name keeps
    # its server.py facade, so no row belongs to the no-facade set.
    server_extraction_symbols_by_owner = {
        "server_process.py": "DATA_DIR log _restart_requested _owner_restart_requested _request_restart_exit",
        "server_routing_context.py": "_task_belongs_to_chat _active_direct_root _addressable_root_tasks _clip_marked _chat_running_tasks _task_result_ground_truth _latest_project_task_result _main_routing_manifest _decision_turn_metadata _scoped_task_metadata _owner_binding_chat_id _project_id_for_registered_chat _reserved_project_for_chat",
        "server_owner_routing.py": "_stage_mailbox_attachments _route_project_chat_to_running_task _owner_evolution_stop _record_routing_receipt _route_owner_message",
        "server_liveness.py": "_supervisor_loop_stalled _chat_turn_wedged _alert_chat_turn_wedge _start_supervisor_liveness_watchdog",
        "server_maintenance.py": "_installed_skill_names _LAST_CANCEL_INTENT_SWEEP _periodic_supervisor_maintenance _reconcile_delegated_runs _startup_custody_sweep _prune_delegated_snapshots _periodic_zombie_reconcile _resume_interrupted_project_deletions _run_startup_task_recovery",
        "server_restart.py": "_pending_restart _live_running_task_ids _handle_restart_in_supervisor _check_pending_restart_drain _perform_supervisor_restart _managed_update_pending_kwargs _safe_restart_serialized _shutdown_task_cleanup_args _shutdown_supervisor_event_bus",
    }
    server_extraction_rows = {
        f"server.py::{symbol}": f"ouroboros/{owner}::{symbol}"
        for owner, symbols in server_extraction_symbols_by_owner.items()
        for symbol in symbols.split()
    }
    # v7 stream W periphery extractions. Owner -> symbols, one row per symbol.
    w_stream_owners = {
        ("skills/unix_computer_use/plugin.py", "skills/unix_computer_use/lib/cu_runtime.py"):
            "_TIMEOUT_SEC _MAX_IMAGE_W _MAX_IMAGE_H _CONNECTIONS_FILE _ACTIVE_CONNECTION_FILE _REMOTE_BACKENDS _MAX_REMOTE_SHOT_BYTES _OSWORLD_PKGS_PREFIX _osworld_result_ok _png_dimensions _png_intact _json _run",
        ("skills/unix_computer_use/plugin.py", "skills/unix_computer_use/lib/cu_connections.py::_ConnectionRegistryMixin"):
            "_ComputerUse._connections_path _ComputerUse._active_connection_path _ComputerUse._read_connections _ComputerUse._atomic_write _ComputerUse._write_connections _ComputerUse._active_connection _ComputerUse._disabled_connection_error _ComputerUse._active_backend_name _ComputerUse._is_remote _ComputerUse.list_connections _ComputerUse.add_connection _ComputerUse.activate_connection _ComputerUse.use_local _ComputerUse.clear_active_connection _ComputerUse.test_connection",
        ("skills/unix_computer_use/plugin.py", "skills/unix_computer_use/lib/cu_remote_backends.py::_RemoteBackendMixin"):
            "_ComputerUse._connection_target _ComputerUse._osworld_execute _ComputerUse._ssh_macos_key_name _ComputerUse._ssh_macos_cliclick_for_pyautogui _ComputerUse._remote_pyautogui _ComputerUse._remote_screenshot_result _ComputerUse._osworld_screenshot _ComputerUse._test_osworld _ComputerUse._ssh_destination _ComputerUse._ssh_scp_source _ComputerUse._ssh_run _ComputerUse._ssh_macos_screenshot _ComputerUse._test_ssh_macos",
        ("devtools/benchmarks/osworld/run_cu_bridge_agent.py", "devtools/benchmarks/osworld/cu_bridge_runtime.py"):
            "SKILL_NAME _api _text_declares_infeasible _terminal_answer_text _final_answer_declares_infeasible",
        ("devtools/benchmarks/osworld/run_cu_bridge_agent.py", "devtools/benchmarks/osworld/cu_bridge_prompts.py"):
            "GATE_PREAMBLE GATE_SUFFIX OSWORLD_PREAMBLE _ACCEPTANCE_CLAIMS",
        ("devtools/benchmarks/osworld/run_cu_bridge_agent.py", "devtools/benchmarks/osworld/cu_bridge_tool_policy.py"):
            "_ALLOWED_CORE_TOOLS _core_tool_names _host_denied_tools _GUI_ACTION_TOOLS _DENIED_SKILL_EXT_TOOLS _effective_disabled_tools _COMPUTER_USE_SHORT_TOOLS",
        ("devtools/benchmarks/osworld/run_cu_bridge_agent.py", "devtools/benchmarks/osworld/cu_bridge_gate.py"):
            "_gate_window_sec _gate_claim_window_sec _gate_verdict _DesktopEnvLogCapture ResetUnverified _reset_verified _live_policy_turns _policy_turns _await_gate_task _gate_round _GATE_TURN_RESERVE _GUEST_DOWN_GRACE_SEC _guest_endpoint_healthy _gate_cancel_unconfirmed _gate_tool_trace _gate_turn_budget",
        ("devtools/benchmarks/osworld/run_cu_bridge_agent.py", "devtools/benchmarks/osworld/cu_bridge_budget.py"):
            "_effective_max_rounds _step_budget _official_evaluate_cwd _worker_round_cap _publish_worker_round_cap _proxy_trace_shows_exhaustion _verify_setup_effect _task_scoped_proxy_config _proxy_config_is_live _refuse_wrong_dataset_commit _refuse_uncapped_step_claim _audit_step_budget _collect_budget_counters",
        ("devtools/benchmarks/osworld/run_step_agent.py", "devtools/benchmarks/osworld/step_agent_common.py"):
            "StepAgentConfig TaskRecordConfig PreflightConfig _safe_slug _http_json",
        ("devtools/benchmarks/osworld/run_step_agent.py", "devtools/benchmarks/osworld/step_agent_env.py"):
            "VMWARE_FUSION_PATHS ALIGNED_UPSTREAM SUPPORTED_PROVIDERS osworld_checkout_info provider_preflight_failures _install_optional_dependency_stubs _ensure_vmrun_on_path _DEFAULT_DESKTOP_PORT _LOOPBACK_HOSTS _is_default_desktop_server _teardown_partial_desktop_env construct_desktop_env",
        ("devtools/benchmarks/osworld/run_step_agent.py", "devtools/benchmarks/osworld/step_agent_claims.py"):
            "ClaimDirNotConfined confined_claims_dir task_claim_key claim_stale_sec acquire_task_claim UNCONFIRMED_SCORE_SUFFIX ClaimMarkerNotDurable record_unconfirmed_score mark_task_scored scored_claim_state task_already_scored release_task_claim",
        ("devtools/benchmarks/osworld/run_step_agent.py", "devtools/benchmarks/osworld/step_agent_actions.py"):
            "SPECIAL_ACTIONS _json_from_text _shell_action _click_action _type_action _hotkey_action _wait_action _normalize_structured_action",
        ("devtools/benchmarks/osworld/run_step_agent.py", "devtools/benchmarks/osworld/step_agent_policy.py"):
            "_initial_observation_with_retries OuroborosStepAgent",
        ("web/tests/harness_accounts.test.js", "web/tests/harness_accounts_helpers.js"):
            "fakeResponse",
        ("web/tests/harness_accounts.test.js", "web/tests/harness_accounts_cards.test.js"):
            "cardWithUrl fakeCodeInput fakeCardHost",
        ("web/tests/harness_accounts.test.js", "web/tests/harness_accounts_custody.test.js"):
            "storeWithReads",
        ("web/tests/harness_accounts.test.js", "web/tests/harness_accounts_panel.test.js"):
            "fakeElement mountSection captureCardControls WAKE_STILL_DOWN WAKE_UP",
    }
    w_stream_rows = {
        f"{old}::{symbol}": (f"{owner}.{symbol.split('.', 1)[1]}" if "::" in owner
                             else f"{owner}::{symbol}")
        for (old, owner), symbols in w_stream_owners.items() for symbol in symbols.split()
    }
    shell_extraction_symbols_by_owner = {
        "shell_process.py": "_RUN_SHELL_DEFAULT_TIMEOUT_SEC _active_subprocesses _subprocess_lock _tracked_subprocess_run _kill_process_group kill_all_tracked_subprocesses _shell_env_for_cwd _resolve_effective_timeout _describe_returncode _format_process_output _executor_can_run_cwd",
        "shell_outputs.py": "_OUTPUT_DIR_MAX_FILES _OUTPUT_DIR_MAX_BYTES _allowed_output_roots _protected_output_source_reason _changed_path_covers _resolve_declared_output _directory_fingerprint_from_entries _bounded_directory_fingerprint _fingerprint_output _snapshot_declared_outputs _scan_directory_output_members _register_process_outputs _UNDECLARED_OUTPUTS_MARKER _SENSITIVE_OUTPUT_NAMES _SENSITIVE_OUTPUT_SUFFIXES _SENSITIVE_OUTPUT_MARKERS _SENSITIVE_OUTPUT_COMPONENT_NAMES _sensitive_output_component_reason _OUTPUT_CALL_PATH_RE _OUTPUT_REDIRECT_PATH_RE _EMBEDDED_OUTPUT_PATH_RE _USER_FILE_WRITE_CALL_RE _USER_FILE_OPEN_WRITE_CALL_RE _USER_FILE_REDIRECT_RE _OUTPUT_STAT_SLACK_SEC _mentioned_user_file_outputs_without_declaration",
        "shell_effects.py": "_resolve_git_root _status_snapshot _shallow_listing _user_files_run_had_effect _protected_runtime_dirty_paths _restore_protected_runtime_paths _tree_fingerprint _resolve_scratch_abs _scratch_safety_reason _record_scratch_fingerprints _get_changed_files _get_diff_stat",
    }
    shell_extraction_rows = {
        f"ouroboros/tools/shell.py::{symbol}": f"ouroboros/tools/{owner}::{symbol}"
        for owner, symbols in shell_extraction_symbols_by_owner.items()
        for symbol in symbols.split()
    }
    headless_extraction_symbols_by_owner = {
        "headless_status.py": "ARTIFACT_STATUS_PENDING ARTIFACT_STATUS_FINALIZING ARTIFACT_STATUS_READY ARTIFACT_STATUS_READY_WITH_CHANGES ARTIFACT_STATUS_READY_NO_CHANGES ARTIFACT_STATUS_MISSING ARTIFACT_STATUS_FAILED ARTIFACT_TERMINAL_STATUSES _FINAL_STATUSES _LOCAL_READONLY_SUBAGENT_MODE _ARTIFACT_LIFECYCLE_FIELDS",
        "workspace_patch_capture.py": "SCRATCH_MANIFEST_NAME _GIT_UNBORN_HEAD build_workspace_patch write_workspace_patch_artifacts _git_stdout _workspace_patch_base _git_empty_tree_oid _head_reflog_exists _looks_like_git_oid _git_path_list _git_bytes _append_git_output _write_patch_separator _untracked_blob_exclude_reason untracked_capture_veto_reason _preflight_head_from_task _preflight_head_present _acting_constraint_from_task _empty_patch_manifest",
    }
    headless_extraction_rows = {
        f"ouroboros/headless.py::{symbol}": f"ouroboros/{owner}::{symbol}"
        for owner, symbols in headless_extraction_symbols_by_owner.items()
        for symbol in symbols.split()
    }
    tool_access_extraction_symbols_by_owner = {
        "tool_access_types.py": "ToolProfile ResourceRoot Operation SubagentCapability ToolAccessDecision ResolvedResourceBinding _ALL_ROOTS _READONLY_RESOURCE_ROOTS _TOP_LEVEL_PRINCIPAL_PROFILES _READ_OPS _TOP_LEVEL_PRINCIPAL_POLICY _POLICY _SUBAGENT_CAPABILITY_TO_OPERATION SUBAGENT_CAPABILITIES",
        "tool_access_paths.py": "_user_files_root _deliverables_root normalize_root path_is_relative_to normalize_root_relative _path_is_relative_to_casefold paths_overlap_casefold workspace_mode_block_reason canonical_data_root normalize_runtime_data_path",
        "tool_access_roots.py": "_is_subagent_ctx is_external_workspace active_tool_profile predicted_subagent_profile project_room_lens_dir load_bound_skill _skill_payload_base resource_root_path binding_targets_system_repo",
        "tool_access_user_files.py": "_USER_FILES_SECRET_COMPONENTS _USER_FILES_SECRET_NAMES _USER_FILES_SECRET_RE _USER_FILES_ALLOWED_DOTNAMES _subagent_projects_read_hint user_files_path_block_reason UserFilesPathBlockedError resolve_user_file_path",
    }
    tool_access_extraction_rows = {
        f"ouroboros/tool_access.py::{symbol}": f"ouroboros/{owner}::{symbol}"
        for owner, symbols in tool_access_extraction_symbols_by_owner.items()
        for symbol in symbols.split()
    }
    # Verbatim theme splits of the v7 T-stream giant test modules: source module -> {owner path: moved symbols}.
    # A moved test, fixture or helper is owned by the sibling module that now hosts it; a test-private import
    # binding keeps the canonical production provider, which these splits never moved.
    test_split_symbols_by_owner = {
        "tests/test_headless_cli.py": {"ouroboros/gateway/tasks.py": "_compose_task_text _resolve_workspace_root api_task_artifact api_task_events api_task_get api_tasks_create api_tasks_list iter_task_events", "ouroboros/headless.py": "ARTIFACT_STATUS_FAILED ARTIFACT_STATUS_FINALIZING ARTIFACT_STATUS_READY ARTIFACT_STATUS_READY_WITH_CHANGES _incidental_lockfile_excludes build_memory_export build_workspace_patch finalize_task_artifacts prune_headless_task_drives prune_task_drives task_artifacts_dir write_workspace_patch_artifacts", "ouroboros/task_results.py": "write_task_result", "ouroboros/tools/registry.py": "ToolContext ToolRegistry", "ouroboros/workspace_preflight.py": "_infer_tools_from_manifests", "tests/_headless_cli_shared.py": "_init_repo_with_file _managed_worker_pool_available", "tests/test_headless_task_api.py": "test_api_tasks_create_rejects_internal_task_types test_api_tasks_create_requires_description_not_legacy_aliases test_compose_task_text_extends_existing_headless_workspace_block test_resolve_workspace_root_blocks_case_variant_control_plane test_task_api_admission_refusal_is_terminal_not_scheduled_phantom test_task_api_enqueue_workspace_creates_child_drive test_task_api_preserves_top_level_actor_id_after_metadata_sanitization test_task_api_refuses_when_durable_queue_snapshot_fails test_task_api_rejects_external_lineage_forgery test_task_api_rejects_forged_subagent_without_child_drive_side_effect test_task_api_rejects_unsafe_task_id_and_system_workspace test_task_api_releases_reservation_when_payload_composition_fails", "tests/test_headless_task_artifacts.py": "test_child_copyback_preserves_acceptance_verdict_and_terminal_post_task_marker test_copy_child_result_cannot_overwrite_finalized_accounting test_copy_child_result_merges_cost_before_finalization test_effective_result_preserves_workspace_artifact_status_with_child_drive test_effective_result_preserves_workspace_patch_kind_with_child_drive test_external_child_task_budget_uses_parent_drive_state test_finalize_task_artifacts_preserves_existing_artifact_axis_fields test_memory_export_includes_nested_memory_files test_startup_prune_removes_only_old_terminal_child_drives test_startup_prune_removes_only_old_terminal_task_scratch test_startup_prune_uses_effective_terminal_status test_task_artifact_endpoint_rebases_child_drive_artifact_after_status_repair test_task_artifact_endpoint_rejects_metadata_name_path_mismatch test_task_artifact_endpoint_serves_manifest_artifact_after_status_repair test_task_artifact_endpoint_serves_only_declared_artifacts", "tests/test_headless_task_events.py": "test_effective_child_completion_waits_for_artifacts test_effective_child_failure_waits_for_artifacts test_effective_task_result_preserves_parent_terminal_status test_logs_tail_parent_filter_includes_child_lineage_events test_public_task_result_strips_nested_legacy_result_status test_task_event_replay_parent_includes_child_lineage_events test_task_event_replay_uses_existing_logs_and_result test_task_list_filters_on_effective_child_status test_task_sse_emits_final_result_after_cursor_saw_scheduled_result test_workspace_event_replay_suppresses_task_done_until_artifacts_terminal", "tests/test_headless_workspace_patch.py": "test_failed_refinalization_drops_stale_workspace_patch_metadata test_finalize_workspace_patch_allows_external_workspace_head_changed test_finalize_workspace_patch_exception_manifest_keeps_base_fields test_workspace_patch_allows_benign_tokenizer_json test_workspace_patch_allows_external_workspace_first_commit test_workspace_patch_excludes_binary_junk_and_oversize test_workspace_patch_fails_on_common_credential_paths test_workspace_patch_fails_on_invalid_head_not_unborn test_workspace_patch_fails_on_sensitive_untracked_file test_workspace_patch_fails_on_sensitive_untracked_file_inside_excluded_dir test_workspace_patch_fails_when_acting_base_sha_head_changed test_workspace_patch_includes_tracked_and_untracked_files test_workspace_patch_lockfile_without_manifest_is_incidental_only_with_code_changes test_workspace_patch_manifest_excludes_env_cache_dirs test_workspace_patch_preserves_lockfile_when_other_changes_are_junk test_workspace_patch_preserves_untracked_paths_with_whitespace test_workspace_patch_supports_unborn_git_worktree test_workspace_patch_supports_unborn_sha256_git_worktree test_workspace_patch_uses_acting_base_sha_without_preflight_metadata", "tests/test_headless_workspace_shell.py": "test_external_workspace_shell_allows_task_local_git test_workspace_context_routes_project_files_and_keeps_system_tools_reachable test_workspace_preflight_infers_binaries_from_script_commands test_workspace_run_shell_allows_absolute_cwd_under_workspace_and_child_drive test_workspace_run_shell_cwd_allows_scratch_and_explicit_system test_workspace_shell_allows_nested_relative_write_paths test_workspace_shell_blocks_nested_symlink_escape_absolute_path test_workspace_shell_blocks_windows_absolute_redirects_before_shell_execution test_workspace_shell_git_ls_remote_requires_network_contract test_workspace_shell_keeps_symlinked_workspace_absolute_paths_allowed test_workspace_shell_safe_stdio_redirects_are_not_write_like test_workspace_shell_sudo_and_pro_passthrough_policy"},
        "tests/test_git_review_pipeline.py": {"tests/_git_review_pipeline_shared.py": "_critical_triad_items _get_git_module _get_git_ops_module _get_registry_module _get_review_module _make_ctx", "tests/test_git_review_advisory_skip_tests.py": "TestAdvisorySkipTests", "tests/test_git_review_bypass_gate.py": "TestBypassPathTestsRun TestRouteSlotAwareBypassGate _make_staged_repo", "tests/test_git_review_enforcement.py": "TestReviewEnforcementModes TestReviewHistoryBuilding TestReviewQuorumLogic _PARSE_REVIEW_JSON_CASES review_ctx test_parse_review_json", "tests/test_git_review_preflight_gate.py": "TestPreflightCheck7P9Limits _PREFLIGHT_CASES test_preflight_check"},
        "tests/test_tool_capabilities.py": {"tests/test_tool_capabilities_black_box_policy.py": "test_protected_black_box_artifact_policy_blocks_introspection test_protected_black_box_recursive_policy_maps_executor_backend_paths test_runtime_data_write_blocks_workspace_executor_control_state", "tests/test_tool_capabilities_readonly_subagent.py": "test_allowed_resources_block_web_and_external_tools test_local_readonly_subagent_allows_enabled_extension_tool test_local_readonly_subagent_data_read_denies_secret_files test_local_readonly_subagent_execute_blocks_forbidden_tools test_local_readonly_subagent_repo_read_denies_secret_files test_local_readonly_subagent_task_drive_and_skill_payload_filters", "tests/test_tool_capabilities_search_code.py": "_make_ctx _populate_repo test_code_search_empty_query test_code_search_include_filter test_code_search_invalid_regex test_code_search_literal test_code_search_max_results test_code_search_no_matches test_code_search_regex test_code_search_scoped_path test_code_search_skips_binaries test_code_search_skips_cache_dirs test_search_code_does_not_follow_symlink_outside_root test_search_code_has_result_limit test_search_code_in_core_tools test_search_code_in_initial_schemas test_search_code_is_parallel_safe test_search_code_registered test_search_code_ripgrep_fallback_when_unavailable test_search_code_ripgrep_path_filters_protected_files", "tests/test_tool_capabilities_subagent_scheduling.py": "test_capability_omission_manifest_surfaces_extension_discovery_failure test_get_task_result_in_core test_local_readonly_subagent_initial_schemas_are_allowlisted test_schedule_subagent_available_in_registry test_schedule_subagent_in_core test_schedule_subagent_in_initial_schemas test_schedule_subagent_inherits_workspace_executor_ref test_schedule_subagent_required_capabilities_fail_fast_for_readonly test_schedule_subagent_required_delegate_capability_is_satisfied_for_readonly test_schedule_subagent_required_vcs_capability_is_satisfied_for_readonly test_wait_task_in_core test_workspace_focus_does_not_turn_top_level_cancel_into_child_only test_workspace_parent_keeps_the_ordinary_top_level_control_surface"},
    }
    test_split_rows = {f"{source}::{symbol}": f"{owner}::{symbol}" for source, owners in test_split_symbols_by_owner.items() for owner, symbols in owners.items() for symbol in symbols.split()}
    test_split_facade_rows = {"tests/test_headless_cli.py::_managed_worker_pool_available",
                              "tests/test_git_review_pipeline.py::_get_git_module",
                              "tests/test_git_review_pipeline.py::_get_git_ops_module",
                              "tests/test_git_review_pipeline.py::_get_registry_module",
                              "tests/test_git_review_pipeline.py::_make_ctx"}
    web_extractions = {f"web/modules/chat.js::{symbol}": f"web/modules/{owner}::{symbol}" for owner, symbols in {"chat_card_state.js": "liveLineRowToggleKey clearStickyCardState COLLAPSED_ACTIVITY_MAX boundActivityPreview projectCollapsedActivity isTerminalTaskPhase", "chat_controls.js": "shouldFirePanic confirmAndSendPanic", "chat_render_batch.js": "insertTimelineNode", "costs.js": "headerBudgetPresentation taskCostMeta taskCostProjection mergeStickyCostMeta", "utils.js": "rawTimestampEpoch", "chat_card_actions.js": "projectIdFromTask"}.items() for symbol in symbols.split()}
    # createChatInstance closure helpers moved into per-instance factories (no facade: they were never exported)
    web_extractions.update({f"web/modules/chat.js::createChatInstance.{symbol}": f"web/modules/{owner}::{factory}.{symbol}" for owner, factory, symbols in (
        ("chat_timeline_anchor.js", "createTimelineAnchors", "NEAR_BOTTOM_THRESHOLD_PX isNearBottom captureVisibleTimelineAnchor restoreVisibleTimelineAnchor"),
        ("chat_message_identity.js", "createMessageIdentity", "buildMessageKey rememberMessageKey formatMsgTime stampNodeTimestamp getSenderLabel"),
        ("chat_document_bubble.js", "createDocumentBubbles", "buildDocumentBubble documentMessageKey appendDocumentBubble"),
        ("chat_subagent_routing.js", "createSubagentRouting", "setSubagentParent summarizeSubagentCardFrame updateSubagentCardFromEvent routeSubagentProgressToCard routeSubagentFinalMessageToCard routeSubagentTerminalToCard"),
    ) for symbol in symbols.split()})
    # v7 wave C, lane W3: the remaining createChatInstance closure clusters, each moved
    # whole into a per-instance factory of its own sibling owner. No facade: none of
    # these helpers was ever exported, so the ledger identity is the only address.
    w3_chat_extraction_symbols_by_owner = (
        ("chat_task_ui_state.js", "createTaskUiStateTracker", "isBackgroundTaskId shouldAlwaysShowTaskCard isForegroundLiveCard createTaskUiState getTaskUiState scheduleTaskUiCleanup bufferLiveUpdate markTaskToolCall forceTaskCard markAssistantReply markTaskComplete"),
        ("chat_card_actions.js", "createCardActions", "turnTaskIntoProject ensureLiveActionsEl syncCancelRunButton syncCancelRunButtonMutation markLiveCardCancelPending captureLiveCardPhase restoreLiveCardPhase reconcileCancelCardFromDetail cancelRunFromCard markTaskCancelable markCardConverted markCardConvertedMutation"),
        ("chat_live_card_view.js", "createLiveCardView", "applySuggestedName applySuggestedNameMutation renderCollapsedActivity ensureSubagentContainer setLiveCardTypingVisible formatLiveCardPhaseLabel setLiveCardExpanded isLiveLineExpandable syncLiveCardToggle directSubagentCount buildTimelineItemHtml isTimelinePinnedToBottom deferCollapsedTimeline renderLiveCardTimeline appendTimelineItem patchLastTimelineItem patchTimelineItemAt renderLiveCardMeta"),
        ("chat_message_annotations.js", "createMessageAnnotations", "routingAnnotationText renderRoutingAnnotation updateMessageAnnotation clearTransientRoutingAnnotations markPendingDelivered"),
        ("chat_composer.js", "createComposer", "resizeChatInput swarmArmed setSwarm setSendBusy scrollToBottom updateScrollButton updateMessagesPadding"),
        ("chat_header_controls.js", "createHeaderControls", "syncHeaderControlState refreshHeaderControlState"),
        ("chat_frame_routing.js", "createFrameRouting", "isKnownProjectFrame incrementUnreadIfNeeded isProjectMirrorFrame isMyThread"),
    )
    # The same wave's chat.js module-scope primitives: three closure helpers with no
    # closure reads at all became plain top-level owners, and the cost presentation
    # joined the existing costs owner.
    w3_chat_primitive_rows = {
        "web/modules/chat.js::withTaskCostMeta": "web/modules/costs.js::withTaskCostMeta",
        "web/modules/chat.js::shownIncidentToastKeys": "web/modules/chat_notices.js::shownIncidentToastKeys",
        "web/modules/chat.js::showTaskIncidentToast": "web/modules/chat_notices.js::showTaskIncidentToast",
        "web/modules/chat.js::showContextFitToast": "web/modules/chat_notices.js::showContextFitToast",
    }
    web_extractions.update(w3_chat_primitive_rows)
    web_extractions.update({f"web/modules/chat.js::createChatInstance.{symbol}": f"web/modules/{owner}::{factory}.{symbol}" for owner, factory, symbols in w3_chat_extraction_symbols_by_owner for symbol in symbols.split()})
    implemented.update(registry_core_rows)
    implemented.update(registry_resolution_rows)
    implemented.update(registry_guard_rows)
    implemented.update(registry_dispatch_method_rows)
    implemented.update(registry_dependency_owners | core_extraction_rows)
    implemented.update(git_extraction_rows)
    implemented.update(shell_extraction_rows)
    implemented.update(headless_extraction_rows)
    implemented.update(tool_access_extraction_rows)
    implemented.update(server_extraction_rows)
    registry_extraction_no_facade_rows = (
        set(registry_core_rows) - {"ouroboros/tools/registry.py::ToolRegistry"}
    ) | set(registry_resolution_rows) | set(registry_guard_rows) | set(
        registry_dispatch_method_rows
    ) | set(registry_dependency_owners) | set(core_extraction_rows)
    # _ComputerUse methods move into mixin classes: the class inherits the exact
    # same function object, so the compatibility contract is inheritance, not a
    # module-level re-export, and the facade cell is "-".
    registry_extraction_no_facade_rows |= {
        identity for identity in w_stream_rows if "::_ComputerUse." in identity
    }
    # Node test fixtures move to a sibling *.test.js discovered by the same glob;
    # a test module has no re-export contract, so those facade cells stay "-".
    registry_extraction_no_facade_rows |= {
        identity for identity in w_stream_rows
        if identity.startswith("web/tests/") and "fakeResponse" not in identity
    }
    retired_current = {
        "ouroboros/tools/registry.py::_HEAL_PROTECTED_PAYLOAD_FILENAMES":
            "retired:unused payload-control alias removed with registry core extraction",
        "tests/test_commit_gate.py::_get_registry_module": "retired:test-only registry import helper removed when CORE_TOOL_NAMES characterization moved to its canonical owner",
    }
    retired_current["ouroboros/review.py::_git_source_snapshot"] = (
        "retired:ref inventories read blobs directly through _iter_ref_gated_blobs "
        "and reuse them by blob id"
    )
    retired_current.update({"web/modules/chat.js::optionalFiniteNumber": "web/modules/costs.js::optionalFiniteNumber", "ouroboros/loop_tool_execution.py::_parse_plan_review_control": "retired:native plan_task ToolResult metadata replaces textual control parsing", "ouroboros/loop_tool_execution.py::PLAN_REVIEW_CONTROL_PREFIX": "retired:loop no longer imports the display-only plan footer prefix", "ouroboros/loop_tool_execution.py::_PLAN_REVIEW_OUTCOMES": "retired:plan producer validates the closed outcome vocabulary before publication"})
    # T1: two retirements that DO carry a semantic delta — the loop's ordered
    # families and generic markers move into the single classifier rather than
    # disappearing, so their rows name the spec 4.3.3 delta instead of "none".
    retired_current.update({
        "ouroboros/loop_tool_execution.py::_FAILURE_PREFIXES":
            "retired:the loop no longer classifies result text; the single classifier owns every family",
        "ouroboros/loop_tool_execution.py::_FAILURE_MARKERS":
            "retired:the generic marker fallbacks live once, in the single classifier",
    })
    retired_current["ouroboros/launcher_onboarding.py::save_settings"] = (
        "retired:the launcher persists nothing at startup; the pre-server provider "
        "normalization is applied to the environment and re-derived by every reader"
    )
    retired_delta_ids = {
        "ouroboros/launcher_onboarding.py::save_settings": "D03",
        "ouroboros/loop_tool_execution.py::_FAILURE_PREFIXES": "D02",
        "ouroboros/loop_tool_execution.py::_FAILURE_MARKERS": "D02",
        # S3, spec 4.3.8: the safety module's import-time supervisor edge.
        "ouroboros/safety.py::update_budget_from_usage": "D05",
        # S3, spec 4.3.6: three worker-pool globals nothing read.
        "supervisor/workers.py::SOFT_TIMEOUT_SEC": "D04",
        "supervisor/workers.py::HARD_TIMEOUT_SEC": "D04",
        "supervisor/workers.py::TOTAL_BUDGET_LIMIT": "D04",
    }
    retired_current.update({
        "supervisor/workers.py::SOFT_TIMEOUT_SEC":
            "retired:no rail reads it; the queue raises the deprecation notice and discards the value",
        "supervisor/workers.py::HARD_TIMEOUT_SEC":
            "retired:no rail reads it; the queue raises the deprecation notice and discards the value",
        "supervisor/workers.py::TOTAL_BUDGET_LIMIT":
            "retired:a third copy of a limit nothing read; supervisor.state is the budget authority",
    })
    retired_current["ouroboros/safety.py::update_budget_from_usage"] = (
        "retired:the ledger writer is injected by the context, or reached at call time"
    )
    existing_process_owner_rows = {
        "tests/test_skill_exec.py::test_run_shell_restores_obfuscated_self_authored_state_marker",
        "ouroboros/tools/registry.py::SKILL_OWNER_STATE_FILENAMES",
        "ouroboros/tools/registry.py::parse_porcelain_paths",
        "ouroboros/tools/registry.py::safe_relpath",
        "ouroboros/tools/registry.py::LIGHT_SHELL_WRITER_COMMANDS",
        "ouroboros/tools/registry.py::SKILL_OWNER_STATE_STEMS",
        "ouroboros/tools/registry.py::build_resolved_resource_binding",
        "ouroboros/tools/registry.py::interpreter_family",
        "ouroboros/tools/registry.py::light_shell_repo_mutation",
        "ouroboros/tools/registry.py::protected_artifact_shell_block_reason",
        "ouroboros/tools/registry.py::runtime_data_guard_targets",
        "ouroboros/tools/registry.py::shell_command_string",
        "ouroboros/tools/registry.py::strip_leading_env_assignments",
        "ouroboros/tools/registry.py::sudo_noninteractive_violation",
        "ouroboros/tools/registry.py::unwrap_env_argv",
        "ouroboros/tools/registry.py::workspace_executor_state_write_block",
        "ouroboros/tools/registry.py::writer_target_tokens",
        "ouroboros/tools/registry.py::PROTECTED_RUNTIME_PATHS",
        "ouroboros/tools/registry.py::task_artifact_dir_path",
        "ouroboros/tools/registry.py::task_id_for_artifacts",
        "ouroboros/tools/registry.py::run_shell_git_block_reason",
        "ouroboros/tools/registry.py::workspace_git_safety_violation",
        "ouroboros/tools/registry.py::is_absolute_path_text",
        "ouroboros/tools/registry.py::path_text_is_inside",
        "ouroboros/tools/registry.py::shell_argv",
        "ouroboros/tools/registry.py::shell_argv_with_path_tokens",
        "ouroboros/tools/registry.py::PROTECTED_RUNTIME_PATHS_LOWER",
        "ouroboros/tools/registry.py::shell_has_write_indicator",
        "ouroboros/tools/registry.py::shell_writer_targets_protected",
        "ouroboros/tools/registry.py::is_external_workspace",
        "ouroboros/tools/registry.py::normalize_root",
        "ouroboros/tools/registry.py::resolve_shell_cwd",
        "ouroboros/tools/registry.py::SKILL_PAYLOAD_CONTROL_DIRNAMES",
        "ouroboros/tools/registry.py::is_skill_payload_path",
        "ouroboros/tools/registry.py::resolve_skill_payload_target",
    }
    # T1: _FAILURE_PREFIXES and _FAILURE_MARKERS are retired rather than re-owned;
    # the loop pair keeps its names as compatibility wrappers over the typed owners.
    implemented.update({name: name for name in ("ouroboros/_outcome_tool_errors.py::_BLOCKING_TOOL_STATUSES", "ouroboros/reflection.py::_ERROR_MARKERS")})
    implemented["ouroboros/loop_tool_execution.py::_extract_result_metadata"] = "ouroboros/loop_tool_execution.py::_typed_result_metadata"
    implemented["ouroboros/loop_tool_execution.py::_is_tool_execution_failure"] = "ouroboros/loop_tool_execution.py::_typed_execution_failure"
    implemented["ouroboros/loop_tool_execution.py::_structured_tool_failure"] = "ouroboros/tools/tool_result.py::_structured_failure"
    implemented["tests/test_tool_execution_classification.py::test_shell_and_claude_failures_are_treated_as_tool_failures"] = "tests/test_tool_execution_classification.py::test_shell_and_protected_failures_are_treated_as_tool_failures"
    existing_process_owner_rows.update({"ouroboros/tools/core.py::_code_search", "ouroboros/loop_tool_execution.py::_structured_tool_failure", "tests/test_tool_execution_classification.py::test_shell_and_claude_failures_are_treated_as_tool_failures", 'ouroboros/tools/core.py::_filter_out_project_store', 'ouroboros/tools/core.py::_policy_is_skill_owner_state_target', 'ouroboros/tools/core.py::active_repo_dir_for', 'ouroboros/tools/core.py::active_tool_profile', 'ouroboros/tools/core.py::build_resolved_resource_binding', 'ouroboros/tools/core.py::decide_tool_access', 'ouroboros/tools/core.py::normalize_root', 'ouroboros/tools/core.py::normalize_runtime_data_path', 'ouroboros/tools/core.py::read_text', 'ouroboros/tools/core.py::SKILL_OWNER_STATE_FILENAMES', "ouroboros/loop_tool_execution.py::_extract_result_metadata", "ouroboros/loop_tool_execution.py::_is_tool_execution_failure", "ouroboros/_outcome_tool_errors.py::_BLOCKING_TOOL_STATUSES", "ouroboros/reflection.py::_ERROR_MARKERS"})
    registry_extraction_no_facade_rows.update({"ouroboros/loop_tool_execution.py::_structured_tool_failure", "tests/test_tool_execution_classification.py::test_shell_and_claude_failures_are_treated_as_tool_failures", "ouroboros/loop_tool_execution.py::_extract_result_metadata", "ouroboros/loop_tool_execution.py::_is_tool_execution_failure", "ouroboros/_outcome_tool_errors.py::_BLOCKING_TOOL_STATUSES", "ouroboros/reflection.py::_ERROR_MARKERS"})
    # T1 fix batch: the self-reported-failure homing, the reflection ok-set, and the
    # two classifier pins that move out of the loop's wall module.
    t1_fix_rows = {
        "ouroboros/_outcome_tool_errors.py::_POLICY_DENIAL_STATUSES": "ouroboros/_outcome_tool_errors.py::_POLICY_DENIAL_STATUSES",
        "ouroboros/reflection.py::should_generate_reflection": "ouroboros/reflection.py::_trace_call_errored",
        "tests/test_loop_misc.py::test_a_tool_that_reports_its_own_failure_is_not_recorded_as_success": "tests/test_tool_execution_classification.py::test_a_tool_that_reports_its_own_failure_is_not_recorded_as_success",
        "tests/test_loop_misc.py::test_auto_attach_skips_a_result_that_declared_failure": "tests/test_tool_execution_classification.py::test_auto_attach_skips_a_result_that_declared_failure",
    }
    implemented.update(t1_fix_rows)
    existing_process_owner_rows.update(t1_fix_rows)
    existing_process_owner_rows.add("tests/test_repo_health_smoke.py::test_transition_rejects_function_swap_even_at_same_cardinality")
    registry_extraction_no_facade_rows.update(t1_fix_rows)
    # v7 stream S3: supervisor/events.py split into per-family owner modules.
    s3_events_symbols_by_owner = {
        "events_chat_delivery.py": "HOST_NARRATION _bound_project_chat_id _handle_typing_start _DELIVERED_MESSAGE_IDS _register_delivered _handle_send_message _handle_send_photo _handle_send_video _handle_send_document",
        "events_subagent_admission.py": "_GIT_UNBORN_HEAD _is_active_subagent_task _active_subagent_count _task_own_id _iter_tree_subagent_tasks _depth_reservation_admits _subagent_cap_blocks _subagent_rejection_meta _subagent_scheduled_meta _send_subagent_rejection _record_delegation_constraint _compose_subagent_text _validate_external_workspace _external_workspace_head _resolve_subagent_constraint",
        "events_schedule_task.py": "_handle_schedule_task VALID_SUBAGENT_MEMORY_MODES _PARENT_CONTEXT_MARKER _PARENT_CONTEXT_END _extract_task_description_and_context _format_task_for_dedup _build_scheduled_task_payload _find_duplicate_task _cleanup_rejected_worktree _reject_schedule_task _reject_if_no_chat_target",
        "events_project_routing.py": "_emit_routing_receipt _publish_routing_ack _rollback_promoted_pending _persist_promote_rejection _prepare_promote_source_off_loop _handle_promote_chat_to_task _handle_routing_manual_target _handle_project_digest _handle_ensure_project_scope",
        "events_coop_checkpoint.py": "_COOP_CHECKPOINT_INFLIGHT _COOP_CHECKPOINT_DROPPED _COOP_CHECKPOINT_LOCK _spawn_coop_checkpoint _checkpoint_coop_roots_on_root_done _maybe_checkpoint_coop_on_tree_quiescence",
        "events_evolution_done.py": "_handle_evolution_task_done",
        "events_task_done.py": "_authoritative_terminal_cost _task_done_review_projection _PROVIDER_DEATH_NOTIFIED _maybe_notify_provider_death _finish_task_done_dispatch _resolve_lifecycle_fault _task_done_durable_fault _handle_task_done",
        "events_budget.py": "_handle_llm_usage _set_root_budget_pause_locked _handle_budget_pause _handle_budget_root_fence _handle_review_wave_budget_insufficient",
        "events_worker_reports.py": "_handle_task_heartbeat _handle_task_dispatch_resolved _handle_task_metrics _handle_log_event _handle_skill_lifecycle _handle_acceptance_fence _handle_external_wait_lease",
        "events_runtime_controls.py": "_handle_deep_self_review_request _handle_promote_to_stable _handle_cancel_task _handle_toggle_evolution _handle_toggle_consciousness _handle_owner_message_injected",
        # The owner-stop backstop joins the existing transitions owner, not a new module.
        "queue_transitions.py": "_close_campaign_after_owner_stop",
    }
    s3_events_rows = {
        f"supervisor/events.py::{symbol}": f"supervisor/{owner}::{symbol}"
        for owner, symbols in s3_events_symbols_by_owner.items()
        for symbol in symbols.split()
    }
    implemented.update(s3_events_rows)
    existing_process_owner_rows.update(
        identity for identity in s3_events_rows
        if s3_events_rows[identity].startswith("supervisor/queue_transitions.py")
    )
    s3_custody_rows = {
        f"supervisor/task_lifecycle.py::{symbol}": f"supervisor/cancel_custody.py::{symbol}"
        for symbol in """_queue_module _durable_settled_status cancel_task_custody SETTLED_ALREADY
            _worker_possibly_alive _active_intent _reaping_owner_abandoned
            _recover_stranded_reaping_slot _claim_intent _settle_intent _release_intent_claim
            _intent_outcome_fields _restore_custody _finish_captured_pending
            _finish_captured_running _finalize_cancel_intent_on_miss""".split()
    }
    implemented.update(s3_custody_rows)
    # v7 stream S3, spec 4.3.12: the dispatch table and its miss path carry a
    # semantic delta at their own path — the retired key and the declared miss
    # disposition — so they are implemented rows with a D06 id, not moves.
    s3_semantic_delta_ids = {
        "supervisor/events.py::EVENT_HANDLERS": "D06",
        "supervisor/events.py::dispatch_event": "D06",
        # spec 4.3.6: the supervisor half of the three retired no-op settings keys.
        "supervisor/state.py::status_text": "D04",
        "supervisor/queue.py::init": "D04",
        "supervisor/workers.py::init": "D04",
        ("devtools/benchmarks/terminal_bench/harbor_installed_agent.py"
         "::OuroborosTerminalBenchAgent._container_env"): "D06",
    }
    implemented.update({name: name for name in s3_semantic_delta_ids})
    existing_process_owner_rows.update(s3_semantic_delta_ids)
    registry_extraction_no_facade_rows.update(s3_semantic_delta_ids)
    s3_worker_process_rows = {
        f"supervisor/workers.py::{symbol}": f"supervisor/worker_process.py::{symbol}"
        for symbol in """WORKER_LOG_SINK_SUPPRESSED_TYPES _current_custody_session_id
            _bind_worker_repo_root _prepare_worker_task_runtime worker_main
            _log_worker_crash""".split()
    }
    implemented.update(s3_worker_process_rows)
    # v7 stream L-A: the scope reviewer keeps the run; its prompt budget/window
    # authority and its pack assembly become owners. Every row is a facade row —
    # the parent re-exports the same objects under their historical private names.
    scope_review_extraction_symbols_by_owner = {
        "scope_review_budget.py": "_SCOPE_MAX_TOKENS _SCOPE_REVIEW_SLOT_TIMEOUT_SEC _SCOPE_OUTPUT_MARGIN_TOKENS _SCOPE_INPUT_TOKEN_LIMIT _SCOPE_MODEL_DEFAULT _SCOPE_BUDGET_TOKEN_LIMIT _SCOPE_FAILCLOSED_WINDOW _SCOPE_MODEL_CONTEXT_WINDOW _calibrated_input_token_limit _shared_window_scaled_reserves _window_scaled_reserves _effective_scope_input_limit _get_scope_model _is_provider_oversize_error _provider_error_is_oversize",
        "scope_review_pack.py": "_DELETED_INLINE_MAX_BYTES _SCOPE_CONTEXT_MANIFEST _SCOPE_STABLE_PREFIX_LEN _ScopeAtlasNotAssembled _current_scope_context_manifest _CANONICAL_CONTEXT_DOCS _CURRENT_TOUCHED_CONTEXT_SKIP_PREFIXES _load_canonical_context_docs _should_skip_current_touched_context _build_review_history_section _parse_staged_name_status _classify_deleted_for_inline _degradable_diff_only_paths _inline_deleted_file_pack _gather_scope_packs _record_ladder_steps _render_touched_section _build_scope_history_section _ScopePromptContext _build_scope_prompt",
    }
    scope_review_extraction_rows = {
        f"ouroboros/tools/scope_review.py::{symbol}": f"ouroboros/tools/{owner}::{symbol}"
        for owner, symbols in scope_review_extraction_symbols_by_owner.items()
        for symbol in symbols.split()
    }
    # S3b: the queue's snapshot-path shadow. The ratchet-transition rename is the
    # wip's own row (D11), registered beside the other delta expectations.
    retired_current["supervisor/queue.py::QUEUE_SNAPSHOT_PATH"] = (
        "retired:supervisor.state owns the queue snapshot path; the queue reads it through the module at use time"
    )
    # S3b: the module-handle extraction of the queue (delta D18).
    s3b_queue_handle_symbols_by_owner = {
        "queue_snapshot.py": "_kept_service_pids parse_iso_to_ts persist_queue_snapshot restore_pending_from_snapshot",
        "queue_timeouts.py": "_enforce_task_timeouts_locked _has_live_descendant _has_pending_descendant _is_descendant_of _subtree_progressing _task_deadline_ts _task_drive_for_task enforce_task_timeouts",
        "queue_schedules.py": "_SKILL_SCHEDULE_SYNC_INTERVAL_SEC _last_skill_schedule_sync _schedule_running_or_queued _scheduled_tasks_path _task_from_schedule _write_scheduled_tasks check_scheduled_tasks list_scheduled_tasks remove_scheduled_task resync_skill_schedules sync_skill_schedules upsert_scheduled_task",
        "queue_evolution.py": "_deliver_pending_owner_report enqueue_evolution_task_if_needed get_evolution_status_snapshot queue_deep_self_review_task",
    }
    s3b_queue_handle_rows = {
        f"supervisor/queue.py::{symbol}": f"supervisor/{owner}::{symbol}"
        for owner, symbols in s3b_queue_handle_symbols_by_owner.items()
        for symbol in symbols.split()
    }
    implemented.update(s3b_queue_handle_rows)
    s3b_queue_no_facade = {
        "supervisor/queue.py::_SKILL_SCHEDULE_SYNC_INTERVAL_SEC",
        "supervisor/queue.py::_last_skill_schedule_sync",
    }
    registry_extraction_no_facade_rows.update(s3b_queue_no_facade)
    # S3b: the module-handle extraction of the worker pool (delta D18).
    s3b_pool_handle_symbols_by_owner = {
        "worker_promotion.py": "_admit_promoted_workspace _canonical_promoted_repair_constraint _fail_promoted_task_loudly _origin_from_mapping _origin_from_task_record _promote_duplicate_reason _promoted_force_plan_metadata _report_binding_failure ensure_project_scope promote_chat_to_task",
        "worker_chat_lane.py": "_broadcast_task_named _handle_chat_direct_locked _run_chat_task auto_resume_after_restart handle_chat_direct handle_chat_ephemeral",
        "worker_health.py": "_emit_task_done_terminal _ensure_workers_healthy_locked ensure_workers_healthy terminal_task_metadata",
        "worker_pool_lifecycle.py": "_WORKER_LIFECYCLE_LOCK _first_worker_boot_event_since _first_worker_event_since _kill_survivors _record_worker_pids _serialized_worker_lifecycle _verify_worker_sha_after_spawn _worker_pids_path _write_failure_result kill_workers_for_update reap_orphaned_workers respawn_worker",
        "worker_assignment.py": "_cancel_unauthorized_evolution _evolution_assignment_error assign_tasks",
    }
    s3b_pool_handle_rows = {
        f"supervisor/workers.py::{symbol}": f"supervisor/{owner}::{symbol}"
        for owner, symbols in s3b_pool_handle_symbols_by_owner.items()
        for symbol in symbols.split()
    }
    implemented.update(s3b_pool_handle_rows)
    # S3b: the retired liveness knobs leave the signatures they were ferried through.
    s3b_retired_signature_rows = {
        "supervisor/queue.py::refresh_timeouts_from_settings":
            "supervisor/queue.py::refresh_timeouts_from_settings",
    }
    implemented.update(s3b_retired_signature_rows)
    existing_process_owner_rows.update(s3b_retired_signature_rows)
    registry_extraction_no_facade_rows.update(s3b_retired_signature_rows)
    s3_semantic_delta_ids["supervisor/queue.py::refresh_timeouts_from_settings"] = "D04"
    retired_current.update({
        "supervisor/queue.py::SOFT_TIMEOUT_SEC":
            "retired:no rail consulted it and its last reader, the owner status line, stopped printing it",
        "supervisor/queue.py::HARD_TIMEOUT_SEC":
            "retired:no rail consulted it and its last reader, the owner status line, stopped printing it",
    })
    retired_delta_ids["supervisor/queue.py::SOFT_TIMEOUT_SEC"] = "D04"
    retired_delta_ids["supervisor/queue.py::HARD_TIMEOUT_SEC"] = "D04"
    implemented.update(w_stream_rows)
    implemented.update(shell_extraction_rows)
    implemented.update(headless_extraction_rows)
    implemented.update(tool_access_extraction_rows)
    # v7 stream L-A: review_helpers keeps the shared plumbing; the reviewer vocabulary
    # and the reviewable-file classification/packs become owners. All facade rows.
    review_helpers_extraction_symbols_by_owner = {
        "review_prompt_text.py": "_SECRET_LINE_RE _JSON_SECRET_RE CRITICAL_FINDING_CALIBRATION REVIEW_PREAMBLE REVIEW_THOROUGHNESS_BLOCK REVIEW_SEVERITY_THRESHOLDS REPO_ANTI_PATTERN_LOCK_GUARD _ANTI_THRASHING_RULE_VERDICT _ANTI_THRASHING_RULE_ITEM_NAME _CONVERGENCE_RULE_TEXT _HISTORY_VERIFICATION_ONLY_RULE single_line format_review_history_entry build_review_history_section build_obligations_block build_anti_thrashing_rules_section build_self_verification_template _OBLIGATION_SUFFIX_RE normalize_reviewer_obligation_id strip_obligation_suffix normalize_reviewer_item normalize_reviewer_items build_rebuttal_section format_obligation_excerpt redact_prompt_secrets _make_fence format_prompt_code_block",
        "review_file_pack.py": "BINARY_EXTENSIONS _FILE_SIZE_LIMIT _SENSITIVE_EXTENSIONS _SENSITIVE_NAMES _VENDORED_SUFFIXES _VENDORED_NAMES _FULL_REPO_BINARY_EXTENSIONS _FULL_REPO_SKIP_DIR_PREFIXES _MAX_FULL_REPO_FILE_BYTES _BINARY_SNIFF_BYTES parse_changed_paths_from_porcelain_z list_changed_paths_from_git_status parse_changed_paths_from_porcelain paths_from_porcelain_line parse_git_name_status format_name_status_for_preflight paths_from_name_status build_touched_file_pack build_advisory_changed_context _is_probably_binary _raw_bytes_binary list_git_tracked_paths iter_repo_pack_entries build_full_repo_pack build_head_snapshot_section",
    }
    review_helpers_extraction_rows = {
        f"ouroboros/tools/review_helpers.py::{symbol}": f"ouroboros/tools/{owner}::{symbol}"
        for owner, symbols in review_helpers_extraction_symbols_by_owner.items()
        for symbol in symbols.split()
    }
    implemented.update(scope_review_extraction_rows)
    # v7 stream L-A: review_state keeps the durable store; its record rules and its
    # in-memory ledger become owners. All facade rows.
    review_state_extraction_symbols_by_owner = {
        "review_state_records.py": "_STATE_SCHEMA_VERSION _MAX_RUN_HISTORY _MAX_ATTEMPT_HISTORY _MAX_COMMIT_READINESS_DEBTS _DEFAULT_TOOL_NAME _DEFAULT_ADVISORY_TOOL_NAME _LEGACY_CURRENT_REPO_KEY _REVIEW_ATTEMPT_TTL_SEC _REVIEW_ATTEMPT_GRACE_SEC _OPEN_COMMIT_READINESS_DEBT_STATUSES _CANONICAL_OBLIGATION_ITEM_RE _normalize_fingerprint_text _normalize_obligation_item_key _stable_digest _make_obligation_fingerprint _looks_like_public_obligation_id _max_iso_ts _min_iso_ts _filter_repo_scope _commit_readiness_debts_view _OBLIGATION_STR_DEFAULTS _DEBT_STR_DEFAULTS _RUN_STR_DEFAULTS _ATTEMPT_STR_DEFAULTS _ATTEMPT_MERGE_INCOMING_FIRST _ATTEMPT_MERGE_INCOMING_LISTS _RUN_STATUS_ICONS _filter_lifecycle_records _allocate_prefixed_id _append_finding_lines ObligationItem CommitReadinessDebtItem AdvisoryRunRecord CommitAttemptRecord _attempt_identity_tuple _attempt_order_key _coerce_int _infer_next_prefixed_sequence _normalize_findings _merge_attempt infer_review_phase _parse_iso_ts _dedupe_strings _utc_now",
        "review_state_model.py": "AdvisoryReviewState",
    }
    review_state_extraction_rows = {
        f"ouroboros/review_state.py::{symbol}": f"ouroboros/{owner}::{symbol}"
        for owner, symbols in review_state_extraction_symbols_by_owner.items()
        for symbol in symbols.split()
    }
    implemented.update(review_helpers_extraction_rows)
    implemented.update(review_state_extraction_rows)
    implemented.update(test_split_rows)
    implemented.update(web_extractions)
    implemented.update(config_extraction_rows)
    implemented["tests/test_repo_health_smoke.py::test_transition_rejects_function_swap_even_at_same_cardinality"] = "tests/test_repo_health_smoke.py::test_transition_allows_a_same_qualname_relocation_but_not_a_swap"
    # v7 stream S, lane S1: the spec 4.3.5 settings seam. One normalization for every
    # reader, one locked read-modify-write for the owner endpoints, one serializer for
    # the three writers, and the start-time mutator removed.
    settings_seam_rows = {
        "ouroboros/gateway/onboarding.py::_settings_fingerprint":
            "ouroboros/gateway/owner_settings.py::settings_document_digest",
        "ouroboros/config.py::load_settings_lock_held":
            "ouroboros/config.py::normalize_settings_raw",
        "ouroboros/gateway/owner_settings.py::_owner_write_settings":
            "ouroboros/gateway/owner_settings.py::_owner_update_settings",
        "ouroboros/config.py::save_settings": "ouroboros/config.py::serialize_settings",
        "ouroboros/packaged_cli.py::_save_settings": "ouroboros/packaged_cli.py::_save_settings",
        "ouroboros/launcher_onboarding.py::prepare_first_run_settings":
            "ouroboros/launcher_onboarding.py::prepare_first_run_settings",
        "tests/test_onboarding_host.py::test_pre_server_normalization_never_creates_the_settings_file":
            "tests/test_onboarding_host.py::test_pre_server_normalization_never_writes_the_settings_file",
        # Lane S2: the last start-time mutator, inside the server lifespan.
        "server.py::lifespan": "server.py::lifespan",
        "tests/test_onboarding_host.py::test_server_boot_normalization_carries_the_same_guard":
            "tests/test_onboarding_host.py::test_server_boot_never_writes_the_settings_file",
    }
    implemented.update(settings_seam_rows)
    # v7 stream T, lane T2: the write/edit/search/forward producers that never left
    # core.py publish their own result code. Same identity, same bytes, and the code
    # IS the answer the adapter already gave for those bytes, so the id is "none".
    t2_core_native_rows = {
        f"ouroboros/tools/core.py::{symbol}": f"ouroboros/tools/core.py::{symbol}"
        for symbol in "_data_write _write_file _edit_text _forward_to_worker".split()
    }
    implemented.update(t2_core_native_rows)
    existing_process_owner_rows.update(t2_core_native_rows)
    registry_extraction_no_facade_rows.update(t2_core_native_rows)
    # v7 lane T2b, owner item A.20 (batch #10): producers whose OBSERVABLE
    # classification the owner changed, so these rows carry the same tool-domain
    # delta id as the T1 cutover rather than "none".
    t2b_owner_delta_rows = {
        "ouroboros/tools/core.py::_send_photo",
        "ouroboros/tools/core.py::_send_video",
        "ouroboros/tools/core.py::_send_file",
        "ouroboros/tools/core.py::_write_file",
        "ouroboros/tools/core.py::_edit_text",
        "ouroboros/tools/core.py::_forward_to_worker",
        "ouroboros/tools/core.py::_data_read",
    }
    # The ratchet relocation contract renamed its own pin in place (commit 73360232)
    # without recording the rename; the row is the ledger half of that change.
    ratchet_relocation_rename = {
        "tests/test_repo_health_smoke.py::test_transition_rejects_function_swap_even_at_same_cardinality":
            "tests/test_repo_health_smoke.py::test_transition_allows_a_same_qualname_relocation_but_not_a_swap",
    }
    implemented.update(ratchet_relocation_rename)
    existing_process_owner_rows.update(ratchet_relocation_rename)
    registry_extraction_no_facade_rows.update(ratchet_relocation_rename)
    existing_process_owner_rows.update(settings_seam_rows)
    implemented.update(server_extraction_rows)
    # v7 stream S lane S2, spec 4.3.11 (Emergency Stop 2A): execute_panic_stop keeps
    # its path, name and public identity; only how it learns the bound port changes.
    s2_panic_delta_rows = {
        "ouroboros/server_control.py::execute_panic_stop":
            "ouroboros/server_control.py::execute_panic_stop",
    }
    implemented.update(s2_panic_delta_rows)
    existing_process_owner_rows.update(s2_panic_delta_rows)
    # No facade row: the symbol keeps its own path and name, so there is nothing to
    # re-export — only its keyword surface gained an optional argument.
    # Integration fix (D13): supervisor/git_ops pre-init module defaults follow the
    # environment-aware roots from ouroboros.config instead of a hardcoded
    # ~/Ouroboros; in-place, no facade — same shape as the panic row above.
    git_ops_delta_rows = {
        "supervisor/git_ops.py::DRIVE_ROOT": "supervisor/git_ops.py::DRIVE_ROOT",
        "supervisor/git_ops.py::REPO_DIR": "supervisor/git_ops.py::REPO_DIR",
    }
    implemented.update(git_ops_delta_rows)
    existing_process_owner_rows.update(git_ops_delta_rows)
    # v7 stream L-A lane L2b: verbatim extraction of the review substrate's record,
    # verdict and projection owners out of ouroboros/review_substrate.py.
    l2b_review_extraction_symbols_by_owner = {
        "review_records.py": "ReviewSlot ReviewRequest ReviewActorRecord ReviewRunResult HARDNESS_ADVISORY_VISIBLE HARDNESS_LABEL_ONLY HARDNESS_HARD_GATE",
        "review_verdict.py": "_TIER_ORDER _CRITERION_STATUSES _criteria_have_supported_evidence _criteria_shape_valid _contributing_actors aggregate_outcome_tier task_acceptance_is_clean DIALOGUE_CONTINUE DIALOGUE_UNREACHABLE DIALOGUE_STABLE_DISAGREEMENT DIALOGUE_STATUS_VALUES _contract_valid_actors aggregate_dialogue_status _unresolved_evidence_ref_labels panel_reason dissent_findings build_improvement_capsule",
        "review_projection.py": "_transport_error_status _public_review_reason _review_actor_projection _response_ref_projection _review_enforcement_impact _review_panel_id build_review_binding compact_review_projection",
    }
    l2b_review_extraction_rows = {
        f"ouroboros/review_substrate.py::{symbol}": f"ouroboros/{owner}::{symbol}"
        for owner, symbols in l2b_review_extraction_symbols_by_owner.items()
        for symbol in symbols.split()
    }
    implemented.update(l2b_review_extraction_rows)
    l2b_evidence_extraction_symbols = (
        "collect_turn_diff _ACCEPT_RESULT_CAP _ACCEPT_ARGS_CAP _ACCEPT_NOTES_CAP "
        "_ACCEPT_TRAJECTORY_MAX_CALLS _ACCEPT_ARTIFACT_PREVIEW_CAP _ACCEPT_ARTIFACT_PREVIEW_MAX_BYTES "
        "_ACCEPT_TOTAL_BUDGET _ACCEPT_OBLIGATIONS_MAX _ACCEPT_RETRIEVAL_URLS_MAX obligation_is_pending "
        "_accept_obligation_row task_acceptance_evidence_revision _accept_redact_cap _accept_task_contract "
        "_accept_protected_set _accept_verification_summary _accept_receipt_exhibits _accept_effective_claims "
        "_accept_claim_support_refs _accept_trajectory _accept_artifact_manifest _accept_enforce_budget "
        "_owner_content_projection _accept_owner_directives _ACCEPT_DELTA_CHILD_CAP _accept_capability_deltas"
    )
    l2b_evidence_extraction_rows = {
        f"ouroboros/review_evidence.py::{symbol}":
            f"ouroboros/review_evidence_sections.py::{symbol}"
        for symbol in l2b_evidence_extraction_symbols.split()
    }
    implemented.update(l2b_evidence_extraction_rows)
    l2b_skill_review_symbols_by_owner = {
        "skill_review_packs.py": "_SKILL_PACK_TOKEN_HEADROOM _skill_pack_token_budget _LOADABLE_BINARY_EXTENSIONS _SkillFileOverBudget _SkillFileUnreadable _SkillBinaryPayload _read_skill_text _build_skill_file_packs",
        "skill_review_rebuttals.py": "_review_history_path _accepted_rebuttals_path _load_accepted_rebuttals _persist_rebuttal_flips _fail_items_from_history_entry _record_accepted_rebuttal _build_skill_review_history_section _convergence_hint _render_accepted_rebuttals_section",
        "skill_review_prompt.py": "_SKILL_CHECKLIST_SECTION _SKILL_REVIEW_ITEMS _CRITICAL_ITEMS _load_governance_artifact _REPO_ROOT _build_review_prompt _emit_skill_advisory_warning _run_skill_advisory_pre_review _review_wave_budget_block _build_review_prompt_for_attempt",
        "skill_review_output.py": "render_skill_review_block _extract_actor_findings _parse_json_array _aggregate_status",
    }
    l2b_skill_review_rows = {
        f"ouroboros/skill_review.py::{symbol}": f"ouroboros/{owner}::{symbol}"
        for owner, symbols in l2b_skill_review_symbols_by_owner.items()
        for symbol in symbols.split()
    }
    implemented.update(l2b_skill_review_rows)
    # v7 stream S, lane S4: ouroboros/extension_loader.py split into owner leaves.
    # The loader keeps the extension lifecycle; the registries, the namespace
    # encoding, the child-catalog re-validation, the staged import trees, the
    # liveness projection and the PluginAPI object each get one owner.
    s4_extension_symbols_by_owner = {
        "extension_registry_state.py": "_ExtensionRegistrations _ExtensionLoadFailure _PluginAPIConfig _lock _extensions _extension_modules _load_failures _unloading _lifecycle_locks _tools _routes _ws_handlers _ui_tabs _settings_sections _lifecycle_lock_for _record_companion_name",
        "extension_surface_names.py": "_EXTENSION_NAME_PREFIX _EXTENSION_SKILL_TOKEN_MAX _EXTENSION_SHORT_MAX _EXTENSION_NAME_RE _extension_skill_token extension_name_prefix extension_surface_name parse_extension_surface_name _widget_span_from_render _assert_namespace_path _assert_tool_name",
        "extension_child_catalog.py": "_out_of_process_handler_proxy _validate_child_catalog_namespace _validate_child_tool_descriptor _validate_child_route_descriptor _validate_child_ws_descriptor _validate_child_ui_descriptor _validate_child_settings_descriptor",
        "extension_import_staging.py": "_plugin_entry_path _module_key _purge_extension_bytecode _stage_extension_import_tree _IMPORT_SWEEP_GRACE_SEC _sweep_stale_extension_imports",
        "extension_liveness.py": "_extension_runtime_state _deps_block_reason _apply_deps_block runtime_state_for_skill_name runtime_state_for_loaded_skill is_extension_live _revert_enabled_after_load_error",
        "extension_plugin_api.py": "PluginAPIImpl current_execution_mode _reject_extension_child_side_effect mint_skill_token set_ws_broadcaster _ws_broadcaster",
    }
    s4_extension_rows = {
        f"ouroboros/extension_loader.py::{symbol}": f"ouroboros/{owner}::{symbol}"
        for owner, symbols in s4_extension_symbols_by_owner.items()
        for symbol in symbols.split()
    }
    implemented.update(s4_extension_rows)
    # v7 stream S, lane S4: ouroboros/tools/control.py split into owner leaves.
    # get_tools() stays with the catalog owner; every handler and helper it wires
    # gets one. The leaves carry the parent's hot-code label, nothing more.
    s4_control_symbols_by_owner = {
        "control_events.py": "_SCHEDULE_EMIT_LOCK _PROMOTE_CONFIRM_TIMEOUT_SEC _PROMOTE_CONFIRM_POLL_SEC _emit_control_event _promotion_pool_disabled_from_snapshot _routing_status_root _wait_for_promotion_admission _wait_for_routing_annotation _emit_and_wait_for_routing",
        "control_routing.py": "_attach_origin_from_metadata _attach_swarm_intent _cached_swarm_handoff _finish_swarm_handoff _promote_chat_to_task _list_projects _route_to_project _steer_task",
        "control_subagent_spec.py": "VALID_SUBTASK_MEMORY_MODES schedule_subagent_properties schedule_subagent_param_names _INTERNAL_SCHEDULE_OPTIONS _validated_schedule_fields RETIRED_SCHEDULE_PARAMS",
        "control_scheduling.py": "_record_scheduled_subagent _emit_swarm_fanout _subagent_slot_note _capability_mismatch_message _finalize_schedule_emission _build_acting_constraint _select_subagent_constraint _populate_subagent_event_extras _prepare_child_drive _earliest_deadline_at _build_child_subagent_contract _resolve_executor_ref _inherited_workspace_from_active_repo _schedule_task",
        "control_runtime.py": "_evolution_restart_block_reason _request_restart _set_tool_timeout _promote_to_stable _request_deep_self_review _chat_history _update_scratchpad _send_user_message _update_identity _toggle_evolution _toggle_consciousness _switch_model",
        "control_task_results.py": "disclosable_capability_delta _subtask_outcome_summary _get_task_result _wait_attention_poll cache_horizon_note _wait_for_task _count_live_sibling_children _UNMINTED_WAIT_GRACE_SEC _unminted_wait_ids _children_roster_projection _wait_for_tasks",
    }
    s4_control_rows = {
        f"ouroboros/tools/control.py::{symbol}": f"ouroboros/tools/{owner}::{symbol}"
        for owner, symbols in s4_control_symbols_by_owner.items()
        for symbol in symbols.split()
    }
    implemented.update(s4_control_rows)
    # The broadcaster slot is REBOUND by set_ws_broadcaster, so a re-export would be
    # a snapshot that stops tracking its owner: the setter is the facade, the binding
    # is not.
    registry_extraction_no_facade_rows.add("ouroboros/extension_loader.py::_ws_broadcaster")
    existing_process_owner_rows.update(test_split_rows)
    registry_extraction_no_facade_rows.update(s2_panic_delta_rows)
    registry_extraction_no_facade_rows.update(git_ops_delta_rows)
    registry_extraction_no_facade_rows.update(set(test_split_rows) - test_split_facade_rows)
    registry_extraction_no_facade_rows.update(old for old in web_extractions if "::createChatInstance." in old)
    # W3: a module-private chat.js helper that moved with its only caller — never exported, so no facade.
    registry_extraction_no_facade_rows.add("web/modules/chat.js::projectIdFromTask")
    registry_extraction_no_facade_rows.update(w3_chat_primitive_rows)
    registry_extraction_no_facade_rows.add("tests/test_repo_health_smoke.py::test_transition_rejects_function_swap_even_at_same_cardinality")
    # S6 (delegation/cancellation targeted fixes): no symbol moves — every row
    # is the SAME identity with a stated behaviour delta, so the owner is the
    # old path and the facade cell is "-". D03 is the one class these rows
    # share: a durable-registry mutator that could not read its file stops
    # answering as if the record were absent.
    s6_delta_rows = {
        identity: identity for identity in (
            "ouroboros/cancel_intents.py::claim_intent",
            "ouroboros/cancel_intents.py::release_claim",
            "ouroboros/cancel_intents.py::settle_intent",
            "ouroboros/cancel_intents.py::mark_intent_scope",
            "ouroboros/cancel_intents.py::mark_finalize_control_drained",
            "ouroboros/cancel_intents.py::_load_intents",
            "ouroboros/subagent_worktrees.py::_load_registry",
            "ouroboros/subagent_worktrees.py::find_execution_snapshot",
            "ouroboros/subagent_worktrees.py::prune_execution_snapshots",
            "ouroboros/subagent_worktrees.py::prune_orphans",
            "ouroboros/subagent_worktrees.py::remove_worktree",
            "ouroboros/subagent_worktrees.py::remove_execution_snapshot",
            "ouroboros/subagent_worktrees.py::provision_worktree",
            "ouroboros/subagent_worktrees.py::provision_payload_snapshot",
            "ouroboros/subagent_worktrees.py::provision_execution_snapshot",
        )
    }
    s6_disclosure_rows = {
        identity: identity for identity in (
            "ouroboros/cancel_intents.py::_SCHEMA_VERSION",
            "ouroboros/subagent_worktrees.py::_KIND_DELEGATED_EXEC",
            "ouroboros/task_finalization.py::register_final_answer_owed",
        )
    }
    s6_rows = {**s6_delta_rows, **s6_disclosure_rows}
    implemented.update(s6_rows)
    existing_process_owner_rows.update(s6_rows)
    registry_extraction_no_facade_rows.update(s6_rows)
    # v7 stream S test-giant theme splits (lane S7a): source module -> {owner path: moved symbols}.
    # A moved test, fixture or stub is owned by the sibling module that now hosts it; a test-private
    # import binding keeps the canonical production provider, which these splits never moved.
    s7a_test_split_symbols_by_owner = _inv.s7a_test_split_symbols_by_owner
    s7a_test_split_rows = {f"{source}::{symbol}": f"{owner}::{symbol}" for source, owners in s7a_test_split_symbols_by_owner.items() for owner, symbols in owners.items() for symbol in symbols.split()}
    s7a_test_split_facade_rows = {
        "tests/test_context.py::_make_health_env",
        "tests/test_delegated_run_isolation.py::_git",
        "tests/test_delegated_run_isolation.py::_isolated_entry",
        "tests/test_delegated_run_isolation.py::_nanny_ctx",
        "tests/test_delegated_run_isolation.py::_seed_target",
        "tests/test_delegated_subagent_transport.py::_gateway",
        "tests/test_delegated_subagent_transport.py::_owned_gateway_uses_each_test_transport",
        "tests/test_delivery_forced_finalization.py::_forced_test_context",
        "tests/test_extensions_api.py::_clean_extensions",
        "tests/test_extensions_api.py::_make_client",
        "tests/test_extensions_api.py::_stop_patches",
        "tests/test_extensions_api.py::_write_ext",
        "tests/test_promote_chat_flow.py::_isolated_projects_root",
        "tests/test_runtime_mode_elevation.py::_make_drive_ctx",
        "tests/test_runtime_mode_elevation.py::_seed_disk",
        "tests/test_workspace_executor.py::_init_repo",
    }
    implemented.update(s7a_test_split_rows)
    existing_process_owner_rows.update(s7a_test_split_rows)
    registry_extraction_no_facade_rows.update(set(s7a_test_split_rows) - s7a_test_split_facade_rows)
    # v7 stream S test-giant theme splits (lane S7b): source module -> {owner path: moved symbols}.
    # Same shape as the S7a block: the sibling module that hosts a test, fixture or helper owns it.
    # A symbol minted on the wip line after the merge base has no baseline identity, so it carries
    # no ledger row (tests/test_evolution_state_integrity_v3.py::_patch_commit_seam is the one case).
    s7b_test_split_symbols_by_owner = _inv.s7b_test_split_symbols_by_owner
    s7b_test_split_rows = {f"{source}::{symbol}": f"{owner}::{symbol}" for source, owners in s7b_test_split_symbols_by_owner.items() for owner, symbols in owners.items() for symbol in symbols.split()}
    s7b_test_split_facade_rows = {
        "tests/test_skill_exec.py::_build_skill",
        "tests/test_skill_exec.py::_make_ctx",
        "tests/test_skill_exec.py::_mark_reviewed_and_enabled",
        "tests/test_skill_exec.py::_valid_script_manifest",
        "tests/test_skill_review.py::_NEW_SKILL_REVIEW_PASS_ITEMS",
        "tests/test_skill_review.py::_build_skill",
        "tests/test_skill_review.py::_make_actor",
        "tests/test_skill_review.py::_make_ctx",
        "tests/test_skill_review.py::_pass_array_for_script_skill",
        "tests/test_skill_review.py::_patch_review",
        "tests/test_skill_loader.py::_valid_script_manifest",
        "tests/test_skill_loader.py::_write_skill",
    }
    implemented.update(s7b_test_split_rows)
    existing_process_owner_rows.update(s7b_test_split_rows)
    registry_extraction_no_facade_rows.update(set(s7b_test_split_rows) - s7b_test_split_facade_rows)
    # v7 stream S test-giant theme splits (lane W5): source module -> {owner path: moved symbols}.
    # Every row is a relocation inside the test tree: a moved test, stub, fixture or argv builder is
    # owned by the sibling module that now hosts it. A facade cell appears only where the parent still
    # imports the moved helper by its old name; a test the parent no longer mentions carries "-".
    w5_test_split_symbols_by_owner = _inv.w5_test_split_symbols_by_owner
    w5_test_split_symbols_by_owner = {
        "tests/test_devtools_benchmarks.py": {"tests/_devtools_benchmarks_shared.py": "REPO_ROOT _git_commit_all _git_repo _isolate_bench_runs_root", "tests/test_devtools_gaia.py": "_inspect_eval_log test_gaia_adapter_wires_settings_and_solver test_gaia_anti_leak_instruction_shape_and_all_solvers test_gaia_attachment_copy_avoids_duplicate_basenames test_gaia_attachment_falls_back_to_shared_files_root_and_rewrites_prompt test_gaia_attachment_reads_files_dict_keys test_gaia_audit_gold_verbatim_alone_is_weak_only test_gaia_audit_strip_boilerplate_prevents_self_flag test_gaia_bwrap_isolate_masks_answer_cache_and_fails_loud test_gaia_claude_code_solver_uses_stream_json_and_writes_trace test_gaia_codex_solver_uses_json_and_writes_trace test_gaia_credential_keys_tolerate_leading_whitespace test_gaia_distinct_same_basename_declarations_both_stage test_gaia_epistemic_instruction_shape_and_all_solvers test_gaia_events_serializer_carries_web_search_sources test_gaia_exact_lookup_does_not_stage_name_anywhere_matches test_gaia_leak_targets_match_real_cheats_and_spare_legit test_gaia_openai_websearch_pin_drops_base_url test_gaia_profile_defaults_are_not_silent_web_off test_gaia_real_taskstate_shape_declares_via_prompt test_gaia_render_injects_keys_and_free_host_service_port test_gaia_render_records_main_web_settings test_gaia_requested_task_ids_honors_sample_id_and_argv_lockstep test_gaia_runner_default_workers_four_strict_baseline_ablation test_gaia_sandbox_declarations_are_confined_to_shared_files test_gaia_sandbox_read_success_path_stages_bytes_and_provenance test_gaia_sandbox_staging_and_typed_error test_gaia_sanitized_env_keeps_only_needed_provider_key test_gaia_sanitized_env_preserves_keys_for_all_model_knobs test_gaia_sanitized_env_preserves_pinned_websearch_backend_key test_gaia_score_leakage_adjusted test_gaia_score_parses_inspect_json_logs test_gaia_score_prefers_official_eval_rows_when_result_json_exists test_gaia_settings_env_filters_custom_settings_secrets test_gaia_shared_files_fallback_blocks_traversal test_gaia_shared_files_fallback_prefers_prompt_subpath_over_basename test_gaia_solver_disable_tools_before_prompt test_gaia_solver_isolates_generic_subprocess_error test_gaia_solver_retries_transient_supervisor_startup test_gaia_solver_returns_real_host_paths_and_denies_secrets test_run_gaia_cannot_record_a_dead_inspect_eval_as_completed test_run_gaia_never_silently_clips_the_harness_error_it_records", "tests/test_devtools_harbor_jobs.py": "_harbor_job_tree _write_cached_task test_frontier_bench_wall_clock_cap_resolves_from_its_own_cache_org test_harbor_task_cache_lookup_never_borrows_another_orgs_timeout test_harbor_task_cache_lookup_refuses_an_ambiguous_task_name test_harbor_task_cache_lookup_uses_dataset_org_not_a_hardcoded_one test_run_tb_classifies_a_harbor_job_by_its_trials_not_its_exit_code test_run_tb_forwards_agent_and_verifier_env_without_leaking_values test_run_tb_job_config_carries_dataset_and_deep_merges_a_base_config test_run_tb_manifest_records_the_model_the_run_actually_resolved test_run_tb_refuses_an_escaping_subtree_before_creating_anything test_run_tb_submission_subtree_components_are_confined test_run_tb_submission_subtree_is_derived_from_the_dataset test_scrub_covers_the_harbor_written_job_config_for_ae_ve_values test_scrub_fails_closed_on_an_unsweepable_ae_ve_value_and_changes_nothing test_scrub_keeps_every_passthrough_occurrence_of_a_repeated_env_name test_scrub_sweeps_and_verifies_json_escaped_forms_not_only_the_literal test_scrubber_refuses_symlinks_instead_of_writing_through_them test_terminal_bench_ambiguous_harbor_result_fails_closed test_terminal_bench_execute_fails_closed_on_partial_deterministic_result test_terminal_bench_execute_fails_closed_on_unparseable_harbor_result test_terminal_bench_execute_writes_ledger_when_harbor_invocation_fails test_terminal_bench_explicit_execute_rejects_missing_requested_task test_terminal_bench_explicit_execute_rejects_unexpected_observed_task test_terminal_bench_explicit_execute_uses_requested_denominator test_terminal_bench_parses_harbor_task_outcomes test_terminal_bench_resolves_only_new_harbor_result test_terminal_bench_run_tb_builds_required_agent_kwargs test_terminal_bench_run_tb_validates_leaderboard_methodology test_terminal_bench_smoke_writes_manifest_and_planned_ledger", "tests/test_devtools_launcher_gate.py": "_CLEAN_LAUNCHER_SOURCE _GUARD_PROBE_SOURCE _SEAM_FORM_TEMPLATE _SEAM_PUBLICATION_DEFECT_SOURCE _SEAM_PUBLICATION_FIXED_SOURCE _SEAM_PUBLICATION_INDIRECT_SOURCE _SEAM_WRITE_FORMS _VIOLATING_LAUNCHER_SOURCE test_every_migrated_launcher_passes_the_structural_gate test_every_migrated_launcher_routes_through_both_manifest_seams test_invariant_c_derives_destinations_from_real_signatures_not_a_hand_written_table test_invariant_c_fails_closed_on_a_write_form_it_cannot_place test_invariant_c_places_the_destination_of_every_write_form test_pre_admission_resolver_sees_through_helpers_and_past_step_aside_branches test_the_gate_catches_a_refusal_authority_derived_from___file__ test_the_gate_catches_pre_admission_reads_parses_probes_and_nested_admission_args test_the_gate_resolves_imported_first_party_helpers_only test_the_gate_separates_argv_shaped_refusals_from_state_shaped_ones test_the_launcher_gate_catches_a_synthetic_violator_of_both_invariants test_the_launcher_gate_does_not_confuse_a_recorded_manifest_path_with_a_publication test_the_launcher_gate_forbids_publishing_a_manifest_inside_the_seam test_the_launcher_gate_leaves_static_launchers_alone test_the_launcher_gate_reproduces_both_round_six_confinement_defects", "tests/test_devtools_launcher_outcomes.py": "_REFUSAL_CASES _process_status_of _refusal_case_auto_run _refusal_case_harness_bench_fast _refusal_case_osworld_adapter_skeleton _refusal_case_pro_predictions _refusal_case_programbench _refusal_case_programbench_e2e _refusal_case_run_clb _refusal_case_run_cu_bridge_agent _refusal_case_run_cu_bridge_agent_seed_gate _refusal_case_run_pro _refusal_case_run_step_agent _refusal_case_swebench_predictions test_benchmark_admission_persists_the_refusal_before_enforcement_raises test_cu_bridge_refusal_mirrors_the_terminal_record_to_the_canonical_manifest test_cu_bridge_refusal_publishes_the_canonical_manifest_exactly_once test_finalize_run_manifest_records_a_typed_outcome_on_every_exit_path test_harness_bench_fast_manifest_is_durable_and_records_the_final_outcome test_harness_bench_fast_records_a_crash_instead_of_leaving_started test_migrated_launcher_exit_status_matches_the_recorded_exit_code test_pro_predictions_records_a_typed_outcome_when_it_stops_on_an_error test_programbench_e2e_ledger_is_append_only_and_manifest_is_written_first test_programbench_e2e_records_a_typed_outcome_on_its_failure_paths test_programbench_launcher_records_a_typed_outcome_on_its_failure_path test_step_agent_refusal_writes_its_manifest_only_on_admission_and_on_seam_exit test_swebench_predictions_records_a_typed_outcome_when_it_stops_on_an_error", "tests/test_devtools_osworld.py": "test_osworld_cli_default_repo_root_blocks_repo_internal_output test_osworld_cli_omitted_data_root_defaults_to_output_isolation test_osworld_cli_rejects_explicit_live_data_root test_osworld_logs_only_normalizer test_osworld_logs_only_normalizer_accepts_nested_trace_manifests test_osworld_preflight_rejects_nonisolated_unix_computer_use_state test_osworld_preflight_rejects_stale_unix_computer_use_review test_osworld_preflight_rejects_unix_computer_use_review_blockers test_osworld_shell_action_does_not_fabricate_bash_history test_osworld_step_predict_attaches_screenshot test_osworld_step_prompt_carries_image_and_in_app_done_guidance test_osworld_step_shell_action_uses_temp_script_without_raw_pkill_pattern", "tests/test_devtools_programbench.py": "_PROVIDER_ROUTE_ENV_KEYS _scrub_model_route_env test_programbench_build_instruction_renders_instance_fields test_programbench_cleanroom_image_ref_and_container_name test_programbench_cleanroom_preflight_requires_task_cleanroom_and_no_network test_programbench_client_poll_error_keeps_container_when_task_live test_programbench_git_workspace_does_not_commit_protected_reference test_programbench_instance_path_stays_under_run_root test_programbench_instruction_states_tree_ships_as_is test_programbench_model_preflight_keeps_openrouter_ids_and_checks_solve_model test_programbench_model_preflight_rejects_legacy_ids_on_direct_route test_programbench_official_eval_failure_writes_sidecars test_programbench_preflight_failure_writes_blocker_sidecars test_programbench_prepare_only_normalizes_raw_workspace test_programbench_prepare_seeded_workspace_is_idempotent_on_solved_tree test_programbench_prepare_seeded_workspace_moves_reference_and_sets_execute_bit test_programbench_resume_skipped_rows_are_successful test_programbench_second_run_reattaches_without_cleanroom_reset test_programbench_seed_workspace_from_image test_programbench_settled_failed_checkpoint_retries_fresh test_programbench_start_cleanroom_container_invokes_docker_run test_programbench_submission_excludes_both_root_binaries test_programbench_submission_failure_writes_sidecars test_programbench_submission_tarball_contract test_programbench_submission_tarball_excludes_repo_noise test_programbench_submit_and_wait_polls_until_terminal test_programbench_submit_and_wait_resumes_from_checkpoint_without_resubmit test_programbench_submit_and_wait_stale_checkpoint_falls_back_to_fresh_submit test_programbench_task_body_sets_executor_and_protected_policy test_programbench_terminal_status_reads_explicit_payload_status test_programbench_verify_reference_executable_runnable", "tests/test_devtools_runtime_attestation.py": "test_gaia_and_tb_launchers_add_no_runtime_attestation test_programbench_e2e_persists_the_manifest_when_attestation_refuses test_runtime_attestation_decides_commit_availability_before_skew test_runtime_attestation_is_wired_into_url_attaching_readiness_paths test_runtime_attestation_lineage_allows_descendants_only test_runtime_attestation_override_waives_only_the_evolved_runtime_reason test_runtime_attestation_records_both_facts_and_fails_closed test_runtime_attestation_requires_the_contracted_runtime_version_field", "tests/test_devtools_swe_pro.py": "_BASH_CAPTURE_AVAILABLE test_swe_predictions_fail_fast_still_writes_sidecars test_swe_predictions_rejects_unsafe_instance_id_before_logs_escape test_swe_pro_capture_excludes_base_untracked_snapshot test_swe_pro_capture_keeps_untracked_text_and_drops_binary test_swe_pro_capture_preserves_pure_lockfile_patch test_swe_pro_capture_requires_valid_base_and_external_output test_swe_pro_e1v2_curve_rows test_swe_pro_e1v2_port_has_csv_option_a_heal_and_no_secrets test_swe_pro_grade_rejects_repo_internal_output test_swe_pro_grade_reports_tri_state_verdicts test_swe_pro_grade_runs_official_eval_with_raw_sample test_swe_pro_grade_ungraded_covers_unparseable_and_empty_requirements test_swe_pro_manifest_records_the_derived_model_not_the_template test_swe_pro_prediction_capture_rejects_empty_patch test_swe_pro_predictions_continue_on_error_writes_denominator_ledger test_swe_pro_predictions_fail_fast_marks_remaining_requested_tasks test_swe_pro_predictions_rejects_unsafe_instance_id_before_patch_path test_swe_verified_preset_uses_official_dataset_name", "tests/test_devtools_terminal_bench.py": "test_bench_template_scaffold_defaults_v655 test_container_env_never_forwards_model_fallback test_harbor_agent_defaults_max_workers_four_and_probes_context_timeout test_run_ouroboros_task_terminal_nonzero_exit_is_not_interruption test_terminal_bench_adapter_defaults_to_required_acceptance_review test_terminal_bench_adapter_does_not_commit_target_workspace test_terminal_bench_adapter_forwards_gigachat_and_preflights_direct_provider test_terminal_bench_adapter_quotes_hostile_workspace_dir test_terminal_bench_adapter_refuses_container_secret_injection_by_default test_terminal_bench_harbor_adapter_is_optional_import test_terminal_bench_harbor_adapter_reads_canonical_version test_terminal_bench_harbor_context_uses_physical_metrics test_terminal_bench_metadata_declares_all_assisting_models test_terminal_bench_network_preflight_supports_openai_compatible test_terminal_bench_network_preflight_uses_configured_provider test_terminal_bench_openrouter_credit_preflight_skips_when_unconfigured test_terminal_bench_openrouter_credit_preflight_uses_authoritative_limit_remaining test_terminal_bench_openrouter_preflight_admits_an_uncapped_key test_terminal_bench_source_copy_excludes_secret_shaped_files test_terminal_bench_source_provenance_hashes_copied_tree test_terminal_bench_task_body_uses_top_level_actor_id", "devtools/benchmarks/osworld/normalize_logs.py": "normalize_bundle", "devtools/benchmarks/programbench/programbench_adapter.py": "build_instruction build_ouroboros_task_body classify_infra_failure cleanroom_image_ref container_name_for_instance create_submission_tarball preflight_cleanroom_container prepare_seeded_workspace seed_workspace_from_image start_cleanroom_container submit_and_wait terminal_task_status verify_reference_executable_runnable", "devtools/benchmarks/swe_bench/presets.py": "resolve_preset"},
        "tests/test_osworld_cu_bridge.py": {"tests/_osworld_cu_bridge_shared.py": "_attempt_dirs _cu_bridge_argv _cu_bridge_stubs", "tests/test_osworld_cu_bridge_claims.py": "test_a_lane_that_dies_between_scoring_and_its_finally_keeps_the_task_scored test_a_scored_but_unmarked_task_stays_refused_after_its_lock_goes_stale test_amend_task_manifest_merges_without_mutating_the_base test_an_interrupt_between_the_score_and_its_marker_does_not_release_the_claim test_claim_dir_is_confined_against_the_execution_checkout_not_only_the_launcher test_claim_dir_is_confined_to_outside_repo_and_live_data test_claim_rechecks_the_marker_after_winning_the_lock test_cu_bridge_claim_is_acquired_inside_the_try_that_releases_it test_cu_bridge_gates_provenance_before_the_vm_and_records_the_escape test_cu_bridge_marks_the_score_before_it_projects_the_result_anywhere test_cu_bridge_refuses_a_claim_dir_inside_the_checkout_it_was_handed test_cu_bridge_refuses_an_unconfined_claim_dir_before_anything_is_created test_cu_bridge_refuses_loudly_when_no_scored_state_can_be_recorded_at_all test_cu_bridge_releases_the_lock_and_keeps_the_marker_on_a_healthy_scored_run test_cu_bridge_retains_the_lock_when_the_scored_marker_will_not_persist test_scored_claim_is_fail_closed_and_is_never_released_without_a_durable_marker test_task_claim_key_is_filesystem_safe test_task_claim_serializes_lanes_and_first_scored_attempt_wins test_the_unconfirmed_marker_does_not_disturb_the_healthy_scored_path test_two_overlapping_attempts_never_share_one_canonical_record", "tests/test_osworld_cu_bridge_gate.py": "_FakeResetEnv _GateArgs _ns test_a_gate_terminated_example_is_not_a_budget_fault test_a_step_claim_the_server_cannot_honor_is_refused_before_the_vm_boots test_acceptance_claims_are_general_and_well_formed test_audit_reads_policy_turns_not_physical_calls test_gate_cancel_unconfirmed_is_the_one_condition_that_may_not_fail_open test_gate_claim_window_tracks_the_single_premise_round test_gate_phase_removes_the_mutating_tools_and_keeps_the_reading_ones test_gate_preamble_is_a_rubric_not_an_exception_list test_gate_round_posts_a_fresh_memory_gate_phase_task_and_reads_the_verdict test_gate_rubric_covers_named_mode_scope_and_prohibition test_gate_tool_trace_carries_full_args_for_the_offline_audit test_gate_turns_are_enforced_per_task_from_the_live_event_log test_gate_verdict_fails_open_unless_explicitly_infeasible test_gate_verdict_reads_the_answer_not_a_recap_of_the_options test_gate_verdict_tolerates_formatting_but_not_prose test_gate_window_is_zero_when_disabled_and_floored_when_enabled test_reset_verified_accepts_a_task_with_no_setup_config test_reset_verified_exhaustion_is_a_typed_infra_error_not_a_pass test_reset_verified_forces_the_snapshot_revert_before_every_retry test_reset_verified_rejects_the_silent_setup_skip_and_recovers_on_retry test_reset_verified_still_rejects_a_missing_screenshot test_step_budget_uses_policy_turns_not_gui_actions test_terminal_answer_text_prefers_final_answer_then_falls_back test_the_confirming_challenger_stays_removed test_the_post_gate_reset_republishes_the_vm_endpoint test_unknown_gate_turns_keep_the_full_reserve test_unused_gate_reserve_is_returned_to_the_worker", "tests/test_osworld_cu_bridge_prompts.py": "test_forensics_clauses_are_pinned_in_the_worker_prompt test_the_bench_agent_cannot_reach_the_bridge_url test_the_working_prompt_forbids_forcing_state_from_underneath_the_app test_v684_prompt_fixes_are_present_and_harmful_clauses_gone test_v685_contract_and_carveout_clauses", "tests/test_osworld_cu_bridge_provenance.py": "_attempt_manifests _refused_attestation_record test_a_checkout_other_than_the_campaign_pin_is_refused_before_the_vm_boots test_cu_bridge_keeps_the_ledger_row_when_the_canonical_outcome_cannot_be_written test_cu_bridge_keeps_the_outcome_files_when_the_ledger_cannot_be_appended test_cu_bridge_ledger_row_never_points_at_an_outcome_that_was_not_written test_cu_bridge_persists_the_attestation_record_it_was_handed test_cu_bridge_publication_failure_never_erases_an_obtained_score test_cu_bridge_refuses_before_the_claim_when_attestation_fails test_module_grandfather_matcher_uses_exact_repo_relative_paths test_osworld_methodology_preregisters_the_dedup_rule_and_defers_the_lane_generator test_osworld_operator_patch_raises_provider_lock_timeout_and_is_documented test_osworld_skeleton_persists_the_attestation_record_it_was_handed test_osworld_skeleton_seed_gate_refusal_short_circuits_the_preflight test_step_agent_preflight_persists_the_attestation_record_it_was_handed test_step_agent_seed_gate_refusal_is_typed_records_not_a_traceback"},
        "tests/test_preflight_runner.py": {"tests/_preflight_runner_shared.py": "REPO_ROOT _FIXTURE_PYTEST_INI _PREFLIGHT_PLUGIN_PROBLEMS _REAL_SPAWN_SKIP_REASON _REQUIRE_PLUGINS_ENV _commit_all _git _make_repo _preflight_plugin_problems stub_passes two_pass_env", "tests/test_preflight_candidate_capture.py": "_spy_on_candidate _start_conflicted_merge test_a_chmod_only_change_reaches_the_candidate test_a_failed_capture_is_a_named_hard_block_not_a_test_failure test_a_mixed_unmerged_index_drops_neither_staged_nor_conflicted_changes test_a_purely_conflicted_merge_runs_against_the_worktree_resolution test_a_raised_assembly_exception_is_owned_by_the_assembly_block test_a_staged_add_removed_from_the_worktree_is_absent_from_the_candidate test_a_staged_binary_change_reaches_the_candidate test_a_staged_change_reverted_in_the_worktree_lands_as_head_content test_a_staged_delete_with_a_recreated_untracked_file_mirrors_the_live_worktree test_a_zero_context_diff_config_still_assembles_the_candidate test_an_unmerged_resolution_by_deletion_is_absent_from_the_candidate test_an_untracked_file_with_a_non_utf8_name_reaches_the_candidate test_crlf_content_survives_the_capture_byte_for_byte test_disposable_index_matches_source_while_files_match_live_worktree test_non_unmerged_source_write_tree_failure_is_a_hard_block test_non_utf8_text_content_survives_the_capture_byte_for_byte test_untracked_listing_is_decoded_with_the_filesystem_codec", "tests/test_preflight_commit_gate.py": "_delete_loose_object test_a_broken_head_ref_does_not_masquerade_as_unborn test_a_failing_post_commit_gate_stops_publication test_a_gate_blocked_update_tx_is_never_promoted_by_boot_recovery test_a_red_gate_on_a_managed_update_rolls_the_merge_back test_a_repository_that_never_had_tests_is_still_out_of_scope test_an_unborn_head_is_proven_absent_not_unreadable test_an_unreadable_baseline_tree_hard_blocks_instead_of_reading_as_no_tests test_an_unreadable_first_parent_hard_blocks_the_post_commit_baseline test_an_unreadable_head_commit_hard_blocks_the_pre_commit_baseline test_the_post_commit_baseline_reaches_back_exactly_one_commit test_the_post_commit_gate_record_carries_the_same_review_metadata_as_its_siblings test_the_pre_commit_baseline_is_head_only_after_a_deliberate_removal", "tests/test_preflight_diagnosis.py": "test_a_genuine_crash_is_not_reclassified_by_timeout_text_elsewhere_in_the_output test_classify_does_not_blame_plugins_for_an_unrelated_usage_error test_classify_green_and_empty_pass test_classify_plugin_missing test_classify_skips_xdist_diagnoses_on_a_non_parallel_pass test_crash_diagnosis_keeps_the_full_pytest_output test_crash_pattern_still_matches_the_mid_line_short_summary_form test_crash_patterns_cover_xdist_controller_phrasing test_crash_patterns_ignore_a_bare_worker_id_in_test_text test_crash_patterns_need_the_whole_controller_line_shape test_crash_patterns_survive_terminal_decoration test_diagnosis_never_overruns_a_declared_max_output test_genuine_crash_still_gets_the_mark_it_serial_remediation test_hard_block_remediation_survives_caller_truncation test_pass_header_reports_that_pass_s_own_duration test_per_test_timeout_kill_is_not_told_to_mark_the_test_serial test_signal_method_timeout_banner_also_avoids_the_serial_remediation", "tests/test_preflight_hermetic_runs.py": "requires_preflight_plugins test_a_candidate_cannot_switch_the_parallel_plugins_off test_a_candidate_faking_the_parallel_flags_cannot_earn_a_green_pass test_a_green_pass_cannot_leak_a_child_into_the_next_pass test_a_second_timeout_keeps_the_excerpt_the_first_one_already_carried test_a_timed_out_pass_reports_what_the_killed_child_had_already_flushed test_both_lanes_empty_blocks test_both_passes_execute_and_partition test_empty_serial_lane_is_green test_hermetic_pytest_applies_candidate_diff_and_scrubs_live_env test_hermetic_pytest_prefers_agent_python_env test_hermetic_pytest_timeout_invokes_full_tree_reaper test_hermetic_pytest_timeout_reaps_detached_session_child test_pass2_timeout_names_serial_pass test_resolve_preflight_timeout_env_override test_the_parallel_pass_really_starts_more_than_one_worker test_timeout_excerpt_stays_inside_the_budget_and_keeps_the_tail test_timeout_message_survives_an_empty_or_missing_excerpt test_worker_crash_is_hard_block", "tests/test_preflight_pass_orchestration.py": "test_a_caller_supplied_parallel_argv_is_not_blocked_for_missing_worker_evidence test_a_failing_pass_blocks_even_when_its_diagnosis_cannot_be_rendered test_a_nominally_parallel_pass_on_one_worker_is_a_hard_block test_a_pass_whose_tree_cannot_be_proven_gone_blocks_even_when_it_exits_zero test_a_red_first_pass_stops_the_run_and_returns_only_its_own_output test_an_unreadable_process_table_is_a_containment_failure_not_an_empty_container test_an_unrenderable_output_budget_blocks_instead_of_passing test_deleting_the_whole_test_suite_is_a_hard_block test_each_pass_gets_the_exact_remaining_budget test_exit_5_is_green_per_pass_but_blocks_when_every_pass_is_empty test_plugins_are_verified_before_the_candidate_tree_exists test_second_pass_never_starts_once_the_total_budget_is_gone test_temp_root_is_swept_between_passes_not_only_at_teardown test_the_legacy_single_pass_does_not_require_the_parallel_plugins test_the_production_entry_points_do_not_short_circuit_a_deleted_suite", "tests/test_preflight_process_containment.py": "test_a_detached_child_is_still_found_after_its_root_exits test_a_member_that_replaced_its_environment_is_still_detected_by_its_group test_a_stranger_that_took_a_recycled_pid_or_pgid_is_never_signalled test_a_windows_root_that_cannot_be_job_held_is_killed_and_reported test_process_container_kills_a_descendant_that_left_the_group test_spawn_plants_the_membership_token_in_a_caller_supplied_env test_the_membership_token_survives_the_preflight_env_scrub test_the_process_group_is_a_detection_input_and_is_never_signalled test_windows_containment_uses_the_shared_job_seam_and_closes_the_spawn_race", "tests/test_preflight_process_reaping.py": "test_a_descendant_unreadable_before_it_was_ever_seen_is_still_a_leak test_a_member_is_signalled_at_most_once_however_long_the_scans_run test_a_member_that_becomes_unreadable_is_a_leak_not_a_clean_reap test_a_root_unreadable_from_the_very_first_scan_is_still_a_leak test_a_windows_job_teardown_that_does_not_confirm_itself_is_a_containment_failure test_an_unanswerable_membership_probe_is_unreadable_not_absent test_reap_does_not_mistake_an_unwaited_corpse_for_a_live_member test_reap_fails_when_a_member_stays_alive_across_scans test_the_deadline_report_names_the_last_scan_that_actually_saw_something test_the_ps_membership_branch_answers_unreadable_for_a_live_pid", "ouroboros/platform_layer.py": "force_kill_pid pid_is_alive"},
        "tests/test_ui_smoke_playwright.py": {"tests/_ui_smoke_shared.py": "REPO_ROOT _free_port _wait_health _wait_supervisor_ready direct_server direct_server_with_data", "tests/test_ui_smoke_widgets.py": "_write_phase3_widget_smoke_extension test_ui_owner_context_mode_and_scope_review_ack test_ui_smoke_phase3_declarative_widgets_and_settings test_ui_smoke_v679_subagent_depth_zero_round_trips_through_settings", "tests/test_ui_smoke_cards.py": "test_ui_smoke_direct_mode_nests_subagent_child_cards test_ui_smoke_finished_cards_keep_height_when_transcript_overflows test_ui_smoke_live_card_mutations_preserve_viewport test_ui_smoke_live_cards_keep_usable_geometry_at_depth_and_in_project_panel", "tests/test_ui_smoke_chat.py": "_install_controlled_visual_viewport _mobile_keyboard_drawer_assertions test_ui_smoke_chat_chronology_reconnect_and_plain_answer_marker test_ui_smoke_collapsed_activity_line_named_vs_unnamed test_ui_smoke_desktop_composer_chips_above_input_send_inside test_ui_smoke_direct_mode_chat_scrolls_on_desktop test_ui_smoke_mobile_composer_toolbar_does_not_overlap_input test_ui_smoke_mobile_keyboard_state_cannot_hide_open_drawer_chromium test_ui_smoke_mobile_keyboard_state_cannot_hide_open_drawer_webkit", "tests/test_ui_smoke_login.py": "test_ui_smoke_dismiss_overlapping_settle_never_freezes_the_card test_ui_smoke_dismiss_overlapping_start_cannot_drop_a_live_job test_ui_smoke_login_recovery_reconcile_detach_and_retry_are_explicit test_ui_smoke_stale_get_cannot_overwrite_login_terminal_faces test_ui_smoke_window_pagehide_detaches_login_without_lifecycle_http", "tests/test_ui_smoke_review_controls.py": "test_ui_smoke_cancel_run_button_eligibility_and_cancelled_state test_ui_smoke_review_truth_is_visible_in_chat_and_logs test_ui_smoke_superseded_input_dialog_resolves_object_result test_ui_smoke_v639_skip_review_button", "tests/fixtures_mock_llm.py": "MockLLMServer"},
    }
    w5_test_split_rows = {f"{source}::{symbol}": f"{owner}::{symbol}" for source, owners in w5_test_split_symbols_by_owner.items() for owner, symbols in owners.items() for symbol in symbols.split()}
    w5_test_split_facade_rows = {
        "tests/test_devtools_benchmarks.py::REPO_ROOT",
        "tests/test_devtools_benchmarks.py::_git_commit_all",
        "tests/test_devtools_benchmarks.py::_git_repo",
        "tests/test_preflight_runner.py::REPO_ROOT",
        "tests/test_preflight_runner.py::_PREFLIGHT_PLUGIN_PROBLEMS",
        "tests/test_preflight_runner.py::_REAL_SPAWN_SKIP_REASON",
        "tests/test_preflight_runner.py::_REQUIRE_PLUGINS_ENV",
        "tests/test_ui_smoke_playwright.py::_free_port",
        "tests/test_ui_smoke_playwright.py::_wait_health",
    }
    implemented.update(w5_test_split_rows)
    existing_process_owner_rows.update(w5_test_split_rows)
    registry_extraction_no_facade_rows.update(set(w5_test_split_rows) - w5_test_split_facade_rows)
    # v7 stream L: llm.py splits into ten owner leaves. Module-level names keep a
    # facade on llm.py; LLMClient members move into owner mixins the class
    # composes, so the class inherits the exact same function objects.
    llm_extraction_symbols_by_owner = {
        "llm_attempt.py": "_CACHE_TTL_SECONDS _VALID_CACHE_TTLS _applied_payload_cache_ttl _attempt_request _candidate_before_dispatch _canonical_candidate_bytes _execute_candidate _execute_candidate_async _is_structured_context_overflow_body _is_structured_context_overflow_exception _physical_candidate _route_normalizes_cache_breakpoints _structured_error_values cache_ttl_seconds supports_message_cache_control",
        "llm_capability_policy.py": "_MANDATORY_VALUE_MARKERS _OPTIONAL_DROPPABLE_PARAMS _OPTIONAL_SAMPLING_PARAMS normalize_reasoning_effort",
        "llm_routing.py": "_OR_PROVIDER_PRESETS _resolve_or_provider",
        "llm_messages.py": "_reasoning_signature_portable_across_or_providers",
        "llm_local.py": "LocalContextTooLargeError _LOCAL_COMPACTION_MODES _compact_local_text _compact_markdown_sections _estimate_message_chars _split_markdown_sections",
        "llm_openai_compatible.py": "_FALSE_LIKE_ENV_VALUES",
        "llm_pricing.py": "add_usage fetch_cloudru_pricing fetch_openrouter_pricing",
    }
    llm_extraction_rows = {
        f"ouroboros/llm.py::{symbol}": f"ouroboros/{owner}::{symbol}"
        for owner, symbols in llm_extraction_symbols_by_owner.items()
        for symbol in symbols.split()
    }
    llm_mixin_symbols_by_owner = {
        ("llm_attempt.py", "_PayloadCachePolicyMixin"): "_MAX_CACHE_BREAKPOINTS _normalize_payload_cache_ttl _payload_cache_breakpoints _pop_cache_breakpoint_disclosure",
        ("llm_capability_policy.py", "_CapabilityPolicyMixin"): "_CAPABILITIES_FETCH_OK _CONTEXT_LENGTH_CACHE _EFFORT_CEILING_CACHE _EFFORT_CEILING_LOADED _EFFORT_FLOOR_CACHE _EFFORT_FLOOR_LOADED _EFFORT_FLOOR_RELOAD_SEC _NESTED_REASONING_PARAM _REJECTED_PARAMS_CACHE _REJECTED_PARAMS_LOADED _REJECTED_PARAMS_RELOAD_SEC _SUPPORTED_PARAMS_CACHE _SUPPORTED_PARAMS_FETCHED _apply_rejected_param_cache _clamp_effort_for_model _effort_ceiling_for _effort_floor_for _fetch_openrouter_capabilities _get_supported_parameters _known_rejected_params _mandatory_value_rejection _parameter_rejection_error _payload_effort _pop_effort_clamp_disclosure _record_effort_ceiling _record_effort_floor _remember_rejected_params _retry_without_optional_sampling _set_payload_effort clamp_effort_for_route metadata_fetch_attempted_and_failed openrouter_context_length",
        ("llm_routing.py", "_ProviderRoutingMixin"): "_explicit_cache_affinity_identity _get_async_remote_client _get_client _get_local_client _get_remote_client _make_no_proxy_async_client _make_no_proxy_client _no_proxy_timeout _openrouter_session_identity _parse_provider_model _prompt_cache_identity _qualified_model_name _resolve_remote_target probe_oversized_context",
        ("llm_messages.py", "_MessageShapingMixin"): "_REASONING_CONTENT_BLOCK_TYPES _content_with_system_notice_marker _copy_messages_with_cache_policy _has_openrouter_reasoning_details _has_replayed_reasoning_metadata _is_deferrable_image_user_turn _model_family _normalize_system_message_placement _replace_image_blocks_with_placeholder _strip_openrouter_roundtrip_metadata sanitize_reasoning_on_model_switch",
        ("llm_fallback.py", "_RecoveryLadderMixin"): "_create_chat_completion_with_retries _create_chat_completion_with_retries_async _is_http_status _is_transient_body_error _openrouter_signature_retry_kwargs _param_retry_kwargs_for_body_error _provider_body_error _reroute_kwargs_for_body_error _reroute_same_model_kwargs _retry_without_prompt_cache_parameter _rotate_openrouter_session_affinity _strip_kwargs_for_encrypted_body_error",
        ("llm_anthropic.py", "_AnthropicLaneMixin"): "_anthropic_blocks_from_content _anthropic_image_block _build_anthropic_messages _build_anthropic_tool_choice _cache_write_split _chat_anthropic _coalesce_anthropic_message _normalize_anthropic_response _sanitize_anthropic_tool_result_content _stringify_anthropic_content",
        ("llm_gigachat.py", "_GigaChatLaneMixin"): "_chat_gigachat _get_gigachat_client _gigachat_function_result _gigachat_messages _gigachat_text _normalize_gigachat_response",
        ("llm_local.py", "_LocalLaneMixin"): "_chat_local _prepare_messages_for_local_context",
        ("llm_openai_compatible.py", "_OpenAICompatibleLaneMixin"): "_build_remote_kwargs _normalize_remote_response _openrouter_main_web_search_tool extract_display_reasoning",
        ("llm_pricing.py", "_GenerationCostMixin"): "_fetch_generation_cost",
    }
    llm_mixin_rows = {
        f"ouroboros/llm.py::LLMClient.{symbol}": f"ouroboros/{owner}::{mixin}.{symbol}"
        for (owner, mixin), symbols in llm_mixin_symbols_by_owner.items()
        for symbol in symbols.split()
    }
    implemented.update(llm_extraction_rows)
    implemented.update(llm_mixin_rows)
    registry_extraction_no_facade_rows.update(llm_mixin_rows)
    # Shared delta registry: D09 is spec 4.3.2 (LLM local retry) and its
    # typed-refusal companion. The id rides the row of each symbol it changed.
    llm_semantic_delta_ids = {"ouroboros/llm.py::LLMClient._chat_local": "D09"}
    # The recovery ladder stops consuming a typed policy refusal (same id).
    llm_semantic_delta_ids.update({
        f"ouroboros/llm.py::LLMClient.{symbol}": "D09"
        for symbol in (
            "_create_chat_completion_with_retries",
            "_create_chat_completion_with_retries_async",
            "_retry_without_prompt_cache_parameter",
            "_openrouter_signature_retry_kwargs",
            "_retry_without_optional_sampling",
        )
    })
    for row in rows:
        delta = v7_evidence._migration_json(row["semantic delta"], ("id", "note"))
        upstream = v7_evidence._migration_json(row["upstream-transfer status/note"], ("status", "note"))
        assert upstream["note"]
        assert row["characterization test"] != "-"
        if row["old path/symbol"] in implemented:
            assert upstream["status"] == "pending"
            assert row["new owner/path"] == implemented[row["old path/symbol"]]
            owner_path = row["new owner/path"].split("::", 1)[0]
            if (
                row["old path/symbol"] in existing_process_owner_rows
                or row["old path/symbol"] in registry_dependency_owners | web_extractions
            ):
                assert (REPO / owner_path).is_file()
            else:
                assert owner_path in v7_evidence.APPROVED_PENDING_OWNERS
            if row["old path/symbol"] in settings_seam_rows:
                # In-place semantic changes: the old identity keeps working because it
                # is still implemented at the old path (a caller, a wrapper, or the same
                # function with a new body), not because a re-export forwards it.
                assert delta["id"] == "D03" and delta["note"]
                assert row["facade/public contract"] == "-"
                continue
            expected_delta = llm_semantic_delta_ids.get(row["old path/symbol"]) or (
                # spec 4.3.6: the settings vocabulary moved as-is, then the three no-op
                # knobs were retired from it (an approved observable delta).
                "D04"
                if row["old path/symbol"] in {
                    "ouroboros/config.py::SETTINGS_DEFAULTS",
                    "ouroboros/config.py::RETIRED_SETTING_KEYS",
                }
                # plan 1.9 batch 8: the ratchet-transition test renamed with its relaxed contract.
                else "D11"
                if row["old path/symbol"] == "tests/test_repo_health_smoke.py::test_transition_rejects_function_swap_even_at_same_cardinality"
                else "D07"
                if row["old path/symbol"] in s2_panic_delta_rows
                else "D13"
                if row["old path/symbol"] in git_ops_delta_rows
                else "D08"
                if row["old path/symbol"] in s6_delta_rows
                else "D02"
                if row["old path/symbol"] in t2b_owner_delta_rows | {
                    "ouroboros/tools/registry.py::ToolEntry",
                    "ouroboros/tools/registry.py::ToolRegistry",
                    # T1: the classification cutover is a spec 4.3.3 tool-domain delta.
                    "ouroboros/loop_tool_execution.py::_extract_result_metadata",
                    "ouroboros/loop_tool_execution.py::_is_tool_execution_failure",
                    "ouroboros/loop_tool_execution.py::_structured_tool_failure",
                    "ouroboros/_outcome_tool_errors.py::_BLOCKING_TOOL_STATUSES",
                    "ouroboros/_outcome_tool_errors.py::_POLICY_DENIAL_STATUSES",
                    "ouroboros/reflection.py::should_generate_reflection",
                    "tests/test_tool_execution_classification.py::test_shell_and_claude_failures_are_treated_as_tool_failures",
                }
                else "D18" if row["old path/symbol"] in (s3b_queue_handle_rows | s3b_pool_handle_rows)
                else s3_semantic_delta_ids.get(row["old path/symbol"], "none")
            )
            assert delta["id"] == expected_delta and delta["note"]
            expected_facade = (
                "-"
                if row["old path/symbol"] in {
                    "ouroboros/tools/registry.py::ToolRegistry._ephemeral_block",
                    "ouroboros/tools/registry.py::ToolRegistry._subagent_and_update_gate",
                    "ouroboros/tools/registry.py::ToolRegistry._heal_mode_block",
                    "ouroboros/tools/registry.py::_executor_backend_candidate_allowed",
                    "ouroboros/tools/registry.py::_command_mentions_protected_root",
                    "ouroboros/tools/registry.py::_light_mode_payload_mutation_allowed",
                    "ouroboros/tools/registry.py::ToolRegistry._protected_shell_block",
                    "ouroboros/tools/registry.py::ToolRegistry._git_protected_roots",
                    "ouroboros/tools/registry.py::ToolRegistry._resolved_shell_cwd",
                    "ouroboros/tools/registry.py::ToolRegistry._external_workspace_git_block",
                    "ouroboros/tools/registry.py::ToolRegistry._external_runtime_protected_paths",
                    "ouroboros/tools/registry.py::ToolRegistry._external_shell_runtime_or_secret_block",
                    "ouroboros/tools/registry.py::ToolRegistry._workspace_shell_write_block",
                    "ouroboros/tools/registry.py::ToolRegistry._shell_git_and_runtime_block",
                    "tests/test_external_workspace_access.py::_command_mentions_protected_root",
                    "ouroboros/tools/registry.py::ToolRegistry._run_shell_safety_check",
                    "ouroboros/tools/registry.py::_light_repo_snapshot",
                    "ouroboros/tools/registry.py::_format_light_repo_write_block",
                    "ouroboros/tools/registry.py::_git_ref_snapshot",
                    "ouroboros/tools/registry.py::ToolRegistry._snapshot_owner_files",
                    "ouroboros/tools/registry.py::ToolRegistry._restore_owner_files",
                    "ouroboros/tools/registry.py::ToolRegistry._run_shell_post_checks",
                    "tests/test_skill_exec.py::test_run_shell_restores_obfuscated_self_authored_state_marker",
                    "ouroboros/tools/registry.py::SKILL_OWNER_STATE_FILENAMES",
                    "ouroboros/tools/registry.py::parse_porcelain_paths",
                    "ouroboros/tools/registry.py::safe_relpath",
                    "ouroboros/tools/registry.py::_detect_runtime_mode_elevation",
                    "ouroboros/tools/registry.py::_SUBAGENT_SHELL_SECRET_MARKERS",
                    "ouroboros/tools/registry.py::_subagent_shell_targets_secret",
                    "ouroboros/tools/registry.py::_detect_mutative_toggle_self_change",
                    "ouroboros/tools/registry.py::_detect_evolution_owner_control_self_change",
                    "ouroboros/tools/registry.py::_detect_context_mode_self_lowering",
                    "ouroboros/tools/registry.py::_READ_ONLY_INSPECTION_COMMANDS",
                    "ouroboros/tools/registry.py::_COMMAND_HEAD_WRAPPERS",
                    "ouroboros/tools/registry.py::_READ_ONLY_GIT_SUBCOMMANDS",
                    "ouroboros/tools/registry.py::_SEARCH_TOOL_EXEC_OPTIONS",
                    "ouroboros/tools/registry.py::_DENIED_READ_OPTIONS",
                    "ouroboros/tools/registry.py::_TRUSTED_EXECUTABLE_DIRS",
                    "ouroboros/tools/registry.py::_trusted_read_head",
                    "ouroboros/tools/registry.py::_denied_read_option",
                    "ouroboros/tools/registry.py::_NESTED_EXECUTION_MARKERS",
                    "ouroboros/tools/registry.py::_NESTED_EXECUTION_TOKENS",
                    "ouroboros/tools/registry.py::_is_pure_read_inspection",
                    "ouroboros/tools/registry.py::_detect_scope_review_floor_self_lowering",
                    "ouroboros/tools/registry.py::_detect_safety_mode_self_lowering",
                    "ouroboros/tools/registry.py::_detect_owner_skill_attest_self_call",
                    "ouroboros/tools/registry.py::_SKILL_OWNER_STATE_STEMS",
                    "ouroboros/tools/registry.py::_DETACHED_PROCESS_MARKERS",
                    "ouroboros/tools/registry.py::_mentions_skill_owner_state",
                    "ouroboros/tools/registry.py::_mentions_detached_process",
                    "ouroboros/tools/registry.py::LIGHT_SHELL_WRITER_COMMANDS",
                    "ouroboros/tools/registry.py::SKILL_OWNER_STATE_STEMS",
                    "ouroboros/tools/registry.py::build_resolved_resource_binding",
                    "ouroboros/tools/registry.py::interpreter_family",
                    "ouroboros/tools/registry.py::light_shell_repo_mutation",
                    "ouroboros/tools/registry.py::protected_artifact_shell_block_reason",
                    "ouroboros/tools/registry.py::runtime_data_guard_targets",
                    "ouroboros/tools/registry.py::shell_command_string",
                    "ouroboros/tools/registry.py::strip_leading_env_assignments",
                    "ouroboros/tools/registry.py::sudo_noninteractive_violation",
                    "ouroboros/tools/registry.py::unwrap_env_argv",
                    "ouroboros/tools/registry.py::workspace_executor_state_write_block",
                    "ouroboros/tools/registry.py::writer_target_tokens",
                    "ouroboros/tools/registry.py::PROTECTED_RUNTIME_PATHS",
                    "ouroboros/tools/registry.py::task_artifact_dir_path",
                    "ouroboros/tools/registry.py::task_id_for_artifacts",
                    "ouroboros/tools/registry.py::run_shell_git_block_reason",
                    "ouroboros/tools/registry.py::workspace_git_safety_violation",
                    "ouroboros/tools/registry.py::is_absolute_path_text",
                    "ouroboros/tools/registry.py::path_text_is_inside",
                    "ouroboros/tools/registry.py::shell_argv",
                    "ouroboros/tools/registry.py::shell_argv_with_path_tokens",
                    "ouroboros/tools/registry.py::PROTECTED_RUNTIME_PATHS_LOWER",
                    "ouroboros/tools/registry.py::shell_has_write_indicator",
                    "ouroboros/tools/registry.py::shell_writer_targets_protected",
                    "ouroboros/tools/registry.py::is_external_workspace",
                    "ouroboros/tools/registry.py::normalize_root",
                    "ouroboros/tools/registry.py::resolve_shell_cwd",
                    "ouroboros/tools/registry.py::SKILL_PAYLOAD_CONTROL_DIRNAMES",
                    "ouroboros/tools/registry.py::is_skill_payload_path",
                    "ouroboros/tools/registry.py::resolve_skill_payload_target",
                } | registry_extraction_no_facade_rows
                else row["old path/symbol"]
            )
            assert row["facade/public contract"] == expected_facade
        elif row["old path/symbol"] in retired_current:
            assert row["new owner/path"] == retired_current[row["old path/symbol"]]
            assert row["facade/public contract"] == "-"
            assert delta["id"] == retired_delta_ids.get(row["old path/symbol"], "none")
            assert delta["note"]
            assert upstream["status"] == "retired"
            assert "v7 WIP" in upstream["note"]
        else:
            assert upstream["status"] == "pending"
            expected_delta = (
                "D08"
                if row["old path/symbol"] in s6_delta_rows
                else "D02"
                if row["old path/symbol"] in {
                    "ouroboros/tools/registry.py::ToolRegistry",
                    # T1: the classification cutover is a spec 4.3.3 tool-domain delta.
                    "ouroboros/loop_tool_execution.py::_extract_result_metadata",
                    "ouroboros/loop_tool_execution.py::_is_tool_execution_failure",
                    "ouroboros/loop_tool_execution.py::_structured_tool_failure",
                    "ouroboros/_outcome_tool_errors.py::_BLOCKING_TOOL_STATUSES",
                    "ouroboros/_outcome_tool_errors.py::_POLICY_DENIAL_STATUSES",
                    "ouroboros/reflection.py::should_generate_reflection",
                    "tests/test_tool_execution_classification.py::test_shell_and_claude_failures_are_treated_as_tool_failures",
                }
                else "none"
            )
            assert delta["id"] == expected_delta and delta["note"]
            assert row["new owner/path"] in v7_evidence.APPROVED_PENDING_OWNERS
            assert row["facade/public contract"] == row["old path/symbol"]
    # Enumerated rows are pinned by MEMBERSHIP, not by a total: every name listed
    # above must still be in the ledger. A literal grand total would churn on
    # every extraction slice and says nothing about correctness — the real
    # contract is that no row escapes classification, asserted below.
    assert sum(row["old path/symbol"] in implemented for row in rows) == len(implemented)
    assert sum(row["old path/symbol"] in retired_current for row in rows) == len(retired_current)
    assert v7_migration.APPROVED_SEMANTIC_DELTAS == frozenset({"none", "D02", "D03", "D04", "D05", "D06", "D07", "D08", "D09", "D11", "D13", "D18"})

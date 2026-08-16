"""Migration-ledger membership: every extraction the v7 branch performed is a row.

Split out of tests/test_v7_prologue_evidence.py so that module stays inside the size
ratchet as the ledger grows stream by stream. The assertions are unchanged.
"""

from __future__ import annotations

import importlib.util
import pathlib

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
    implemented.update(registry_core_rows)
    implemented.update(registry_resolution_rows)
    implemented.update(registry_guard_rows)
    implemented.update(registry_dispatch_method_rows)
    implemented.update(registry_dependency_owners | core_extraction_rows)
    implemented.update(git_extraction_rows)
    implemented.update(shell_extraction_rows)
    implemented.update(headless_extraction_rows)
    implemented.update(tool_access_extraction_rows)
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
    retired_current.update({"ouroboros/loop_tool_execution.py::_parse_plan_review_control": "retired:native plan_task ToolResult metadata replaces textual control parsing", "ouroboros/loop_tool_execution.py::PLAN_REVIEW_CONTROL_PREFIX": "retired:loop no longer imports the display-only plan footer prefix", "ouroboros/loop_tool_execution.py::_PLAN_REVIEW_OUTCOMES": "retired:plan producer validates the closed outcome vocabulary before publication"})
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
    implemented.update({name: name for name in ("ouroboros/loop_tool_execution.py::_FAILURE_PREFIXES", "ouroboros/loop_tool_execution.py::_extract_result_metadata", "ouroboros/_outcome_tool_errors.py::_BLOCKING_TOOL_STATUSES", "ouroboros/reflection.py::_ERROR_MARKERS")})
    implemented.update(w_stream_rows)
    existing_process_owner_rows.update({"ouroboros/tools/core.py::_code_search", 'ouroboros/tools/core.py::_filter_out_project_store', 'ouroboros/tools/core.py::_policy_is_skill_owner_state_target', 'ouroboros/tools/core.py::active_repo_dir_for', 'ouroboros/tools/core.py::active_tool_profile', 'ouroboros/tools/core.py::build_resolved_resource_binding', 'ouroboros/tools/core.py::decide_tool_access', 'ouroboros/tools/core.py::normalize_root', 'ouroboros/tools/core.py::normalize_runtime_data_path', 'ouroboros/tools/core.py::read_text', 'ouroboros/tools/core.py::SKILL_OWNER_STATE_FILENAMES', "ouroboros/loop_tool_execution.py::_FAILURE_PREFIXES", "ouroboros/loop_tool_execution.py::_extract_result_metadata", "ouroboros/_outcome_tool_errors.py::_BLOCKING_TOOL_STATUSES", "ouroboros/reflection.py::_ERROR_MARKERS"})
    registry_extraction_no_facade_rows.update({"ouroboros/loop_tool_execution.py::_FAILURE_PREFIXES", "ouroboros/loop_tool_execution.py::_extract_result_metadata", "ouroboros/_outcome_tool_errors.py::_BLOCKING_TOOL_STATUSES", "ouroboros/reflection.py::_ERROR_MARKERS"})
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
                or row["old path/symbol"] in registry_dependency_owners
            ):
                assert (REPO / owner_path).is_file()
            else:
                assert owner_path in v7_evidence.APPROVED_PENDING_OWNERS
            expected_delta = (
                "D02"
                if row["old path/symbol"] in {
                    "ouroboros/tools/registry.py::ToolEntry",
                    "ouroboros/tools/registry.py::ToolRegistry",
                }
                else "none"
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
            assert delta["id"] == "none" and delta["note"]
            assert upstream["status"] == "retired"
            assert "v7 WIP" in upstream["note"]
        else:
            assert upstream["status"] == "pending"
            expected_delta = (
                "D02"
                if row["old path/symbol"] == "ouroboros/tools/registry.py::ToolRegistry"
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
    assert v7_migration.APPROVED_SEMANTIC_DELTAS == frozenset({"none", "D01", "D02"})

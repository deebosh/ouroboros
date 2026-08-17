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
    web_extractions = {f"web/modules/chat.js::{symbol}": f"web/modules/{owner}::{symbol}" for owner, symbols in {"chat_card_state.js": "liveLineRowToggleKey clearStickyCardState COLLAPSED_ACTIVITY_MAX boundActivityPreview projectCollapsedActivity isTerminalTaskPhase", "chat_controls.js": "shouldFirePanic confirmAndSendPanic", "chat_render_batch.js": "insertTimelineNode", "costs.js": "headerBudgetPresentation taskCostMeta taskCostProjection mergeStickyCostMeta", "utils.js": "rawTimestampEpoch"}.items() for symbol in symbols.split()}
    # createChatInstance closure helpers moved into per-instance factories (no facade: they were never exported)
    web_extractions.update({f"web/modules/chat.js::createChatInstance.{symbol}": f"web/modules/{owner}::{factory}.{symbol}" for owner, factory, symbols in (
        ("chat_timeline_anchor.js", "createTimelineAnchors", "NEAR_BOTTOM_THRESHOLD_PX isNearBottom captureVisibleTimelineAnchor restoreVisibleTimelineAnchor"),
        ("chat_message_identity.js", "createMessageIdentity", "buildMessageKey rememberMessageKey formatMsgTime stampNodeTimestamp getSenderLabel"),
        ("chat_document_bubble.js", "createDocumentBubbles", "buildDocumentBubble documentMessageKey appendDocumentBubble"),
        ("chat_subagent_routing.js", "createSubagentRouting", "setSubagentParent summarizeSubagentCardFrame updateSubagentCardFromEvent routeSubagentProgressToCard routeSubagentFinalMessageToCard routeSubagentTerminalToCard"),
    ) for symbol in symbols.split()})
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
        "events_schedule_task.py": "VALID_SUBAGENT_MEMORY_MODES _PARENT_CONTEXT_MARKER _PARENT_CONTEXT_END _extract_task_description_and_context _format_task_for_dedup _build_scheduled_task_payload _find_duplicate_task _cleanup_rejected_worktree _reject_schedule_task _reject_if_no_chat_target",
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
    existing_process_owner_rows.update(test_split_rows)
    registry_extraction_no_facade_rows.update(s2_panic_delta_rows)
    registry_extraction_no_facade_rows.update(set(test_split_rows) - test_split_facade_rows)
    registry_extraction_no_facade_rows.update(old for old in web_extractions if "::createChatInstance." in old)
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
    s7a_test_split_symbols_by_owner = {
        "tests/test_claudexor_owned_daemon.py": {"tests/test_claudexor_executor_frame.py": "_agent_with_metadata test_no_executor_fact_when_the_run_is_native_blocked_or_undecided test_resolved_harness_route_reaches_the_frame_assembler test_the_executor_fact_survives_history_replay_and_the_frozen_contract", "tests/test_claudexor_login_accounts.py": "test_a_vouched_login_survives_an_unreadable_manifest test_account_removal_is_the_engine_contract_and_refuses_out_loud test_login_capable_harness_ids_reads_the_manifest_auth_block test_login_endpoint_validates_before_any_daemon_work test_status_payload_filters_api_key_only_adapters test_zero_readable_manifests_is_a_read_failure_not_an_empty_filter", "tests/test_claudexor_login_jobs.py": "_INPUT_OP _create_login _input_request _invoke_login_job_handler _job_request test_control_problem_required_actions_are_top_level_and_bounded test_gateway_setup_job_operations_use_the_exact_daemon_routes test_login_create_fails_closed_when_the_catalog_cannot_be_read test_login_create_keeps_the_codex_invariant_on_both_engines test_login_create_transport_is_gated_by_the_executed_probe test_login_disclosure_capability_reads_the_operations_catalog test_login_input_409_conflicts_ride_through_typed test_login_input_endpoint_proxies_the_code_to_the_engine test_login_input_endpoint_validates_before_any_daemon_work test_login_input_engine_404_is_a_typed_capability_gap test_login_job_409_is_reconcile_scoped test_login_job_absence_statuses_pass_through test_login_job_success_envelopes_are_single_and_operation_specific test_login_reconcile_validates_job_id_before_daemon_work test_login_request_transport_default_is_capability_gated", "tests/test_claudexor_status_payload.py": "_reads_probe test_status_payload_calls_a_normalized_empty_envelope_a_failed_read test_status_payload_calls_half_an_account_envelope_a_failed_read test_status_payload_classifies_each_fanned_out_facet_independently test_status_payload_discloses_a_refused_per_harness_model_read test_status_payload_fans_out_the_independent_daemon_reads test_status_payload_keeps_typed_unreachable_when_a_fanned_out_read_refuses test_status_payload_marks_every_facet_unread_when_the_daemon_is_not_running test_status_payload_marks_facets_ok_when_the_daemon_answered test_status_payload_reads_block_matches_the_declared_gateway_contract test_wake_endpoint_discloses_a_typed_refusal_instead_of_a_generic_error test_wake_endpoint_starts_the_daemon_and_returns_the_fresh_reading"},
        "tests/test_context.py": {"ouroboros/context.py": "build_runtime_section build_user_content", "tests/_context_shared.py": "_make_health_env", "tests/test_context_advisory_review.py": "TestAdvisoryReviewStatusInContext", "tests/test_context_drive_state.py": "test_drive_state_section_is_typed_projection_with_pointer test_review_ledger_caps_runs_and_attempts_with_omission_notes test_settled_continuation_with_open_obligations_survives_age_retirement test_settled_continuations_retire_after_age_window", "tests/test_context_memory.py": "test_append_journal_milestone_bounds_over_limit_with_pointer test_installed_skills_section_includes_warnings_verdict test_low_mode_preserves_full_unconsolidated_dialogue_suffix test_low_mode_without_consolidation_keeps_max_raw_dialogue_tail test_project_workpad_and_journal_not_silently_sliced test_recent_chat_for_project_thread_shows_only_its_own_thread test_recent_chat_ignores_stale_consolidation_offset_after_rotation test_recent_chat_keeps_offset_when_same_log_gets_appended test_recent_chat_main_includes_all_threads_full_awareness test_recent_chat_offset_uses_filtered_dialogue_entries test_recent_chat_starts_after_consolidated_offset test_recent_sections_filter_process_logs_by_task_id test_retired_dialogue_summary_fallback_preserves_continuity_without_blocks test_retired_dialogue_summary_remains_visible_when_blocks_exist test_world_profile_is_loaded_with_stable_memory", "tests/test_context_runtime_section.py": "TestRuntimeEnvSection test_build_llm_messages_has_no_recorder_only_soft_cap_chain test_ephemeral_force_plan_is_routing_only_and_transfers_work test_force_plan_metadata_adds_structured_notice_without_rewriting_user_text test_runtime_section_exposes_host_routing_manifest_and_manual_contract test_runtime_section_external_workspace_includes_user_files_shell_affordance test_runtime_section_includes_filesystem_affordances_with_ctx test_runtime_section_includes_improvement_backlog_digest test_runtime_section_includes_light_runtime_mode_rule test_runtime_section_includes_non_workspace_memory_boundary test_runtime_section_omits_light_rule_for_advanced test_runtime_section_workspace_rule_preserves_system_review_commit_authority"},
        "tests/test_delegated_run_isolation.py": {"tests/_delegated_run_isolation_shared.py": "_HealthEnv _TerminalSweepGateway _binding_request_row _git _isolated_entry _nanny_ctx _seed_target", "tests/test_delegated_run_apply_intent.py": "TestAmbiguityAcknowledgment TestApplyIntentAmbiguity TestRootMutationAuthority", "tests/test_delegated_run_capture_honesty.py": "TestCaptureHonesty TestSplitDriveCaptureRead TestStartupGCFailClosed _failed_manifest_capture", "tests/test_delegated_run_reconciliation_capture.py": "TestLazyCaptureAtDisposition TestOrphanReconciliation _AbsentGateway"},
        "tests/test_delegated_subagent_transport.py": {"ouroboros/config.py": "CLAUDEXOR_DELEGATED_MARKER_MIN_VERSION", "ouroboros/provider_models.py": "MODEL_SETTING_KEYS", "ouroboros/tool_capabilities.py": "ACTING_SUBAGENT_TOOL_NAMES LOCAL_READONLY_SUBAGENT_TOOL_NAMES", "tests/_delegated_transport_shared.py": "_HealthStub _LiveRunStub _StreamingStub _delegating_ctx _dispatch _event_types _gateway _health_invariants _isolation_stub _nanny_ctx _owned_gateway_uses_each_test_transport _started_request _waiting _write_attempt _write_failed_attempt", "tests/test_delegated_cancellation_settlement.py": "test_a_retirement_that_landed_is_not_replayed_as_still_owned test_an_unverifiable_cancel_is_a_loud_durable_incident test_cancel_and_verify_carries_the_verify_reads_terminal_detail test_cancel_never_claims_more_than_a_terminal_receipt_proves test_cancelling_a_run_this_module_already_settled_is_not_an_incident test_settlement_claims_terminal_only_when_the_durable_facts_landed", "tests/test_delegated_executor_axis.py": "NANNY_TOOLS ROUTE test_a_blocked_pin_ends_the_task_unrun_instead_of_spending test_a_model_scoped_window_does_not_block_a_route_pinned_to_another_model test_a_plain_task_is_not_subject_to_the_executor_axis test_a_route_that_declares_only_the_confined_profile_is_admitted_not_refused test_a_spent_window_with_no_reset_instant_is_still_spent test_a_stale_unknown_executor_value_degrades_to_auto_not_to_a_crash test_an_acting_child_is_health_checked_against_the_profile_it_will_ask_for test_an_explicit_off_is_a_decision_an_empty_value_is_not test_an_unparseable_configured_route_is_disclosed_not_silent test_an_unreadable_profile_keeps_the_route_usable test_both_child_allowlists_can_see_the_nanny_verbs test_delegate_start_refuses_typed_when_no_route_is_configured test_dispatch_row_auto_with_a_healthy_route_becomes_a_nanny test_dispatch_row_auto_with_an_unavailable_route_runs_native_with_a_visible_marker test_dispatch_row_auto_with_every_profile_spent_falls_back_to_the_api test_dispatch_row_auto_without_a_route_runs_native test_dispatch_row_explicit_harness_blocks_and_never_reaches_the_native_path test_dispatch_row_native_is_native_and_asks_the_daemon_nothing test_executor_resolution_row_also_lands_in_canonical_events test_get_subagent_harness_reads_the_env_key test_one_exhausted_credential_profile_does_not_take_the_harness_offline test_route_parsing_is_opaque test_rule_auto_with_every_profile_spent_falls_back_to_the_api_loudly test_rule_auto_with_healthy_harness_delegates test_rule_auto_with_unavailable_harness_falls_native_with_a_visible_marker test_rule_auto_without_harness_runs_native test_rule_explicit_harness_blocks_instead_of_spending_api_money test_rule_native_is_native_whatever_the_state test_subagent_harness_key_stays_out_of_the_model_key_sweep test_subscription_window_exhausted_beacon_wakes_the_waiting_parent test_there_is_no_hurry_verb test_unknown_executor_is_rejected", "tests/test_delegated_reconciliation.py": "test_a_breach_whose_cancel_was_never_verified_is_not_reported_as_cancelled test_a_terminalizing_parent_releases_the_run_it_still_holds test_an_orphaned_delegated_run_is_reconciled_when_its_owner_is_gone test_both_custody_surfaces_see_the_same_live_task_set test_reconciliation_default_transport_is_the_ensured_owned_daemon test_the_loops_own_release_point_reaches_the_delegated_reconciler test_the_startup_sweep_reconciles_delegated_runs_too test_what_the_daemon_says_is_absent_is_closed_not_faulted_forever", "tests/test_delegated_result_delivery.py": "_read_artifact_whole test_a_large_delegated_result_is_delivered_whole_or_declared_partial test_a_line_the_delivery_layer_cut_is_not_covered test_a_reconciled_run_with_an_unread_artifact_is_visible_as_uncollected test_a_restaged_different_artifact_does_not_inherit_the_old_acknowledgement test_a_truncated_primary_output_is_resolved_from_the_artifact_route test_an_unread_result_is_a_loud_durable_fact_at_settlement test_an_unresolvable_truncated_output_is_disclosed_and_never_acknowledged test_no_post_fires_when_the_start_request_row_did_not_land test_reading_the_staged_artifact_whole_writes_the_canonical_acknowledgement test_the_coverage_ack_binds_to_what_delivery_actually_hands_the_model test_the_progress_payload_survives_a_verbose_harness_too test_the_staged_artifact_is_the_bytes_it_declares_even_under_a_translating_text_layer", "tests/test_delegated_run_accounting.py": "_plain_ctx _settled_run _waited_run test_a_202_handle_without_a_run_id_is_a_live_run_not_a_failure test_a_failed_ledger_write_leaves_the_session_retryable test_a_session_is_not_counted_as_a_physical_provider_call test_a_subscription_session_settles_at_zero_and_keeps_the_projection_final test_an_estimated_spend_is_not_a_settled_one test_an_unreported_token_count_is_unknown_not_zero test_d29_absent_authroute_records_empty_never_invented test_d29_applied_credential_profile_reaches_the_durable_record test_no_receipt_on_a_failed_settlement_and_one_after_the_successful_retry test_settlement_reads_the_harnesss_own_spend_field test_the_agent_facing_cost_tells_the_same_story_as_the_ledger test_the_durable_access_profile_is_the_receipt_never_our_own_request test_the_last_delegation_projection_is_written_at_the_settle_seam test_the_settled_envelope_tells_the_same_story_as_the_row test_the_start_request_asks_for_the_substrate_it_claims test_the_terminal_payload_carries_the_applied_model_and_the_mismatch_delta test_the_unmetered_external_row_would_have_dropped_cost_final", "tests/test_delegated_run_containment.py": "test_a_mutating_run_asks_for_a_scoped_home_and_a_read_only_one_does_not test_a_run_with_no_os_boundary_is_disclosed_in_three_places_and_still_allowed test_absence_of_the_artifact_is_no_evidence_and_a_read_only_run_is_never_faulted test_an_attempt_that_recorded_no_home_fact_is_not_a_containment_fault test_an_engine_without_the_marker_refuses_the_mutating_lane_and_keeps_the_read_only_one test_asking_for_a_scoped_home_is_not_evidence_that_one_was_applied test_linux_shaped_run_is_disclosed_unconfined_with_the_engines_reason_not_cancelled test_the_child_is_told_its_boundary_is_a_request_and_not_a_fact test_the_dispatcher_refuses_the_same_engine_the_nanny_would test_the_relayed_result_never_claims_an_isolation_no_artifact_proves test_the_two_floors_sit_at_the_measured_bands", "tests/test_delegated_run_custody.py": "test_a_failed_start_does_not_leave_the_registration_it_created test_a_queued_handle_with_no_run_id_names_its_registration_like_its_twin test_a_retry_testifies_about_the_stored_invocation_not_the_current_config test_a_start_whose_custody_row_did_not_land_does_not_claim_to_be_custodied test_an_absent_run_closes_only_after_its_registration_is_discharged test_an_unresolved_containment_fault_cannot_age_out_of_the_health_view test_custody_rows_outlive_the_child_drive_they_were_written_from test_custody_survives_the_worker_that_started_the_run test_delegated_spend_settles_into_the_canonical_budget_ledger test_durable_truncation_is_disclosed_never_a_bare_slice test_every_pre_custody_exit_names_the_registration_it_created test_reconciliation_recovers_a_pending_invocation_whose_worker_died test_shared_project_retirement_defers_quietly_for_non_canonical_sharers test_the_invocation_id_is_reused_on_retry_and_fresh_per_intended_start", "tests/test_delegated_run_profile.py": "test_a_delegated_run_can_only_be_touched_by_the_task_that_started_it test_a_mutating_child_runs_live_in_a_private_snapshot_not_the_shared_tree test_a_mutating_run_is_refused_when_the_root_and_the_granted_write_root_disagree test_a_mutating_run_requires_an_ACTIVE_workspace_not_merely_agreement test_a_read_only_child_uses_the_same_transport_with_a_narrower_profile test_a_read_only_task_cannot_obtain_workspace_write test_a_succeeded_run_that_never_proved_its_profile_says_so_in_its_result test_a_widened_run_is_cancelled_and_typed_not_reported_as_progress test_an_inactive_workspace_is_refused_even_when_the_root_is_set test_an_undisclosed_effective_profile_is_unverified_not_compliant test_an_unresolvable_write_root_is_a_typed_refusal_not_a_traceback test_effective_access_is_verified_not_assumed test_the_guards_that_protect_a_delegated_run_fail_closed test_the_host_states_its_prohibitions_on_every_delegated_run test_the_model_has_no_argument_that_could_widen_the_profile", "tests/test_delegated_wait_timeline.py": "_timeline test_a_batch_bigger_than_the_display_tail_says_how_much_it_is_not_showing test_a_bounded_rolling_timeline_records_the_whole_batch_that_arrived test_a_daemon_that_adds_more_rows_than_cursor_steps_loses_none_of_them test_a_growing_timeline_still_records_exactly_the_rows_that_are_new test_a_long_busy_windows_advance_list_is_measured_not_estimated test_a_verbose_timeline_tail_cannot_push_the_whole_payload_over_the_limit test_label_shedding_is_disclosed_on_the_row_that_gave_them_up test_the_advance_list_is_a_list_not_a_count test_the_advance_list_yields_entirely_rather_than_pushing_the_payload_over test_the_standing_tail_is_adopted_as_history_only_for_a_caught_up_caller", "tests/test_delegated_wait_window.py": "_BoundRecordingStub _DiesAfter _FinishesOnTheSecondPoll _SlowPollStub _wait_against_a_live_run _wait_against_a_streaming_run test_a_containment_breach_still_halts_mid_window test_a_daemon_that_dies_mid_window_is_refused_not_reported_as_a_quiet_wait test_a_daemon_that_dies_only_at_the_spent_window_poll_still_expires_gracefully test_a_progress_emit_failure_never_aborts_the_wait test_a_run_that_finishes_during_the_last_sleep_is_reported_terminal test_a_streaming_run_no_longer_wakes_the_model_per_event_batch test_a_terminal_state_still_returns_immediately_mid_window test_a_wait_with_no_deadline_keeps_the_full_window_it_asked_for test_bounded_poll_retries_the_git_atomic_object_race_once test_every_poll_is_bounded_by_what_the_window_has_left test_every_read_the_wait_issues_carries_a_bound_and_the_window_is_it test_the_clamp_keys_on_whether_a_deadline_exists_not_on_int_of_what_is_left test_the_configured_wait_ceiling_cannot_promise_more_than_the_tool_can_serve test_the_human_keeps_the_live_stream_while_the_model_waits test_the_last_poll_of_a_spent_window_is_bounded_not_skipped test_the_wait_adopts_the_standing_tail_before_it_starts_watching test_the_wait_leaves_the_grace_it_needs_to_answer_at_all test_the_wait_window_never_outlives_the_nannys_own_deadline test_wait_payload_carries_elapsed_and_cap_facts test_wait_payload_facts_stay_null_for_a_row_that_predates_them"},
        "tests/test_delivery_forced_finalization.py": {"tests/_delivery_forced_shared.py": "_bind_host_pass _forced_test_context", "tests/test_delivery_control_latch.py": "_arm_latch_with_candidate test_children_unabsorbed_forced_path_never_leaks_protocol_json test_forced_finalization_degrades_broken_json_looking_text_to_retained_candidate test_forced_finalization_degrades_malformed_control_to_retained_candidate test_forced_finalization_degrades_unknown_verb_control_to_retained_candidate test_forced_finalization_keeps_armed_prose_as_the_answer test_forced_finalization_passes_broken_json_through_when_latch_not_armed test_forced_finalization_passes_json_through_when_latch_not_armed test_forced_finalization_resolves_armed_keep_to_retained_candidate test_forced_round_limit_resolves_armed_replace_control test_nonforced_resolver_treats_unknown_verb_object_as_protocol_not_prose", "tests/test_delivery_forced_absorption_acceptance.py": "_acceptance_panel_result _forced_absorption_acceptance_context test_claimed_child_dispositions_reads_the_blackboard test_forced_children_unabsorbed_rail_runs_acceptance_with_debt_evidence test_forced_rail_keeps_bypass_verdict_when_subtree_is_not_quiescent test_forced_rail_terminalizes_a_requested_improvement_pass test_orphan_label_keeps_cancelled_lifecycle_and_terminal_result test_orphan_note_names_claimed_but_failed_disposition", "tests/test_delivery_forced_acceptance_bypass.py": "test_budget_fence_no_spend_path_stamps_typed_acceptance_bypass test_forced_bypass_never_overwrites_an_existing_host_decision test_forced_bypass_probe_failure_records_unknown_eligibility test_forced_bypass_records_not_eligible_for_child_tasks test_forced_bypass_stamps_over_deferred_agent_stance test_round_limit_stamps_typed_acceptance_bypass", "tests/test_delivery_forced_owner_refresh.py": "test_child_result_change_after_host_panel_requires_replacement_and_fresh_panel test_child_result_change_during_host_panel_supersedes_pass test_forced_owner_arrival_gets_one_complete_refresh test_forced_replacement_supersedes_accepted_pass_in_outcome_and_projection test_second_forced_owner_arrival_returns_exact_resume_fallback test_stale_preserve_supersedes_accepted_pass_in_outcome_and_projection", "tests/test_delivery_forced_suffix_binding.py": "test_forced_finalization_stops_services_before_model_and_binds_evidence test_forced_model_call_rebinds_latest_child_result_and_suffix test_forced_retained_candidate_suffix_creates_new_unaccepted_revision test_normal_host_suffix_is_inside_candidate_and_panel_subject test_production_budget_wrapup_propagates_budget_exceeded test_production_budget_wrapup_routes_through_delivery_candidate"},
        "tests/test_extensions_api.py": {"tests/_extensions_api_shared.py": "_clean_extensions _make_client _stop_patches _write_ext", "tests/_shared.py": "clean_extension_runtime_state", "tests/test_extensions_dispatcher.py": "test_api_extension_dispatcher_404_for_unknown_route test_api_extension_dispatcher_allows_head_for_get_route test_api_extension_dispatcher_rejects_not_live_route test_api_extension_dispatcher_reloads_stale_live_route test_api_extension_dispatcher_routes_to_registered_handler test_api_extension_dispatcher_surfaces_lazy_load_error test_api_extension_module_rejects_non_live_extension test_api_extension_module_serves_only_live_declared_entry test_api_extension_settings_section_returns_only_requested_skill", "tests/test_extensions_skill_grants.py": "test_api_skill_grants_rejects_blocking_blocker_review test_api_skill_grants_saves_keys_and_permissions test_api_skill_grants_soft_fails_extension_reconcile_after_persist test_api_skill_reconcile_clears_cached_load_error test_api_skill_reconcile_rejects_missing_skill_name test_api_skill_review_offloads_to_thread_and_returns_outcome test_lifecycle_queue_endpoint_marks_stale_review_job_interrupted", "tests/test_extensions_skill_lifecycle.py": "client_env test_api_projects_conflict_and_refuses_enable_until_peer_is_disabled test_api_skill_delete_accepts_unsanitized_external_directory_leaf test_api_skill_delete_rejects_external_symlink_bucket test_api_skill_delete_rejects_name_collision_before_state_delete test_api_skill_delete_removes_external_payload_state_and_unloads test_api_skill_toggle_allows_warnings_review test_api_skill_toggle_allows_warnings_under_blocking test_api_skill_toggle_blocks_missing_isolated_deps_env test_api_skill_toggle_collision_disable_does_not_write_shared_state test_api_skill_toggle_enables_and_loads_extension test_api_skill_toggle_rejects_non_boolean_enabled", "tests/test_extensions_websocket.py": "test_tool_registry_execute_dispatches_ext_tool test_ws_endpoint_dispatches_ext_prefixed_messages test_ws_endpoint_dispatches_first_message_after_lazy_load test_ws_endpoint_reconciles_and_unloads_not_live_extension test_ws_endpoint_surfaces_extension_load_error"},
        "tests/test_promote_chat_flow.py": {"tests/_promote_chat_shared.py": "_isolated_projects_root", "tests/test_chat_steering.py": "test_busy_direct_main_root_is_manifested_and_steerable_without_promotion test_chat_running_tasks_lists_same_chat_pooled_only test_decision_turn_metadata_injects_running_tasks_and_client_id test_direct_turn_closed_admission_returns_manual_target test_handle_steer_task_delivers_once_to_running_task test_handle_steer_task_stale_target_notifies_visibly test_main_steer_can_address_project_bound_root_from_host_manifest test_steer_task_tool_emits_event_with_target_and_client_id test_steer_task_tool_requires_args", "tests/test_project_chat_routing.py": "test_busy_project_chat_routes_to_ephemeral_decision_turn test_chat_history_tool_spans_all_threads_full_awareness test_direct_chat_project_thread_skips_letters_home test_promote_chat_to_task_broadcasts_projects_changed test_recent_context_full_awareness_and_project_focus_with_bindings test_registered_project_chat_ids_recognizes_every_project test_restart_drain_defers_then_completes_without_sleeping test_restart_drain_no_live_tasks_restarts_immediately test_restart_drain_uses_generic_queue_heartbeat_not_retired_planning_knob test_route_project_chat_1to1_delivery_is_idempotent test_route_project_chat_defers_when_multiple_running_tasks test_route_project_chat_does_not_confirm_failed_mailbox_write test_route_project_chat_ignores_non_registered_chat_ids", "tests/test_project_task_binding.py": "test_all_task_project_bindings_exposes_project_id test_bound_project_history_backfills_task_progress test_bound_task_heartbeat_routes_to_project_panel test_bound_task_media_routes_to_project_panel test_bound_task_send_message_routes_future_events_to_project test_chat_history_filters_by_thread test_journal_write_rejects_over_limit_instead_of_truncating test_project_from_task_auto_names_from_live_queue_snapshot test_project_from_task_auto_names_from_objective test_project_from_task_endpoint_creates_binding test_project_from_task_names_skill_lifecycle_task test_project_from_task_uses_neutral_name_when_nothing_derivable test_project_from_task_uses_objective_hint_for_in_progress_direct_chat test_project_media_and_typing_broadcasts_carry_chat_id", "tests/test_promote_workspace_provisioning.py": "_promote_ctx pathlib_resolve test_promote_broken_working_dir_loud_fails_never_blind_ensures test_promote_fileless_project_autoprovisions_and_binds_workspace test_promote_provisioning_failure_loud_fails_not_silent_fileless test_promote_workspace_none_still_opts_out_of_autoprovision"},
        "tests/test_runtime_mode_core.py": {"ouroboros/onboarding_wizard.py": "build_onboarding_html", "ouroboros/runtime_mode_policy.py": "protected_path_category", "ouroboros/tools/registry.py": "ToolRegistry", "tests/_runtime_mode_core_shared.py": "_git_repo _make_skill_payload _registry", "tests/test_runtime_mode_registry_gating.py": "_CommitCtx test_advanced_commit_blocks_protected_staged_paths test_advanced_commit_blocks_rename_from_protected_path test_advanced_mode_allows_non_critical_write_calls_through test_advanced_mode_blocks_protected_write test_dot_github_workflow_is_release_invariant test_light_mode_blocks_repo_mutation_tools test_light_mode_does_not_block_skill_exec_at_registry_layer test_light_mode_redirects_absolute_home_path_to_user_files test_light_mode_redirects_cognitive_memory_write test_light_mode_redirects_windows_style_cognitive_path test_light_mode_still_allows_read_only_tools test_pro_commit_uses_normal_review_for_protected_paths test_pro_mode_allows_protected_write_with_core_patch_notice test_pro_mode_edit_text_emits_core_patch_notice test_restore_to_head_blocks_protected_rename_source test_restore_to_head_blocks_release_invariant_path test_revert_commit_blocks_protected_contract_path", "tests/test_runtime_mode_repair_confinement.py": "_ctx_with_skill_repair test_cross_skill_redirect_error_unit test_data_settings_case_variant_wins_over_stale_bucket_skill_name test_data_settings_path_wins_over_stale_bucket_skill_name test_explicit_data_skills_path_wins_over_stale_bucket_skill_name test_repair_mode_blocks_cross_skill_redirect_via_bucket_skill_name test_repair_mode_matching_bucket_skill_name_is_silently_redundant test_repo_path_wins_over_stale_bucket_skill_name test_short_form_requires_existing_payload_root test_synthesize_payload_constraint_unit", "tests/test_runtime_mode_shell_gating.py": "_outside_runtime_registry test_advanced_mode_blocks_python_os_remove_protected_path test_advanced_mode_blocks_runshell_protected_backslash_path test_advanced_mode_blocks_runshell_protected_python_writer test_advanced_mode_does_not_run_light_tripwire test_default_lane_allows_minusC_retarget_from_default_cwd test_default_lane_allows_mutating_git_outside_runtime test_default_lane_allows_readonly_git_at_runtime_cwd test_default_lane_blocks_mutating_git_targeting_runtime test_light_mode_allows_extension_tool_dispatch test_light_mode_allows_non_repo_shell_file_operations test_light_mode_allows_readonly_runshell test_light_mode_allows_shell_wrapper_non_repo_writer test_light_mode_blocks_inplace_mutation_tools test_light_mode_blocks_pr_integration_tools test_light_mode_blocks_runshell_mutation test_light_mode_blocks_simple_shell_c_repo_writer test_light_mode_inline_writer_is_refused_upfront test_light_mode_tripwire_catches_python_repo_writer test_light_mode_tripwire_catches_untracked_repo_file test_light_mode_tripwire_runs_after_failed_command test_light_mode_workspace_artifact_does_not_trip_self_repo_snapshot test_run_shell_allows_readonly_mentions_of_protected_paths test_run_shell_blocks_env_wrapped_git_mutation test_run_shell_blocks_shell_wrapped_git_mutation test_run_shell_blocks_sort_uniq_protected_output_paths", "tests/test_runtime_mode_skill_payload.py": "test_b2_external_workspace_stray_bucket_is_ignored_not_blocked test_light_bucket_native_rejected_at_gate test_light_control_plane_sidecar_still_blocked_with_bucket_skill_name test_light_data_write_with_bucket_skill_name_resolves_under_payload test_light_mode_blocked_message_lists_three_paths test_light_partial_args_surface_specific_error_not_generic_light_block test_light_str_replace_editor_with_bucket_skill_name_allowed test_light_write_file_with_skill_payload_root_allowed", "tests/test_runtime_mode_surfaces.py": "REPO test_api_settings_post_clamps_unknown_runtime_mode test_api_settings_post_silently_drops_runtime_mode_changes test_api_state_declares_phase2_keys test_chat_context_mode_toggle_reports_owner_endpoint_errors test_onboarding_css_has_three_column_variant test_onboarding_js_exposes_skills_repo_path_input_and_binding test_onboarding_js_has_runtime_mode_selector_and_save_payload test_phase4_ui_copy_matches_shipped_runtime test_settings_js_reads_and_writes_phase2_keys test_settings_ui_renders_runtime_mode_and_skills_path test_skills_ui_reads_live_extension_state_fields test_state_response_typeddict_declares_phase2_keys"},
        "tests/test_runtime_mode_elevation.py": {"tests/_runtime_mode_elevation_shared.py": "_make_drive_ctx _seed_disk isolated_settings", "tests/test_runtime_mode_authorship.py": "_own_ratchet_env test_agent_save_cannot_end_a_forwarded_mode_mid_run test_env_boot_baseline_may_tighten_the_elevation_floor_but_never_raise_it test_env_declared_context_mode_cannot_author_a_lowering test_env_declared_safety_mode_cannot_author_a_lowering test_env_forwarded_modes_survive_the_documented_startup_path test_every_settings_writer_routes_through_the_shared_prologue test_generic_settings_post_does_not_author_a_mode_decision test_merge_settings_payload_preserves_other_keys test_owner_endpoint_authors_its_own_key_even_at_the_default test_private_owner_write_settings_keeps_context_lowering_guard", "tests/test_runtime_mode_data_write.py": "test_data_read_allows_skill_review_json test_data_read_cognitive_bad_line_args_are_tolerant test_data_read_does_not_slice_memory_by_default test_data_read_supports_line_ranges test_data_write_allows_other_data_files test_data_write_blocks_self_authored_state_marker test_data_write_blocks_serialized_content_object test_data_write_blocks_settings_case_variants test_data_write_blocks_settings_json test_data_write_blocks_settings_via_env_override test_data_write_blocks_settings_via_symlink test_data_write_blocks_skill_grants_case_variants test_data_write_blocks_skill_grants_json test_data_write_blocks_skill_trust_state_json test_data_write_blocks_skill_trust_state_under_symlinked_skill_dir test_data_write_blocks_unseeded_native_payload test_data_write_marks_new_external_skill_self_authored test_malformed_self_authored_marker_is_not_trusted test_str_replace_blocks_self_authored_marker", "tests/test_runtime_mode_launcher_bridges.py": "test_launcher_auto_grant_bridge_disables_truthy_alias test_launcher_auto_grant_bridge_saves_after_confirmation test_launcher_runtime_mode_bridge_can_cancel_pending_mode_without_restart test_launcher_runtime_mode_bridge_reports_pending_restart_against_active test_launcher_runtime_mode_bridge_saves_after_confirmation test_launcher_skill_grant_supports_permission_grants test_launcher_skill_key_grant_handles_reconcile_http_error test_launcher_skill_key_grant_rejects_instruction_skill test_launcher_skill_key_grant_supports_extensions test_launcher_skill_key_grant_validates_review_and_manifest", "tests/test_runtime_mode_owner_endpoints.py": "test_generic_settings_save_preserves_pending_runtime_mode_without_hot_apply test_merge_settings_payload_skips_auto_grant_reviewed_skills test_merge_settings_payload_skips_context_mode test_merge_settings_payload_skips_runtime_mode test_owner_auto_grant_endpoint_persists_outside_generic_settings test_owner_context_mode_endpoint_persists_and_hot_applies test_owner_context_mode_endpoint_refuses_lowering_while_task_runs test_owner_context_mode_idle_predicate_covers_pending_and_direct_chat_busy test_owner_runtime_mode_endpoint_persists_next_boot_without_env_elevation test_owner_runtime_mode_endpoint_reports_no_restart_when_mode_unchanged test_owner_runtime_mode_endpoint_reports_restart_until_pending_mode_is_active test_save_settings_refuses_context_mode_lowering_without_owner_flag test_settings_save_warns_when_an_agent_task_is_running test_started_predicate_is_read_only_and_never_constructs_the_agent", "tests/test_runtime_mode_write_guards.py": "_clear_safety_provider_env test_browser_evaluate_context_mode_self_lowering_guard test_context_mode_guard_does_not_block_readonly_diagnostics test_context_mode_self_lowering_indicators_block_attack_patterns test_elevation_indicators_block_attack_patterns_in_all_modes test_elevation_indicators_do_not_false_positive test_files_api_owner_only_helper_blocks_skill_state_case_variants test_files_api_owner_only_helper_blocks_symlinked_skill_state_dir test_files_api_write_blocks_settings_json test_run_shell_blocks_delayed_skill_owner_state_writer test_run_shell_blocks_detached_skill_state_command test_run_shell_blocks_obfuscated_skill_owner_state_write test_run_shell_scans_scripts_relative_to_cwd test_workspace_mode_still_blocks_runtime_mode_elevation"},
        "tests/test_workspace_executor.py": {"ouroboros/workspace_executor.py": "execute", "tests/_workspace_executor_shared.py": "_init_repo", "tests/test_workspace_executor_admission.py": "test_api_task_metadata_accepts_normalized_executor_ref test_api_task_rejects_empty_executor_ref test_api_task_rejects_executor_ref_mapping_to_data_drive test_api_task_rejects_executor_ref_mapping_to_system_repo test_api_task_rejects_executor_ref_not_covering_workspace test_api_task_rejects_executor_ref_without_external_workspace test_api_task_rejects_local_network_none test_api_task_rejects_malformed_executor_mapping_entry test_api_task_rejects_malformed_executor_ref test_api_task_rejects_reserved_executor_metadata_aliases test_api_task_rejects_reserved_executor_metadata_ref", "tests/test_workspace_executor_docker.py": "test_docker_executor_accepts_backend_absolute_write_targets_and_outputs test_docker_executor_enforces_network_none_before_exec test_docker_executor_rejects_network_none_when_container_has_network test_docker_executor_run_script_uses_backend_script_path test_docker_executor_service_shell_uses_process_group_stop test_docker_executor_stop_failure_preserves_service_handle test_docker_executor_timeout_cleans_backend_process", "tests/test_workspace_executor_services.py": "test_executor_cleanup_scans_child_drive_records_from_parent_data_root test_executor_keep_alive_service_survives_task_teardown test_executor_local_service_can_restart_after_exit test_executor_local_service_lifecycle_hides_private_snapshot test_executor_local_service_sanitizes_env_and_redacts_logs test_executor_panic_cleanup_kills_durable_foreground_and_service_processes test_executor_service_status_and_durable_record_redact_secret_like_args test_executor_services_participate_in_task_and_global_cleanup test_start_service_with_executor_ref_uses_local_for_unmapped_task_drive_cwd"},
    }
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
        ("llm_attempt.py", "_PayloadCachePolicyMixin"): "_normalize_payload_cache_ttl _payload_cache_breakpoints _pop_cache_breakpoint_disclosure",
        ("llm_capability_policy.py", "_CapabilityPolicyMixin"): "_apply_rejected_param_cache _clamp_effort_for_model _effort_ceiling_for _effort_floor_for _fetch_openrouter_capabilities _get_supported_parameters _known_rejected_params _mandatory_value_rejection _parameter_rejection_error _payload_effort _pop_effort_clamp_disclosure _record_effort_ceiling _record_effort_floor _remember_rejected_params _retry_without_optional_sampling _set_payload_effort clamp_effort_for_route metadata_fetch_attempted_and_failed openrouter_context_length",
        ("llm_routing.py", "_ProviderRoutingMixin"): "_explicit_cache_affinity_identity _get_async_remote_client _get_client _get_local_client _get_remote_client _make_no_proxy_async_client _make_no_proxy_client _no_proxy_timeout _openrouter_session_identity _parse_provider_model _prompt_cache_identity _qualified_model_name _resolve_remote_target probe_oversized_context",
        ("llm_messages.py", "_MessageShapingMixin"): "_content_with_system_notice_marker _copy_messages_with_cache_policy _has_openrouter_reasoning_details _has_replayed_reasoning_metadata _is_deferrable_image_user_turn _model_family _normalize_system_message_placement _replace_image_blocks_with_placeholder _strip_openrouter_roundtrip_metadata sanitize_reasoning_on_model_switch",
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
                else "D08"
                if row["old path/symbol"] in s6_delta_rows
                else "D02"
                if row["old path/symbol"] in {
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
    assert v7_migration.APPROVED_SEMANTIC_DELTAS == frozenset({"none", "D01", "D02", "D03", "D04", "D05", "D06", "D07", "D08", "D09", "D11"})

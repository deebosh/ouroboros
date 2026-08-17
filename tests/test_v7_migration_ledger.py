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
    }
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
    }
    implemented.update({name: name for name in s3_semantic_delta_ids})
    existing_process_owner_rows.update(s3_semantic_delta_ids)
    registry_extraction_no_facade_rows.update(s3_semantic_delta_ids)
    implemented.update(w_stream_rows)
    implemented.update(shell_extraction_rows)
    implemented.update(headless_extraction_rows)
    implemented.update(tool_access_extraction_rows)
    implemented.update(test_split_rows)
    implemented.update(web_extractions)
    implemented.update(config_extraction_rows)
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
    }
    implemented.update(settings_seam_rows)
    existing_process_owner_rows.update(settings_seam_rows)
    existing_process_owner_rows.update(test_split_rows)
    registry_extraction_no_facade_rows.update(set(test_split_rows) - test_split_facade_rows)
    registry_extraction_no_facade_rows.update(old for old in web_extractions if "::createChatInstance." in old)
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
            expected_delta = (
                # spec 4.3.6: the settings vocabulary moved as-is, then the three no-op
                # knobs were retired from it (an approved observable delta).
                "D04"
                if row["old path/symbol"] in {
                    "ouroboros/config.py::SETTINGS_DEFAULTS",
                    "ouroboros/config.py::RETIRED_SETTING_KEYS",
                }
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
                "D02"
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
    assert v7_migration.APPROVED_SEMANTIC_DELTAS == frozenset({"none", "D01", "D02", "D03", "D04", "D06"})

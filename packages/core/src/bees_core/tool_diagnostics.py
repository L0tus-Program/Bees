"""Códigos de domínio públicos; nunca publicar texto livre do journal ou do executor."""

from bees_core.executor_transport.contracts import ERROR_CODES

PUBLIC_TOOL_ERRORS = (
    ERROR_CODES
    | frozenset(
        {
            "mcp_unreachable",
            "mcp_outcome_unknown",
            "mcp_unknown_tool",
            "mcp_invalid_arguments",
            "environment_unavailable",
            "human_control",
            "resource_busy",
            "scope_denied",
            "no_roots",
            "pdf_reader_unavailable",
            "document_unreadable",
            "content_too_large",
            "cannot_delete_root",
            "directory_not_empty",
            "not_a_file",
            "file_too_large",
            "media_type_unsupported",
            "office_unavailable",
            "format_unsupported",
            "conversion_failed",
        }
    )
    | frozenset(
        "web_" + code
        for code in {
            "dns_failed",
            "private_address_blocked",
            "unsupported_url",
            "credentials_in_url",
            "port_blocked",
            "timeout",
            "connection_failed",
            "redirect_invalid",
            "response_too_large",
            "too_many_redirects",
            "pdf_unreadable",
            "unsupported_content_type",
            "search_failed",
        }
    )
    | frozenset(
        {
            "tool_result_unknown",
            "tool_interrupted_unknown",
            "tool_outcome_unknown",
            "tool_failed",
            "tool_unavailable",
            "invalid_tool_arguments",
            "tool_result_too_large",
            "worker_restart",
            "executor_error",
            "executor_offline",
            "executor_revoked",
            "executor_deadline",
            "executor_publication_fenced",
            "executor_result_unknown",
            "grant_changed",
            "unsupported_url",
            "navigation_failed",
            "element_not_found",
            "browser_unavailable",
            "computer_unavailable",
            "execution_unavailable",
            "path_outside_roots",
            "path_invalid",
            "not_found",
            "not_a_directory",
            "is_a_directory",
            "sensitive_field",
            "artifact_storage_unavailable",
            "artifact_media_type_unsupported",
            "artifact_too_large",
            "artifact_content_invalid",
            "artifact_integrity_failed",
            "artifact_conflict",
            "search_failed",
            "dns_failed",
            "connection_failed",
            "private_address_blocked",
            "port_blocked",
            "redirect_invalid",
            "response_too_large",
            "timeout",
            "mcp_auth_failed",
            "mcp_protocol_error",
            "mcp_server_unavailable",
            "mcp_tool_error",
            "policy_denied",
            "approval_denied",
            "denied_by_user",
            "bee_not_found",
            "delegation_cycle",
            "delegation_depth",
            "delegation_limit",
            "delegation_failed",
        }
    )
)


def public_tool_code(value: object) -> str | None:
    return value if isinstance(value, str) and value in PUBLIC_TOOL_ERRORS else None

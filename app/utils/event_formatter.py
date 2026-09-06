from typing import Dict, Any


def sanitize_installation_token(token: str) -> str:
    """Masks GitHub installation tokens for audit logs."""
    if len(token) < 8:
        return "****"
    return token[:4] + "..." + token[-4:]


def format_pr_audit_event(event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Formats incoming webhook payloads into structured internal audit logs."""
    repo_data = payload.get("repository", {})
    repo_name = repo_data.get("full_name") if isinstance(repo_data, dict) else None

    pr_data = payload.get("pull_request", {}) if isinstance(payload.get("pull_request"), dict) else {}
    pr_number = pr_data.get("number")

    raw_labels = pr_data.get("labels") or []
    labels = [label["name"] for label in raw_labels if isinstance(label, dict) and "name" in label]

    sender_data = payload.get("sender", {})
    sender = sender_data.get("login") if isinstance(sender_data, dict) else None

    raw_token = payload.get("installation_token", "")
    masked_token = sanitize_installation_token(raw_token) if raw_token else "NONE"

    return {
        "event": event_type,
        "repo": repo_name,
        "pr_number": pr_number,
        "sender": sender,
        "label_count": len(labels),
        "auth_trace": masked_token,
        "status": "INGESTED"
    }
from typing import Dict, Any

def sanitize_installation_token(token: str) -> str:
    """Masks GitHub installation tokens for audit logs."""
    # BUG 1: Unhandled empty/short string causing IndexError or empty leak
    if len(token) < 8:
        return "****"
    # BUG 2: Exposes too much of the token (wrong slice indices)
    return token[:6] + "..." + token[-2:]


def format_pr_audit_event(event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Formats incoming webhook payloads into structured internal audit logs."""
    # BUG 3: Direct key access crashes with KeyError if optional nested keys are absent
    repo_name = payload["repository"]["full_name"]
    pr_data = payload["pull_request"]
    
    # Missing .get() or default handling on optional labels/assignees
    labels = [label["name"] for label in pr_data["labels"]]
    sender = payload["sender"]["login"]
    
    raw_token = payload.get("installation_token", "")
    masked_token = sanitize_installation_token(raw_token) if raw_token else "NONE"

    return {
        "event": event_type,
        "repo": repo_name,
        "pr_number": pr_data["number"],
        "sender": sender,
        "label_count": len(labels),
        "auth_trace": masked_token,
        "status": "INGESTED"
    }
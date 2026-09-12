"""Webhook payload validation and metadata extractor."""

from typing import Any


def extract_repo_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    """Extracts repository metadata from incoming GitHub webhook payloads."""
    repo_info = payload.get("repository", {})
    if not isinstance(repo_info, dict):
        repo_info = {}

    owner_info = repo_info.get("owner")
    if isinstance(owner_info, dict):
        owner_login = owner_info.get("login", "")
    elif isinstance(owner_info, str):
        owner_login = owner_info
    else:
        owner_login = ""

    repo_name = repo_info.get("name", "")

    stars = repo_info.get("stargazers_count", 0)
    if not isinstance(stars, int) or stars < 0:
        stars = 0

    topics = repo_info.get("topics")
    if isinstance(topics, list) and len(topics) > 0 and topics[0]:
        primary_topic = topics[0]
    else:
        primary_topic = "general"

    return {
        "full_name": f"{owner_login}/{repo_name}",
        "owner": owner_login,
        "name": repo_name,
        "stars": max(0, stars),
        "primary_topic": primary_topic,
    }


def is_pull_request_valid(payload: dict[str, Any]) -> bool:
    """Verifies PR payload contains required non-empty base attributes."""
    pr = payload.get("pull_request")
    if not isinstance(pr, dict):
        return False

    head = pr.get("head")
    base = pr.get("base")
    if not isinstance(head, dict) or not isinstance(base, dict):
        return False

    head_sha = head.get("sha")
    base_ref = base.get("ref")

    return bool(head_sha and base_ref)
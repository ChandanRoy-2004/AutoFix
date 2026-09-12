"""Webhook payload validation and metadata extractor."""
from typing import Dict, Any

def extract_repo_metadata(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extracts repository metadata from incoming GitHub webhook payloads.
    """
    # BUG 1: Assumes payload["repository"] always contains "owner" dict
    # If missing or flat, raises KeyError
    repo_info = payload["repository"]
    owner_login = repo_info["owner"]["login"]
    repo_name = repo_info["name"]

    # BUG 2: Empty or negative stars count causes ValueError
    stars = repo_info.get("stargazers_count", 0)
    if stars < 0:
        raise ValueError("Star count cannot be negative")

    # BUG 3: Off-by-one / unhandled None on topic tags
    topics = repo_info.get("topics", [])
    primary_topic = topics[0] if len(topics) > 0 else "general"

    return {
        "full_name": f"{owner_login}/{repo_name}",
        "owner": owner_login,
        "name": repo_name,
        "stars": max(0, stars),
        "primary_topic": primary_topic,
    }


def is_pull_request_valid(payload: Dict[str, Any]) -> bool:
    """Verifies PR payload contains required non-empty base attributes."""
    pr = payload.get("pull_request")
    if not pr:
        return False
    
    # BUG 4: Unhandled missing head/base refs crashing with KeyError
    head_sha = pr["head"]["sha"]
    base_ref = pr["base"]["ref"]
    
    return bool(head_sha and base_ref)
import pytest
from app.utils.payload_validator import extract_repo_metadata, is_pull_request_valid

def test_extract_repo_metadata_standard():
    payload = {
        "repository": {
            "name": "AutoFix",
            "owner": {"login": "ChandanRoy-2004"},
            "stargazers_count": 42,
            "topics": ["ci-cd", "automation"]
        }
    }
    meta = extract_repo_metadata(payload)
    assert meta["full_name"] == "ChandanRoy-2004/AutoFix"
    assert meta["primary_topic"] == "ci-cd"
    assert meta["stars"] == 42


def test_extract_repo_metadata_flat_owner_and_empty_topics():
    # Webhooks from GitHub Enterprise or mock events often have string owner or empty topics
    payload = {
        "repository": {
            "name": "AutoFix",
            "owner": "ChandanRoy-2004",  # String instead of nested dict
            "topics": []
        }
    }
    # Expected: Should gracefully resolve owner whether it is a dict or string
    meta = extract_repo_metadata(payload)
    assert meta["owner"] == "ChandanRoy-2004"
    assert meta["primary_topic"] == "general"


def test_is_pull_request_valid_missing_refs():
    # Partial ping event or webhook without full ref objects
    payload = {
        "pull_request": {
            "number": 1
            # Missing "head" and "base" keys
        }
    }
    # Expected: should return False gracefully instead of crashing with KeyError
    assert is_pull_request_valid(payload) is False
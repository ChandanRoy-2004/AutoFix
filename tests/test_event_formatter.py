import pytest
from app.utils.event_formatter import format_pr_audit_event, sanitize_installation_token

def test_sanitize_installation_token():
    token = "ghs_16charSecretKeyExample123456"
    masked = sanitize_installation_token(token)
    # Expected: keep first 4 chars, mask middle, keep last 4 chars
    assert masked.startswith("ghs_")
    assert masked.endswith("3456")
    assert "..." in masked

def test_sanitize_short_token():
    assert sanitize_installation_token("") == "****"
    assert sanitize_installation_token("abc") == "****"

def test_format_pr_audit_event_minimal():
    # Webhooks frequently emit payloads where 'labels' is empty or missing from partial hooks
    payload = {
        "repository": {"full_name": "ChandanRoy-2004/AutoFix"},
        "pull_request": {
            "number": 15,
            "labels": []
        },
        "sender": {"login": "developer-user"},
        "installation_token": "ghs_9876543210abcdef"
    }
    result = format_pr_audit_event("pull_request", payload)
    assert result["repo"] == "ChandanRoy-2004/AutoFix"
    assert result["pr_number"] == 15
    assert result["status"] == "INGESTED"

def test_format_pr_audit_event_missing_labels_key():
    payload = {
        "repository": {"full_name": "ChandanRoy-2004/AutoFix"},
        "pull_request": {
            "number": 16
        },
        "sender": {"login": "developer-user"}
    }
    # Should not raise KeyError when 'labels' key is completely missing
    result = format_pr_audit_event("pull_request", payload)
    assert result["label_count"] == 0
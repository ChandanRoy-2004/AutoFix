import hashlib
import hmac
import json
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from app.api.github_routes import verify_signature
from app.core.config import settings
from app.main import app

client = TestClient(app)


def test_verify_signature_dev_mode():
    with patch.object(settings, "GITHUB_WEBHOOK_SECRET", ""):
        assert verify_signature(b'{"test": 1}', None) is True
        assert verify_signature(b'{"test": 1}', "sha256=invalid") is True


def test_verify_signature_missing_header():
    with patch.object(settings, "GITHUB_WEBHOOK_SECRET", "secret123"):
        assert verify_signature(b'{"test": 1}', None) is False


def test_verify_signature_valid_and_invalid():
    secret = "mysecret"
    body = b'{"hello": "world"}'
    valid_sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    invalid_sig = "sha256=wrong"

    with patch.object(settings, "GITHUB_WEBHOOK_SECRET", secret):
        assert verify_signature(body, valid_sig) is True
        assert verify_signature(body, invalid_sig) is False


def test_webhook_invalid_signature():
    secret = "mysecret"
    body = json.dumps({"action": "opened"}).encode()
    with patch.object(settings, "GITHUB_WEBHOOK_SECRET", secret):
        response = client.post(
            "/api/github/webhook",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": "sha256=bad",
            },
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid signature"


def test_webhook_pull_request_opened():
    secret = "mysecret"
    payload = {
        "action": "opened",
        "number": 10,
        "repository": {"full_name": "owner/repo", "clone_url": "https://github.com/owner/repo.git"},
        "pull_request": {"number": 10, "head": {"ref": "feature-1"}},
        "installation": {"id": 12345},
    }
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    with patch.object(settings, "GITHUB_WEBHOOK_SECRET", secret):
        response = client.post(
            "/api/github/webhook",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": sig,
            },
        )
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["event"] == "pull_request"


def test_webhook_workflow_run_failure():
    secret = "mysecret"
    payload = {
        "action": "completed",
        "workflow_run": {
            "conclusion": "failure",
            "head_branch": "patch-1",
            "pull_requests": [{"number": 15}],
        },
        "repository": {"full_name": "owner/repo"},
        "installation": {"id": 54321},
    }
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    with patch.object(settings, "GITHUB_WEBHOOK_SECRET", secret):
        response = client.post(
            "/api/github/webhook",
            content=body,
            headers={
                "X-GitHub-Event": "workflow_run",
                "X-Hub-Signature-256": sig,
            },
        )
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert data["event"] == "workflow_run"


def test_webhook_invalid_json():
    secret = "mysecret"
    body = b"not-a-valid-json"
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    with patch.object(settings, "GITHUB_WEBHOOK_SECRET", secret):
        response = client.post(
            "/api/github/webhook",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": sig,
            },
        )
        assert response.status_code == 400

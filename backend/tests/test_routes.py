import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def client():
    with patch("db.audit_log.init_db"), \
         patch("integrations.k8s_client._load_config"):
        from main import app
        return TestClient(app)


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == "1.0.0"


def test_status_endpoint(client):
    mock_pods = [
        {"name": "api-abc", "phase": "Running", "ready": True, "restarts": 0, "node": "node-1"},
        {"name": "api-xyz", "phase": "CrashLoopBackOff", "ready": False, "restarts": 5, "node": "node-2"},
    ]
    with patch("api.routes.get_pods", return_value=mock_pods):
        response = client.post("/api/status", json={"namespace": "default"})
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert data["ready"] == 1
    assert data["unhealthy"] == 1
    assert data["namespace"] == "default"


def test_explain_endpoint(client):
    mock_loki_lines = [
        "ERROR: OOMKilled container exceeded memory limit",
        "FATAL: cannot allocate memory",
    ]
    mock_explain_result = {
        "severity": "critical",
        "summary": "The pod was OOMKilled.",
        "top_causes": ["Memory limit exceeded"],
        "suggested_action": "kubectl describe pod my-pod -n default",
        "audit_id": 1,
    }
    with patch("api.routes.get_pod_logs", new_callable=AsyncMock, return_value=mock_loki_lines), \
         patch("api.routes.explain_logs", new_callable=AsyncMock, return_value=mock_explain_result):
        response = client.post(
            "/api/explain",
            json={"pod_name": "my-pod", "namespace": "default", "lines": 50},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["severity"] == "critical"
    assert data["pod_name"] == "my-pod"
    assert data["namespace"] == "default"
    assert data["log_lines_analyzed"] == 2
    assert "audit_id" in data


def test_explain_endpoint_loki_empty_uses_kubectl(client):
    """When Loki returns empty, the route falls back to kubectl logs."""
    mock_explain_result = {
        "severity": "info",
        "summary": "No issues found.",
        "top_causes": [],
        "suggested_action": "none",
        "audit_id": 2,
    }
    with patch("api.routes.get_pod_logs", new_callable=AsyncMock, return_value=[]), \
         patch("api.routes.explain_logs", new_callable=AsyncMock, return_value=mock_explain_result), \
         patch("api.routes.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="log line 1\nlog line 2\n")
        response = client.post(
            "/api/explain",
            json={"pod_name": "my-pod", "namespace": "default"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["log_lines_analyzed"] == 2


def test_ask_endpoint(client):
    mock_result = {
        "answer": "Use kubectl get pods -n default to list pods.",
        "model": "claude-opus-4-6",
        "tokens_used": 80,
    }
    with patch("api.routes.ask_claude", new_callable=AsyncMock, return_value=mock_result):
        response = client.post("/api/ask", json={"question": "How do I list pods?"})
    assert response.status_code == 200
    data = response.json()
    assert "answer" in data
    assert "kubectl" in data["answer"]


def test_ask_with_namespace_context(client):
    mock_pods = [
        {"name": "app-1", "phase": "Running", "ready": False, "restarts": 10, "node": "node-1"},
    ]
    mock_result = {
        "answer": "Pod app-1 has 10 restarts, likely crashing.",
        "model": "claude-opus-4-6",
        "tokens_used": 90,
    }
    with patch("api.routes.get_pods", return_value=mock_pods), \
         patch("api.routes.ask_claude", new_callable=AsyncMock, return_value=mock_result):
        response = client.post(
            "/api/ask",
            json={"question": "Why is my pod restarting?", "namespace": "default"},
        )
    assert response.status_code == 200


def test_remediate_endpoint(client):
    mock_result = {
        "pod": "default/broken-pod",
        "issue": "CrashLoopBackOff",
        "suggested_commands": [
            "kubectl describe pod broken-pod -n default",
            "kubectl rollout restart deployment/broken -n default",
        ],
        "explanation": "The pod is crashing due to a misconfiguration.",
        "risk_score": 0.2,
        "risk_label": "low",
    }
    with patch("api.routes.get_pods", return_value=[]), \
         patch("api.routes.suggest_remediation", new_callable=AsyncMock, return_value=mock_result):
        response = client.post(
            "/api/remediate",
            json={"namespace": "default", "pod": "broken-pod", "issue": "CrashLoopBackOff"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["risk_label"] == "low"
    assert len(data["suggested_commands"]) == 2

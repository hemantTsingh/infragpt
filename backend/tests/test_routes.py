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
    mock_result = {
        "pod": "default/my-pod",
        "explanation": "The pod crashed due to OOM.",
        "severity": "critical",
        "model": "claude-opus-4-6",
        "tokens_used": 150,
    }
    with patch("api.routes.get_pod_logs", return_value="some log content"), \
         patch("api.routes.explain_logs", new_callable=AsyncMock, return_value=mock_result):
        response = client.post("/api/explain", json={"namespace": "default", "pod": "my-pod"})
    assert response.status_code == 200
    data = response.json()
    assert data["severity"] == "critical"
    assert data["pod"] == "default/my-pod"


def test_explain_pod_not_found(client):
    with patch("api.routes.get_pod_logs", return_value="Error fetching logs: Not Found"):
        response = client.post("/api/explain", json={"namespace": "default", "pod": "missing-pod"})
    assert response.status_code == 404


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
        "suggested_commands": ["kubectl describe pod broken-pod -n default", "kubectl rollout restart deployment/broken -n default"],
        "explanation": "The pod is crashing due to a misconfiguration.",
        "risk_score": 0.2,
        "risk_label": "low",
    }
    with patch("api.routes.get_pod_logs", return_value="crash loop detected"), \
         patch("api.routes.suggest_remediation", new_callable=AsyncMock, return_value=mock_result):
        response = client.post(
            "/api/remediate",
            json={"namespace": "default", "pod": "broken-pod", "issue": "CrashLoopBackOff"},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["risk_label"] == "low"
    assert len(data["suggested_commands"]) == 2

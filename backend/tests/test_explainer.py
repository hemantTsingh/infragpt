import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


SAMPLE_LOGS = """
2026-01-01T10:00:00Z Starting application
2026-01-01T10:00:01Z Connected to database
2026-01-01T10:01:00Z ERROR: connection refused to redis:6379
2026-01-01T10:01:01Z FATAL: cannot start without cache layer
2026-01-01T10:01:02Z panic: runtime error: invalid memory address
"""


@pytest.mark.asyncio
async def test_explain_logs_returns_expected_fields():
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="The pod crashed due to a Redis connection failure. Severity: critical. Recommended: check Redis service.")]
    mock_message.model = "claude-opus-4-6"
    mock_message.usage.input_tokens = 100
    mock_message.usage.output_tokens = 50

    with patch("ai.log_explainer._get_client") as mock_get_client, \
         patch("ai.log_explainer.log_decision"):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message
        mock_get_client.return_value = mock_client

        from ai.log_explainer import explain_logs
        result = await explain_logs("default", "my-pod-abc123", SAMPLE_LOGS)

    assert result["pod"] == "default/my-pod-abc123"
    assert "explanation" in result
    assert result["severity"] in ("info", "warning", "critical")
    assert result["tokens_used"] == 150


@pytest.mark.asyncio
async def test_explain_logs_severity_critical_detected():
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="OOMKilled: container exceeded memory limit. This is critical.")]
    mock_message.model = "claude-opus-4-6"
    mock_message.usage.input_tokens = 80
    mock_message.usage.output_tokens = 40

    with patch("ai.log_explainer._get_client") as mock_get_client, \
         patch("ai.log_explainer.log_decision"):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message
        mock_get_client.return_value = mock_client

        from ai.log_explainer import explain_logs
        result = await explain_logs("production", "api-server-xyz", "OOMKilled")

    assert result["severity"] == "critical"


@pytest.mark.asyncio
async def test_ask_claude_returns_answer():
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="To check pod logs run: kubectl logs <pod> -n <namespace>")]
    mock_message.model = "claude-opus-4-6"
    mock_message.usage.input_tokens = 60
    mock_message.usage.output_tokens = 30

    with patch("ai.log_explainer._get_client") as mock_get_client, \
         patch("ai.log_explainer.log_decision"):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message
        mock_get_client.return_value = mock_client

        from ai.log_explainer import ask_claude
        result = await ask_claude("How do I check pod logs?")

    assert "answer" in result
    assert "kubectl" in result["answer"]
    assert result["tokens_used"] == 90


def test_anomaly_rule_engine_detects_oom():
    from ai.anomaly_classifier import _rule_based_classify
    logs = "2026-01-01 container foo OOMKilled"
    anomalies = _rule_based_classify(logs)
    assert any(a["type"] == "oom_kill" for a in anomalies)
    assert any(a["severity"] == "critical" for a in anomalies)


def test_anomaly_rule_engine_detects_connection_refused():
    from ai.anomaly_classifier import _rule_based_classify
    logs = "2026-01-01 connection refused to postgres:5432"
    anomalies = _rule_based_classify(logs)
    assert any(a["type"] == "connection_error" for a in anomalies)


def test_anomaly_rule_engine_no_false_positives():
    from ai.anomaly_classifier import _rule_based_classify
    logs = "2026-01-01 Application started successfully. All systems nominal."
    anomalies = _rule_based_classify(logs)
    assert anomalies == []

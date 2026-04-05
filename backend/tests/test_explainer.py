import sys
import os
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SAMPLE_LOGS = [
    "2026-01-01T10:00:00Z Starting application",
    "2026-01-01T10:00:01Z Connected to database",
    "2026-01-01T10:01:00Z ERROR: connection refused to redis:6379",
    "2026-01-01T10:01:01Z FATAL: cannot start without cache layer",
    "2026-01-01T10:01:02Z panic: runtime error: invalid memory address",
]

_VALID_AI_RESPONSE = json.dumps({
    "severity": "critical",
    "summary": "Pod is crashing due to a Redis connection failure.",
    "top_causes": [
        "Redis service is unreachable on port 6379",
        "Cache layer is required for startup",
        "Memory panic after failed connection",
    ],
    "suggested_action": "kubectl describe svc redis -n default",
})


# ── Test 1: valid logs → correct fields in response ──────────────────────────

@pytest.mark.asyncio
async def test_explain_logs_valid_response():
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text=_VALID_AI_RESPONSE)]

    with patch("ai.log_explainer._get_client") as mock_get_client, \
         patch("ai.log_explainer.log_decision", return_value=42):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message
        mock_get_client.return_value = mock_client

        from ai.log_explainer import explain_logs
        result = await explain_logs("my-pod", "default", SAMPLE_LOGS)

    assert result["severity"] == "critical"
    assert isinstance(result["summary"], str) and len(result["summary"]) > 0
    assert isinstance(result["top_causes"], list) and len(result["top_causes"]) == 3
    assert "suggested_action" in result
    assert result["audit_id"] == 42


# ── Test 2: empty log list → no-log fallback dict ────────────────────────────

@pytest.mark.asyncio
async def test_explain_logs_empty_returns_fallback():
    with patch("ai.log_explainer._get_client") as mock_get_client, \
         patch("ai.log_explainer.log_decision", return_value=7):
        # _get_client should never be called for empty logs
        from ai.log_explainer import explain_logs
        result = await explain_logs("my-pod", "default", [])

    mock_get_client.assert_not_called()
    assert result["severity"] == "info"
    assert result["summary"] == "No logs found."
    assert result["top_causes"] == []
    assert result["suggested_action"] == "none"
    assert result["audit_id"] == 7


# ── Test 3: API timeout → fallback dict, no exception raised ─────────────────

@pytest.mark.asyncio
async def test_explain_logs_api_timeout_no_raise():
    with patch("ai.log_explainer._get_client") as mock_get_client, \
         patch("ai.log_explainer.log_decision", return_value=99):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = TimeoutError("API timed out")
        mock_get_client.return_value = mock_client

        from ai.log_explainer import explain_logs
        result = await explain_logs("my-pod", "default", SAMPLE_LOGS)

    # Must not raise — must return a degraded-but-valid dict
    assert "severity" in result
    assert "summary" in result
    assert "top_causes" in result
    assert "suggested_action" in result
    assert result["audit_id"] == 99
    assert result["severity"] == "info"
    assert "unavailable" in result["summary"].lower()

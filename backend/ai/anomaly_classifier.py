import os
import re
from typing import Optional

import anthropic

from db.audit_log import log_decision

RULE_PATTERNS = [
    {
        "pattern": re.compile(r"OOMKilled", re.IGNORECASE),
        "type": "oom_kill",
        "severity": "critical",
        "message": "Container was OOMKilled — memory limit exceeded",
    },
    {
        "pattern": re.compile(r"CrashLoopBackOff", re.IGNORECASE),
        "type": "crash_loop",
        "severity": "critical",
        "message": "Pod is in CrashLoopBackOff",
    },
    {
        "pattern": re.compile(r"Back-off restarting failed container", re.IGNORECASE),
        "type": "crash_loop",
        "severity": "critical",
        "message": "Container restart back-off detected",
    },
    {
        "pattern": re.compile(r"connection refused", re.IGNORECASE),
        "type": "connection_error",
        "severity": "warning",
        "message": "Connection refused — dependency may be down",
    },
    {
        "pattern": re.compile(r"timeout|timed out", re.IGNORECASE),
        "type": "timeout",
        "severity": "warning",
        "message": "Timeout detected in logs",
    },
    {
        "pattern": re.compile(r"permission denied", re.IGNORECASE),
        "type": "permission_error",
        "severity": "warning",
        "message": "Permission denied — check RBAC or file permissions",
    },
    {
        "pattern": re.compile(r"out of memory|memory pressure", re.IGNORECASE),
        "type": "memory_pressure",
        "severity": "warning",
        "message": "Memory pressure detected",
    },
    {
        "pattern": re.compile(r"panic:", re.IGNORECASE),
        "type": "panic",
        "severity": "critical",
        "message": "Application panic detected",
    },
    {
        "pattern": re.compile(r"FATAL|fatal error", re.IGNORECASE),
        "type": "fatal_error",
        "severity": "critical",
        "message": "Fatal error in application logs",
    },
    {
        "pattern": re.compile(r"disk.*full|no space left on device", re.IGNORECASE),
        "type": "disk_full",
        "severity": "critical",
        "message": "Disk full or no space left on device",
    },
]


def _rule_based_classify(logs: str) -> list[dict]:
    found = []
    seen_types = set()
    for rule in RULE_PATTERNS:
        if rule["type"] not in seen_types and rule["pattern"].search(logs):
            found.append({
                "type": rule["type"],
                "severity": rule["severity"],
                "message": rule["message"],
                "source": "rule_engine",
            })
            seen_types.add(rule["type"])
    return found


async def _claude_classify(namespace: str, pod: Optional[str], logs: str) -> list[dict]:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    prompt = f"""Analyze these Kubernetes pod logs for anomalies that rule-based systems might miss.
For each anomaly found, respond in this exact JSON array format (no markdown):
[
  {{"type": "anomaly_type", "severity": "info|warning|critical", "message": "brief description", "source": "claude"}}
]
If no additional anomalies found beyond obvious errors, return: []

Pod: {namespace}/{pod or "unknown"}
Logs:
{logs[-4000:]}"""

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    import json
    try:
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
    except (json.JSONDecodeError, ValueError):
        pass
    return []


async def classify_anomalies(namespace: str, pod: Optional[str], logs: str) -> dict:
    rule_anomalies = _rule_based_classify(logs)

    claude_anomalies = []
    if not any(a["severity"] == "critical" for a in rule_anomalies):
        claude_anomalies = await _claude_classify(namespace, pod, logs)

    all_anomalies = rule_anomalies + claude_anomalies
    needs_attention = any(a["severity"] in ("warning", "critical") for a in all_anomalies)

    log_decision(
        action="classify_anomalies",
        resource=f"{namespace}/{pod or '*'}",
        decision=f"Found {len(all_anomalies)} anomalies",
        confidence=0.9 if rule_anomalies else 0.7,
    )

    return {
        "anomalies": all_anomalies,
        "total_found": len(all_anomalies),
        "needs_attention": needs_attention,
    }

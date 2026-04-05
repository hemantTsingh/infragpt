import json
import os
from typing import Optional

import anthropic

from db.audit_log import log_decision

RISK_LABELS = {
    (0.0, 0.3): "low",
    (0.3, 0.6): "medium",
    (0.6, 0.8): "high",
    (0.8, 1.01): "critical",
}

HIGH_RISK_COMMANDS = {"kubectl delete", "kubectl drain", "kubectl cordon", "--force", "--grace-period=0"}


def _assess_risk(commands: list[str]) -> float:
    score = 0.1
    cmd_str = " ".join(commands).lower()
    if any(kw in cmd_str for kw in HIGH_RISK_COMMANDS):
        score += 0.5
    if "restart" in cmd_str or "rollout" in cmd_str:
        score += 0.2
    if "scale" in cmd_str:
        score += 0.1
    return min(score, 1.0)


def _risk_label(score: float) -> str:
    for (low, high), label in RISK_LABELS.items():
        if low <= score < high:
            return label
    return "unknown"


async def suggest_remediation(
    namespace: str, pod: str, issue: str, logs: Optional[str] = None
) -> dict:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    log_context = f"\nRecent logs:\n{logs[-2000:]}" if logs else ""

    prompt = f"""You are a Kubernetes SRE. A pod has an issue that needs remediation.

Pod: {namespace}/{pod}
Issue: {issue}{log_context}

Respond with a JSON object (no markdown) in this exact format:
{{
  "explanation": "clear explanation of the issue and why these steps fix it",
  "suggested_commands": [
    "kubectl command 1",
    "kubectl command 2"
  ]
}}

Rules:
- Commands must be real, runnable kubectl commands for namespace {namespace}
- Order commands from safest to most invasive
- Maximum 5 commands
- Use the actual pod/deployment name: {pod}"""

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=768,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        data = json.loads(raw[start:end])
        explanation = data.get("explanation", "")
        commands = data.get("suggested_commands", [])
    except (json.JSONDecodeError, ValueError):
        explanation = raw
        commands = []

    risk_score = _assess_risk(commands)
    risk_label = _risk_label(risk_score)

    log_decision(
        action="remediation",
        resource=f"{namespace}/{pod}",
        decision=f"risk={risk_label} commands={commands}",
        confidence=1.0 - risk_score,
    )

    return {
        "pod": f"{namespace}/{pod}",
        "issue": issue,
        "suggested_commands": commands,
        "explanation": explanation,
        "risk_score": round(risk_score, 2),
        "risk_label": risk_label,
    }

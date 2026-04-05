import os
from typing import Optional

import anthropic

from db.audit_log import log_decision

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


async def explain_logs(namespace: str, pod: str, logs: str) -> dict:
    prompt = f"""You are an expert Kubernetes SRE. Analyze the following pod logs and provide:
1. A concise summary of what is happening (2-3 sentences)
2. Root cause analysis if there are errors
3. Severity level — respond with exactly one of: info, warning, critical
4. Recommended actions as a numbered list

Pod: {namespace}/{pod}

Logs:
{logs[-6000:]}"""

    client = _get_client()
    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    explanation = message.content[0].text
    severity = "info"
    lower = explanation.lower()
    if "critical" in lower or "crash" in lower or "oomkilled" in lower:
        severity = "critical"
    elif "warning" in lower or "error" in lower or "fail" in lower:
        severity = "warning"

    log_decision(
        action="explain_logs",
        resource=f"{namespace}/{pod}",
        decision=explanation[:500],
        confidence=1.0,
    )

    return {
        "pod": f"{namespace}/{pod}",
        "explanation": explanation,
        "severity": severity,
        "model": message.model,
        "tokens_used": message.usage.input_tokens + message.usage.output_tokens,
    }


async def ask_claude(question: str, context: Optional[str] = None) -> dict:
    system = (
        "You are InfraGPT, an expert Kubernetes and infrastructure assistant. "
        "Give concise, actionable answers. Include kubectl commands where relevant."
    )

    user_message = question
    if context:
        user_message = f"Context:\n{context}\n\nQuestion: {question}"

    client = _get_client()
    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )

    answer = message.content[0].text

    log_decision(
        action="ask",
        resource="general",
        decision=answer[:500],
        confidence=1.0,
    )

    return {
        "answer": answer,
        "model": message.model,
        "tokens_used": message.usage.input_tokens + message.usage.output_tokens,
    }

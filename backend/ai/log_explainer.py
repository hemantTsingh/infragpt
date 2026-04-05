import json
import logging
import os
from datetime import datetime
from typing import Optional

import anthropic

from db.audit_log import log_decision

logger = logging.getLogger(__name__)

_client: Optional[anthropic.Anthropic] = None

_EXPLAIN_SYSTEM = (
    "You are an expert Kubernetes SRE. Analyze these pod logs and "
    "respond ONLY with valid JSON in this exact format:\n"
    "{\n"
    '  "severity": "info|warning|critical",\n'
    '  "summary": "string (2 sentences max)",\n'
    '  "top_causes": ["string", "string", "string"],\n'
    '  "suggested_action": "string (one kubectl command or none)"\n'
    "}"
)

_EMPTY_FALLBACK = {
    "severity": "info",
    "summary": "No logs found.",
    "top_causes": [],
    "suggested_action": "none",
}


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


async def explain_logs(pod_name: str, namespace: str, log_lines: list[str]) -> dict:
    if not log_lines:
        audit_id = log_decision(
            action="explain_logs",
            resource=f"{namespace}/{pod_name}",
            decision=json.dumps(_EMPTY_FALLBACK),
            confidence=0.0,
        )
        return {**_EMPTY_FALLBACK, "audit_id": audit_id}

    joined = "\n".join(log_lines)
    if len(joined) > 6000:
        joined = joined[-6000:]

    try:
        message = _get_client().messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=512,
            system=_EXPLAIN_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": f"Pod: {namespace}/{pod_name}\n\nLogs:\n{joined}",
                }
            ],
        )
        raw = message.content[0].text.strip()

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            # Strip markdown code fences if the model wrapped the JSON
            if "```" in raw:
                raw = raw.split("```")[1].lstrip("json").strip()
            try:
                result = json.loads(raw)
            except json.JSONDecodeError:
                result = {
                    "severity": "info",
                    "summary": raw[:200],
                    "top_causes": [],
                    "suggested_action": "none",
                }

    except Exception as exc:
        logger.warning("explain_logs AI call failed for %s/%s: %s", namespace, pod_name, exc)
        result = {
            "severity": "info",
            "summary": f"AI analysis unavailable: {exc}",
            "top_causes": [],
            "suggested_action": "none",
        }

    audit_id = log_decision(
        action="explain_logs",
        resource=f"{namespace}/{pod_name}",
        decision=json.dumps({
            "severity": result.get("severity"),
            "summary": result.get("summary", "")[:500],
            "timestamp": datetime.utcnow().isoformat(),
        }),
        confidence=1.0,
    )

    return {**result, "audit_id": audit_id}


async def ask_claude(question: str, context: Optional[str] = None) -> dict:
    system = (
        "You are InfraGPT, an expert Kubernetes and infrastructure assistant. "
        "Give concise, actionable answers. Include kubectl commands where relevant."
    )
    user_message = question
    if context:
        user_message = f"Context:\n{context}\n\nQuestion: {question}"

    message = _get_client().messages.create(
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

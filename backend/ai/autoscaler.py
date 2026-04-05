import json
import os
from typing import Optional

import anthropic

from db.audit_log import log_decision
from integrations.k8s_client import get_hpa, patch_hpa
from integrations.prom_client import get_pod_cpu, get_pod_memory


async def get_scaling_recommendation(
    namespace: str,
    deployment: str,
    current_replicas: int,
    min_replicas: int,
    max_replicas: int,
) -> dict:
    cpu = await get_pod_cpu(namespace, deployment)
    memory = await get_pod_memory(namespace, deployment)

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    metrics_str = (
        f"CPU usage: {cpu:.4f} cores" if cpu is not None else "CPU usage: unavailable"
    )
    if memory is not None:
        metrics_str += f"\nMemory usage: {memory / 1024 / 1024:.1f} MiB"
    else:
        metrics_str += "\nMemory usage: unavailable"

    prompt = f"""You are an autoscaling expert for Kubernetes. Recommend scaling decisions.

Deployment: {namespace}/{deployment}
Current replicas: {current_replicas}
HPA range: min={min_replicas}, max={max_replicas}
{metrics_str}

Respond with a JSON object (no markdown):
{{
  "recommended_replicas": <integer between {min_replicas} and {max_replicas}>,
  "action": "scale_up" | "scale_down" | "no_change",
  "reason": "brief explanation",
  "confidence": <float 0.0-1.0>
}}

Rules:
- Only recommend changes if there is clear evidence
- Prefer gradual scaling (±1-2 replicas)
- If metrics are unavailable, recommend no_change"""

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        data = json.loads(raw[start:end])
    except (json.JSONDecodeError, ValueError):
        data = {
            "recommended_replicas": current_replicas,
            "action": "no_change",
            "reason": "Failed to parse Claude response",
            "confidence": 0.0,
        }

    log_decision(
        action="autoscale",
        resource=f"{namespace}/{deployment}",
        decision=f"action={data.get('action')} replicas={data.get('recommended_replicas')}",
        confidence=data.get("confidence", 0.0),
    )

    return {
        "deployment": f"{namespace}/{deployment}",
        "current_replicas": current_replicas,
        "recommended_replicas": data.get("recommended_replicas", current_replicas),
        "action": data.get("action", "no_change"),
        "reason": data.get("reason", ""),
        "confidence": data.get("confidence", 0.0),
        "metrics": {"cpu": cpu, "memory_mib": memory / 1024 / 1024 if memory else None},
    }


async def apply_scaling_recommendation(
    namespace: str,
    deployment: str,
    hpa_name: Optional[str] = None,
) -> dict:
    hpas = get_hpa(namespace)
    target_hpa = next(
        (h for h in hpas if h["name"] == (hpa_name or deployment)),
        None,
    )

    if not target_hpa:
        return {"error": f"No HPA found for {deployment} in {namespace}"}

    rec = await get_scaling_recommendation(
        namespace=namespace,
        deployment=deployment,
        current_replicas=target_hpa["current_replicas"],
        min_replicas=target_hpa["min_replicas"],
        max_replicas=target_hpa["max_replicas"],
    )

    if rec["action"] == "no_change" or rec["confidence"] < 0.7:
        rec["applied"] = False
        rec["skip_reason"] = "no_change or low confidence"
        return rec

    new_min = target_hpa["min_replicas"]
    new_max = target_hpa["max_replicas"]
    recommended = rec["recommended_replicas"]

    if rec["action"] == "scale_up":
        new_min = max(new_min, recommended)
    elif rec["action"] == "scale_down":
        new_max = min(new_max, recommended)

    patch_hpa(namespace, target_hpa["name"], new_min, new_max)
    rec["applied"] = True
    rec["new_hpa_min"] = new_min
    rec["new_hpa_max"] = new_max
    return rec

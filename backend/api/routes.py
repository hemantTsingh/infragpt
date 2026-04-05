from fastapi import APIRouter, HTTPException

from ai.anomaly_classifier import classify_anomalies
from ai.log_explainer import explain_logs
from ai.remediation import suggest_remediation
from api.models import (
    AnomalyRequest,
    AnomalyResponse,
    AskRequest,
    AskResponse,
    ExplainRequest,
    ExplainResponse,
    HealthResponse,
    RemediationRequest,
    RemediationResponse,
    StatusRequest,
    StatusResponse,
    PodStatus,
)
from integrations.k8s_client import get_pod_logs, get_pods
from ai.log_explainer import ask_claude

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok", version="1.0.0")


@router.post("/api/explain", response_model=ExplainResponse)
async def explain(req: ExplainRequest):
    logs = get_pod_logs(req.namespace, req.pod, tail_lines=req.tail_lines)
    if logs.startswith("Error fetching logs:"):
        raise HTTPException(status_code=404, detail=logs)
    result = await explain_logs(req.namespace, req.pod, logs)
    return ExplainResponse(**result)


@router.post("/api/status", response_model=StatusResponse)
async def status(req: StatusRequest):
    try:
        pods = get_pods(req.namespace)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    pod_statuses = [
        PodStatus(
            name=p["name"],
            phase=p["phase"] or "Unknown",
            ready=p["ready"],
            restarts=p["restarts"],
            node=p["node"] or "unknown",
        )
        for p in pods
    ]

    ready_count = sum(1 for p in pod_statuses if p.ready)
    return StatusResponse(
        namespace=req.namespace,
        pods=pod_statuses,
        total=len(pod_statuses),
        ready=ready_count,
        unhealthy=len(pod_statuses) - ready_count,
    )


@router.post("/api/ask", response_model=AskResponse)
async def ask(req: AskRequest):
    context_parts = []
    if req.namespace:
        try:
            pods = get_pods(req.namespace)
            unhealthy = [p for p in pods if not p["ready"]]
            context_parts.append(
                f"Namespace {req.namespace}: {len(pods)} pods, "
                f"{len(unhealthy)} unhealthy: {[p['name'] for p in unhealthy]}"
            )
        except Exception:
            pass
    if req.context:
        context_parts.append(req.context)

    result = await ask_claude(req.question, "\n".join(context_parts) if context_parts else None)
    return AskResponse(**result)


@router.post("/api/remediate", response_model=RemediationResponse)
async def remediate(req: RemediationRequest):
    logs = get_pod_logs(req.namespace, req.pod, tail_lines=100)
    result = await suggest_remediation(req.namespace, req.pod, req.issue, logs)
    return RemediationResponse(**result)


@router.post("/api/anomalies", response_model=AnomalyResponse)
async def anomalies(req: AnomalyRequest):
    logs = req.logs
    if not logs and req.pod:
        logs = get_pod_logs(req.namespace, req.pod, tail_lines=500)
    if not logs:
        raise HTTPException(status_code=400, detail="Provide logs or a pod name to fetch logs from")
    result = await classify_anomalies(req.namespace, req.pod, logs)
    return AnomalyResponse(**result)

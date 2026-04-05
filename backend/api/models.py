from pydantic import BaseModel, Field
from typing import Optional


class ExplainRequest(BaseModel):
    pod_name: str
    namespace: str
    lines: int = Field(default=100, ge=10, le=2000)


class ExplainResponse(BaseModel):
    pod_name: str
    namespace: str
    audit_id: int
    severity: str
    summary: str
    top_causes: list[str]
    suggested_action: str
    log_lines_analyzed: int


class AskRequest(BaseModel):
    question: str
    namespace: Optional[str] = None
    context: Optional[str] = None


class AskResponse(BaseModel):
    answer: str
    model: str
    tokens_used: int


class StatusRequest(BaseModel):
    namespace: str


class PodStatus(BaseModel):
    name: str
    phase: str
    ready: bool
    restarts: int
    node: str


class StatusResponse(BaseModel):
    namespace: str
    pods: list[PodStatus]
    total: int
    ready: int
    unhealthy: int


class RemediationRequest(BaseModel):
    namespace: str
    pod: str
    issue: str


class RemediationResponse(BaseModel):
    pod: str
    issue: str
    suggested_commands: list[str]
    explanation: str
    risk_score: float = Field(ge=0.0, le=1.0)
    risk_label: str


class AnomalyRequest(BaseModel):
    namespace: str
    pod: Optional[str] = None
    logs: Optional[str] = None


class AnomalyResponse(BaseModel):
    anomalies: list[dict]
    total_found: int
    needs_attention: bool


class HealthResponse(BaseModel):
    status: str
    version: str


class AuditEntry(BaseModel):
    id: int
    timestamp: str
    action: str
    resource: str
    decision: str
    confidence: float


class AuditResponse(BaseModel):
    entries: list[AuditEntry]
    total: int

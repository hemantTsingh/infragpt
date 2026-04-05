import logging
import time
from typing import Optional

import httpx

LOKI_URL = "http://loki.monitoring.svc.cluster.local:3100"

logger = logging.getLogger(__name__)


async def get_pod_logs(pod_name: str, namespace: str, lines: int = 100) -> list[str]:
    """Fetch pod logs from Loki. Returns empty list on any connection error."""
    end = int(time.time() * 1e9)
    start = int((time.time() - 3600) * 1e9)  # last hour
    logql = f'{{namespace="{namespace}"}} |= "{pod_name}"'

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{LOKI_URL}/loki/api/v1/query_range",
                params={
                    "query": logql,
                    "start": start,
                    "end": end,
                    "limit": lines,
                    "direction": "forward",
                },
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.warning("Loki unreachable for %s/%s: %s", namespace, pod_name, exc)
        return []

    log_lines: list[str] = []
    for stream in data.get("data", {}).get("result", []):
        for _ts, line in stream.get("values", []):
            log_lines.append(line)
    return log_lines


async def query_logs(
    namespace: str,
    pod: Optional[str] = None,
    since_minutes: int = 60,
    limit: int = 1000,
) -> list[str]:
    end = int(time.time() * 1e9)
    start = int((time.time() - since_minutes * 60) * 1e9)

    logql = (
        f'{{namespace="{namespace}", pod="{pod}"}}'
        if pod
        else f'{{namespace="{namespace}"}}'
    )

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{LOKI_URL}/loki/api/v1/query_range",
            params={"query": logql, "start": start, "end": end, "limit": limit},
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

    lines = []
    for stream in data.get("data", {}).get("result", []):
        for _ts, line in stream.get("values", []):
            lines.append(line)
    return lines


async def query_loki_raw(
    logql: str,
    since_minutes: int = 60,
    limit: int = 500,
) -> dict:
    end = int(time.time() * 1e9)
    start = int((time.time() - since_minutes * 60) * 1e9)

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{LOKI_URL}/loki/api/v1/query_range",
            params={"query": logql, "start": start, "end": end, "limit": limit},
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


async def get_error_logs(
    namespace: str,
    pod: Optional[str] = None,
    since_minutes: int = 30,
) -> list[str]:
    if pod:
        logql = f'{{namespace="{namespace}", pod="{pod}"}} |~ "(?i)(error|fatal|panic|exception)"'
    else:
        logql = f'{{namespace="{namespace}"}} |~ "(?i)(error|fatal|panic|exception)"'

    return (
        (await query_loki_raw(logql, since_minutes=since_minutes, limit=200))
        .get("data", {})
        .get("result", [])
    )


async def health_check() -> bool:
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{LOKI_URL}/ready", timeout=5.0)
            return response.status_code == 200
    except httpx.RequestError:
        return False

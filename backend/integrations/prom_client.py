from typing import Optional

import httpx

PROMETHEUS_URL = (
    "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090"
)


async def query(promql: str) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": promql},
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


async def query_range(
    promql: str, start: str, end: str, step: str = "60s"
) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{PROMETHEUS_URL}/api/v1/query_range",
            params={"query": promql, "start": start, "end": end, "step": step},
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()


async def get_pod_cpu(namespace: str, pod: str) -> Optional[float]:
    result = await query(
        f'rate(container_cpu_usage_seconds_total{{namespace="{namespace}",pod=~"{pod}.*",container!=""}}[5m])'
    )
    results = result.get("data", {}).get("result", [])
    if results:
        return float(results[0]["value"][1])
    return None


async def get_pod_memory(namespace: str, pod: str) -> Optional[float]:
    result = await query(
        f'sum(container_memory_working_set_bytes{{namespace="{namespace}",pod=~"{pod}.*",container!=""}}) by (pod)'
    )
    results = result.get("data", {}).get("result", [])
    if results:
        return float(results[0]["value"][1])
    return None


async def get_cluster_metrics() -> dict:
    cpu_result = await query(
        'sum(rate(container_cpu_usage_seconds_total{container!=""}[5m])) by (namespace)'
    )
    mem_result = await query(
        'sum(container_memory_working_set_bytes{container!=""}) by (namespace)'
    )

    cpu_by_ns = {
        item["metric"].get("namespace", "unknown"): float(item["value"][1])
        for item in cpu_result.get("data", {}).get("result", [])
    }
    mem_by_ns = {
        item["metric"].get("namespace", "unknown"): float(item["value"][1])
        for item in mem_result.get("data", {}).get("result", [])
    }

    return {"cpu_by_namespace": cpu_by_ns, "memory_by_namespace": mem_by_ns}


async def get_pod_restart_rate(namespace: str, pod: str) -> Optional[float]:
    result = await query(
        f'rate(kube_pod_container_status_restarts_total{{namespace="{namespace}",pod="{pod}"}}[1h])'
    )
    results = result.get("data", {}).get("result", [])
    if results:
        return float(results[0]["value"][1])
    return None


async def health_check() -> bool:
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{PROMETHEUS_URL}/-/healthy", timeout=5.0)
            return response.status_code == 200
    except httpx.RequestError:
        return False

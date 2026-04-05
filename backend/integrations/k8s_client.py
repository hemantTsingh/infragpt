import datetime

from kubernetes import client, config
from kubernetes.client.rest import ApiException


def _load_config():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


_load_config()


def get_pods(namespace: str) -> list[dict]:
    v1 = client.CoreV1Api()
    pods = v1.list_namespaced_pod(namespace)
    return [
        {
            "name": pod.metadata.name,
            "namespace": pod.metadata.namespace,
            "phase": pod.status.phase,
            "ready": all(
                cs.ready for cs in (pod.status.container_statuses or [])
            ),
            "restarts": sum(
                cs.restart_count for cs in (pod.status.container_statuses or [])
            ),
            "node": pod.spec.node_name,
        }
        for pod in pods.items
    ]


def get_pod_logs(namespace: str, pod: str, tail_lines: int = 200) -> str:
    v1 = client.CoreV1Api()
    try:
        return v1.read_namespaced_pod_log(
            name=pod,
            namespace=namespace,
            tail_lines=tail_lines,
            timestamps=True,
        )
    except ApiException as e:
        return f"Error fetching logs: {e.reason}"


def get_deployments(namespace: str) -> list[dict]:
    apps_v1 = client.AppsV1Api()
    deployments = apps_v1.list_namespaced_deployment(namespace)
    return [
        {
            "name": d.metadata.name,
            "namespace": d.metadata.namespace,
            "replicas": d.spec.replicas,
            "available": d.status.available_replicas or 0,
            "ready": d.status.ready_replicas or 0,
        }
        for d in deployments.items
    ]


def scale_deployment(namespace: str, deployment: str, replicas: int) -> dict:
    apps_v1 = client.AppsV1Api()
    apps_v1.patch_namespaced_deployment_scale(
        name=deployment,
        namespace=namespace,
        body={"spec": {"replicas": replicas}},
    )
    return {"deployment": deployment, "namespace": namespace, "replicas": replicas}


def restart_deployment(namespace: str, deployment: str) -> dict:
    apps_v1 = client.AppsV1Api()
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": datetime.datetime.utcnow().isoformat()
                    }
                }
            }
        }
    }
    apps_v1.patch_namespaced_deployment(
        name=deployment, namespace=namespace, body=patch
    )
    return {"deployment": deployment, "namespace": namespace, "action": "restarted"}


def get_nodes() -> list[dict]:
    v1 = client.CoreV1Api()
    nodes = v1.list_node()
    result = []
    for node in nodes.items:
        conditions = {c.type: c.status for c in node.status.conditions}
        result.append(
            {
                "name": node.metadata.name,
                "ready": conditions.get("Ready") == "True",
                "cpu": node.status.capacity.get("cpu"),
                "memory": node.status.capacity.get("memory"),
                "roles": [
                    k.split("/")[-1]
                    for k in node.metadata.labels
                    if k.startswith("node-role.kubernetes.io/")
                ],
            }
        )
    return result


def get_events(namespace: str, involved_object: str = None) -> list[dict]:
    v1 = client.CoreV1Api()
    field_selector = None
    if involved_object:
        field_selector = f"involvedObject.name={involved_object}"
    events = v1.list_namespaced_event(
        namespace, field_selector=field_selector
    )
    return [
        {
            "reason": e.reason,
            "message": e.message,
            "type": e.type,
            "count": e.count,
            "first_time": str(e.first_timestamp),
            "last_time": str(e.last_timestamp),
        }
        for e in events.items
    ]


def get_hpa(namespace: str) -> list[dict]:
    autoscaling_v2 = client.AutoscalingV2Api()
    try:
        hpas = autoscaling_v2.list_namespaced_horizontal_pod_autoscaler(namespace)
        return [
            {
                "name": h.metadata.name,
                "namespace": h.metadata.namespace,
                "min_replicas": h.spec.min_replicas,
                "max_replicas": h.spec.max_replicas,
                "current_replicas": h.status.current_replicas,
                "desired_replicas": h.status.desired_replicas,
            }
            for h in hpas.items
        ]
    except ApiException:
        return []


def patch_hpa(namespace: str, hpa_name: str, min_replicas: int, max_replicas: int) -> dict:
    autoscaling_v2 = client.AutoscalingV2Api()
    autoscaling_v2.patch_namespaced_horizontal_pod_autoscaler(
        name=hpa_name,
        namespace=namespace,
        body={"spec": {"minReplicas": min_replicas, "maxReplicas": max_replicas}},
    )
    return {
        "hpa": hpa_name,
        "min_replicas": min_replicas,
        "max_replicas": max_replicas,
    }

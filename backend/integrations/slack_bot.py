import hashlib
import hmac
import os
import time
from typing import Optional

from fastapi import HTTPException, Request
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

_slack_client: Optional[WebClient] = None


def _get_client() -> WebClient:
    global _slack_client
    if _slack_client is None:
        token = os.getenv("SLACK_BOT_TOKEN")
        if not token:
            raise RuntimeError("SLACK_BOT_TOKEN not set")
        _slack_client = WebClient(token=token)
    return _slack_client


def verify_slack_signature(body: bytes, timestamp: str, signature: str) -> bool:
    signing_secret = os.getenv("SLACK_SIGNING_SECRET", "")
    if abs(time.time() - int(timestamp)) > 300:
        return False
    base = f"v0:{timestamp}:{body.decode('utf-8')}"
    expected = (
        "v0="
        + hmac.new(
            signing_secret.encode(), base.encode(), hashlib.sha256
        ).hexdigest()
    )
    return hmac.compare_digest(expected, signature)


async def handle_slash_command(request: Request) -> dict:
    body_bytes = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "0")
    signature = request.headers.get("X-Slack-Signature", "")

    if not verify_slack_signature(body_bytes, timestamp, signature):
        raise HTTPException(status_code=403, detail="Invalid Slack signature")

    from urllib.parse import parse_qs
    params = parse_qs(body_bytes.decode("utf-8"))
    command = params.get("command", [""])[0]
    text = params.get("text", [""])[0].strip()
    response_url = params.get("response_url", [""])[0]
    channel_id = params.get("channel_id", [""])[0]

    if command == "/infragpt":
        return await _handle_infragpt_command(text, channel_id, response_url)

    return {"response_type": "ephemeral", "text": f"Unknown command: {command}"}


async def _handle_infragpt_command(text: str, channel_id: str, response_url: str) -> dict:
    parts = text.split(maxsplit=1)
    subcommand = parts[0].lower() if parts else "help"
    args = parts[1] if len(parts) > 1 else ""

    if subcommand == "status":
        namespace = args or "default"
        from integrations.k8s_client import get_pods
        pods = get_pods(namespace)
        unhealthy = [p for p in pods if not p["ready"]]
        lines = [f"*Namespace:* `{namespace}` — {len(pods)} pods, {len(unhealthy)} unhealthy"]
        for p in unhealthy:
            lines.append(f"  :red_circle: `{p['name']}` phase={p['phase']} restarts={p['restarts']}")
        return {"response_type": "in_channel", "text": "\n".join(lines)}

    if subcommand == "explain":
        parts2 = args.split("/")
        if len(parts2) != 2:
            return {"response_type": "ephemeral", "text": "Usage: /infragpt explain <namespace>/<pod>"}
        namespace, pod = parts2
        from integrations.k8s_client import get_pod_logs
        from ai.log_explainer import explain_logs
        logs = get_pod_logs(namespace, pod, tail_lines=100)
        result = await explain_logs(namespace, pod, logs)
        return {
            "response_type": "in_channel",
            "text": f"*InfraGPT Analysis — `{namespace}/{pod}`*\n{result['explanation'][:2000]}",
        }

    if subcommand == "ask":
        from ai.log_explainer import ask_claude
        result = await ask_claude(args)
        return {"response_type": "in_channel", "text": f"*InfraGPT:* {result['answer'][:2000]}"}

    return {
        "response_type": "ephemeral",
        "text": (
            "*InfraGPT Slash Commands*\n"
            "`/infragpt status [namespace]` — show pod health\n"
            "`/infragpt explain <namespace>/<pod>` — AI log analysis\n"
            "`/infragpt ask <question>` — ask InfraGPT anything"
        ),
    }


def post_message(channel: str, text: str, blocks: Optional[list] = None) -> bool:
    try:
        kwargs = {"channel": channel, "text": text}
        if blocks:
            kwargs["blocks"] = blocks
        _get_client().chat_postMessage(**kwargs)
        return True
    except SlackApiError:
        return False


def post_alert(channel: str, namespace: str, pod: str, severity: str, message: str) -> bool:
    emoji = {"critical": ":rotating_light:", "warning": ":warning:", "info": ":information_source:"}.get(
        severity, ":grey_question:"
    )
    text = f"{emoji} *InfraGPT Alert* [{severity.upper()}]\nPod: `{namespace}/{pod}`\n{message}"
    return post_message(channel, text)

"""Forward Claude pane events into Hermes's webhook adapter (V2 HMAC)."""
from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request

import herdrbridge as hb

WEBHOOK_BASE = os.environ.get("HERMES_WEBHOOK_BASE", "http://127.0.0.1:8644")
PROMPT_TEMPLATE = (
    "Claude Code session '{session}' (herdr pane {pane_id}) is now {state}.\n"
    "Screen excerpt:\n{excerpt}\n\n"
    "You are Hermes. Use the claude-bridge skill: run `state {session}` and `read {session}`, then relay "
    "Claude's question or result to the user. If Claude is asking for approval, describe the exact command "
    "or action and wait for the user's decision — never approve or answer it yourself."
)


@dataclasses.dataclass
class WebhookConfig:
    route: str
    secret: str
    url: str


def sign_v2(secret: str, timestamp: int, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), ("%d." % timestamp).encode("utf-8") + body, hashlib.sha256).hexdigest()


def build_payload(session: str, pane_id: str, state: str, excerpt: str) -> dict:
    lines = [ln.rstrip() for ln in (excerpt or "").splitlines()]
    text = "\n".join(lines[-40:])
    if len(text) > 3000:
        text = text[-3000:]
    return {"event_type": "claude_done" if state == "idle" else "claude_blocked",
            "session": session, "pane_id": pane_id, "state": state, "excerpt": text}


def _cfg_path(state_dir: str) -> str:
    return os.path.join(state_dir, "webhook.json")


def load_config(state_dir: str) -> WebhookConfig | None:
    p = _cfg_path(state_dir)
    if not os.path.exists(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        d = json.load(f)
    return WebhookConfig(d["route"], d["secret"], d["url"])


def save_config(state_dir: str, cfg: WebhookConfig) -> None:
    os.makedirs(state_dir, exist_ok=True)
    p = _cfg_path(state_dir)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(p, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(dataclasses.asdict(cfg), f, indent=1)
    os.chmod(p, 0o600)


def _default_opener(req, timeout):
    return urllib.request.urlopen(req, timeout=timeout)


def post_webhook(cfg: WebhookConfig, payload: dict, opener=None, now=None, timeout: float = 10) -> int:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ts = int((now or time.time)())
    req = urllib.request.Request(cfg.url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "X-Webhook-Timestamp": str(ts),
        "X-Webhook-Signature-V2": sign_v2(cfg.secret, ts, body),
    })
    try:
        with (opener or _default_opener)(req, timeout) as resp:
            return int(getattr(resp, "status", 200))
    except urllib.error.HTTPError as e:
        return int(e.code)


_SECRET_RE = re.compile(r"^\s*Secret:\s*(\S+)\s*$", re.M)


def parse_subscribe_secret(output: str) -> str | None:
    m = _SECRET_RE.search(output or "")
    return m.group(1) if m else None


def setup_route(state_dir: str, route: str, deliver: str, secret: str | None = None, runner=subprocess.run) -> WebhookConfig:
    hermes = os.environ.get("HERMES_BIN") or shutil.which("hermes") or "hermes"
    argv = [hermes, "webhook", "subscribe", route,
            "--events", "claude_blocked,claude_done", "--skills", "claude-bridge", "--deliver", deliver,
            "--description", "claude-bridge: Claude Code pane became blocked or finished", "--prompt", PROMPT_TEMPLATE]
    if secret:
        argv += ["--secret", secret]
    cp = runner(argv, capture_output=True, text=True)
    if cp.returncode != 0:
        raise hb.BridgeError("hermes webhook subscribe failed (%d): %s" % (cp.returncode, (cp.stderr or cp.stdout).strip()))
    final_secret = secret or parse_subscribe_secret(cp.stdout)
    if not final_secret:
        raise hb.BridgeError("could not read the generated secret from `hermes webhook subscribe` output; re-run with --secret S:\n%s" % cp.stdout)
    cfg = WebhookConfig(route, final_secret, "%s/webhooks/%s" % (WEBHOOK_BASE, route))
    save_config(state_dir, cfg)
    return cfg

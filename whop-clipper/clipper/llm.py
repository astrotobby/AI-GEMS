"""One structured-output call to Claude, through whichever route is paid for.

1. ANTHROPIC_API_KEY  - the Messages API (needs API credit)
2. CLAUDE_CODE_OAUTH_TOKEN - the Claude Code CLI signed in with a Claude Pro/Max plan
   (`claude setup-token`), so scripts and clip picks come out of the subscription instead.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

SYSTEM = "You are a careful short-form video editor and copywriter. Answer only with the requested JSON."


def _api(prompt: str, schema: dict, api_key: str, model: str) -> dict:
    import anthropic

    resp = anthropic.Anthropic(api_key=api_key).beta.messages.create(
        model=model, max_tokens=4000,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        messages=[{"role": "user", "content": prompt}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("model declined")
    return json.loads(next(b.text for b in resp.content if b.type == "text"))


def _cli(prompt: str, schema: dict, token: str, model: str) -> dict:
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("Claude Code CLI is not installed")
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    env["CLAUDE_CODE_OAUTH_TOKEN"] = token  # an API key in the env would win over the plan, so drop it
    proc = subprocess.run(
        [exe, "-p", "--output-format", "json", "--json-schema", json.dumps(schema), "--model", model,
         "--system-prompt", SYSTEM, "--tools", "", "--strict-mcp-config", "--no-session-persistence"],
        input=prompt, capture_output=True, text=True, timeout=300, env=env,
    )
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        raise RuntimeError(f"Claude Code failed: {(proc.stderr or proc.stdout)[-300:]}") from None
    if out.get("is_error") or not isinstance(out.get("structured_output"), dict):
        raise RuntimeError(f"Claude Code: {str(out.get('result') or out.get('subtype'))[:300]}")
    return out["structured_output"]


def structured(prompt: str, schema: dict, api_key: str, model: str, oauth_token: str = "") -> dict:
    """Try the API, then the Pro/Max plan. Raises with every reason if neither works."""
    errors = []
    if api_key:
        try:
            return _api(prompt, schema, api_key, model)
        except Exception as e:  # e.g. no API credit - the plan may still cover it
            errors.append(e)
    if oauth_token:
        try:
            return _cli(prompt, schema, oauth_token, model)
        except Exception as e:
            errors.append(e)
    if not errors:
        raise RuntimeError("no Claude credentials (ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN)")
    raise RuntimeError(" | ".join(str(e) for e in errors))

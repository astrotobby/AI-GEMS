#!/usr/bin/env python3
"""Move YouTube OAuth JSON to/from GitHub Actions encrypted repository secrets."""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = "astrotobby/AI-GEMS"
ROOT = Path(__file__).resolve().parents[2]
TOKEN_DIR = ROOT / "whop-clipper" / "work" / "yt_tokens"
API = "https://api.github.com/repos/" + REPO
CHANNELS = {
    "ASTRO_TOBBY": {
        "label": "Astro Tobby", "id": "UCxndvpcFaVhN1bJxgWIjG_A",
        "file": "ASTRO_TOBBY.json", "env": "YOUTUBE_OAUTH_ASTRO_TOBBY_JSON",
        "secret": "YOUTUBE_OAUTH_ASTRO_TOBBY_JSON",
    },
    "DIJITOL_NOMAD": {
        "label": "DIJITOL NOMAD", "id": "UCE_cklDludc6nkU1_4K6JzA",
        "file": "DIJITOL_NOMAD.json", "env": "YOUTUBE_OAUTH_DIJITOL_NOMAD_JSON",
        "secret": "YOUTUBE_OAUTH_DIJITOL_NOMAD_JSON",
    },
}

class StorageError(RuntimeError):
    pass

def request_json(url, method="GET", payload=None, form=None, headers=None):
    h = {"Accept": "application/json"}
    h.update(headers or {})
    body = None
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        h["Content-Type"] = "application/json"
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raise StorageError("Remote API request failed with HTTP " + str(exc.code)) from None
    except urllib.error.URLError as exc:
        raise StorageError("Remote API connection failed (" + type(exc.reason).__name__ + ")") from None

def clean_credentials(raw, slug):
    spec = CHANNELS[slug]
    if not isinstance(raw, dict):
        raise StorageError("OAuth data for " + spec["label"] + " is not a JSON object.")
    result = {}
    for key in ("client_id", "client_secret", "refresh_token"):
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise StorageError("OAuth data for " + spec["label"] + " is missing " + key + ".")
        result[key] = value.strip()
    if raw.get("channel_id") != spec["id"]:
        raise StorageError("OAuth channel identity does not match " + spec["label"] + ".")
    label = str(raw.get("channel") or "").strip()
    if label and label.casefold() != spec["label"].casefold():
        raise StorageError("OAuth channel label does not match " + spec["label"] + ".")
    result["channel"] = spec["label"]
    result["channel_id"] = spec["id"]
    result["youtube_title"] = str(raw.get("youtube_title") or spec["label"])
    return result

def load_file(slug):
    path = TOKEN_DIR / CHANNELS[slug]["file"]
    if not path.is_file():
        raise StorageError("Cached OAuth file missing for " + CHANNELS[slug]["label"] + ".")
    try:
        return clean_credentials(json.loads(path.read_text(encoding="utf-8")), slug)
    except json.JSONDecodeError:
        raise StorageError("Cached OAuth file is invalid JSON for " + CHANNELS[slug]["label"] + ".") from None

def verify_google_account(credentials, slug):
    _, token = request_json("https://oauth2.googleapis.com/token", method="POST", form={
        "client_id": credentials["client_id"],
        "client_secret": credentials["client_secret"],
        "refresh_token": credentials["refresh_token"],
        "grant_type": "refresh_token",
    })
    access = token.get("access_token")
    if not access:
        raise StorageError("Google rejected the refresh token for " + CHANNELS[slug]["label"] + ".")
    _, info = request_json("https://oauth2.googleapis.com/tokeninfo?" + urllib.parse.urlencode({"access_token": access}))
    scopes = set(str(info.get("scope") or token.get("scope") or "").split())
    if not scopes.intersection({
        "https://www.googleapis.com/auth/youtube",
        "https://www.googleapis.com/auth/youtube.force-ssl",
    }):
        raise StorageError("OAuth grant lacks YouTube management scope for " + CHANNELS[slug]["label"] + ".")
    _, identity = request_json(
        "https://www.googleapis.com/youtube/v3/channels?part=id&mine=true",
        headers={"Authorization": "Bearer " + access},
    )
    ids = {str(item.get("id") or "") for item in identity.get("items", [])}
    if CHANNELS[slug]["id"] not in ids:
        raise StorageError("Google account does not own the configured " + CHANNELS[slug]["label"] + " channel.")

def headers(token):
    return {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "AI-GEMS-YouTube-OAuth-Secret-Migration",
    }

def persist_one(slug, credentials, token, key):
    try:
        from nacl.public import PublicKey, SealedBox
    except ImportError:
        raise StorageError("PyNaCl is missing; install it with python -m pip install pynacl.") from None
    try:
        public_key = base64.b64decode(key["key"], validate=True)
        encrypted = SealedBox(PublicKey(public_key)).encrypt(
            json.dumps(credentials, separators=(",", ":"), sort_keys=True).encode()
        )
    except Exception:
        raise StorageError("Could not encrypt OAuth credentials for GitHub.") from None
    body = {
        "encrypted_value": base64.b64encode(encrypted).decode("ascii"),
        "key_id": key["key_id"],
    }
    spec = CHANNELS[slug]
    status, _ = request_json(
        API + "/actions/secrets/" + spec["secret"],
        method="PUT", payload=body, headers=headers(token),
    )
    if status not in (201, 204):
        raise StorageError("GitHub did not confirm " + spec["secret"] + ".")
    print("ENCRYPTED_SECRET_STORED " + spec["secret"] + " (" + str(status) + ")")

def persist(slugs):
    repo = os.environ.get("GITHUB_REPOSITORY", REPO).strip()
    if repo.casefold() != REPO.casefold():
        raise StorageError("Migration is restricted to astrotobby/AI-GEMS.")
    token = os.environ.get("YOUTUBE_SECRETS_MIGRATION_TOKEN", "").strip()
    if not token:
        raise StorageError(
            "Missing YOUTUBE_SECRETS_MIGRATION_TOKEN. Add a fine-grained token scoped to this repository "
            "with Secrets: write permission, then rerun the migration workflow."
        )
    creds = {slug: load_file(slug) for slug in slugs}
    for slug in slugs:
        verify_google_account(creds[slug], slug)
    _, key = request_json(API + "/actions/secrets/public-key", headers=headers(token))
    for slug in slugs:
        persist_one(slug, creds[slug], token, key)
    print("YOUTUBE_OAUTH_SECRET_MIGRATION_SUCCESS channels=" + str(len(slugs)))

def restore():
    raw = {slug: os.environ.get(spec["env"], "").strip() for slug, spec in CHANNELS.items()}
    present = [slug for slug, value in raw.items() if value]
    if not present:
        print("::warning::Encrypted YouTube secrets not configured yet; legacy cache fallback remains active.")
        return 0
    if len(present) != len(CHANNELS):
        missing = [spec["secret"] for slug, spec in CHANNELS.items() if not raw[slug]]
        raise StorageError("Both channels are required; missing " + ", ".join(missing))
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        TOKEN_DIR.chmod(0o700)
    for slug, value in raw.items():
        try:
            credentials = clean_credentials(json.loads(value), slug)
        except json.JSONDecodeError:
            raise StorageError("Encrypted repository secret contains invalid JSON for " + CHANNELS[slug]["label"] + ".") from None
        path = TOKEN_DIR / CHANNELS[slug]["file"]
        path.write_text(json.dumps(credentials, separators=(",", ":"), sort_keys=True), encoding="utf-8")
        if os.name != "nt":
            path.chmod(0o600)
        print("DURABLE_OAUTH_SECRET_RESTORED " + CHANNELS[slug]["label"])
    print("Both channels are using encrypted repository secrets.")
    return 0

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--restore", action="store_true")
    group.add_argument("--persist-all", action="store_true")
    group.add_argument("--persist-channel", choices=("Astro Tobby", "DIJITOL NOMAD"))
    args = parser.parse_args()
    try:
        if args.restore:
            return restore()
        if args.persist_all:
            return persist(list(CHANNELS))
        slug = "ASTRO_TOBBY" if args.persist_channel == "Astro Tobby" else "DIJITOL_NOMAD"
        return persist([slug])
    except StorageError as exc:
        print("YOUTUBE_OAUTH_SECRET_STORAGE_ERROR: " + str(exc), file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())

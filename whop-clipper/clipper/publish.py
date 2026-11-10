"""Post clips and read their view counts: YouTube Shorts through the YouTube Data API with your own
Google OAuth app, Instagram Reels and TikTok through Composio."""
from __future__ import annotations

import json as _json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


import logging

log = logging.getLogger(__name__)


class PublishError(RuntimeError):
    pass


class UploadedNotPublic(PublishError):
    """The video is on the channel but YouTube did not make it public. It must not be uploaded again."""

    def __init__(self, msg: str, url: str):
        super().__init__(msg)
        self.url = url


def find(obj: Any, *keys: str) -> Any:
    """Depth-first search for the first non-empty value under any of keys (Composio nests responses)."""
    if isinstance(obj, dict):
        for k in keys:
            if obj.get(k) not in (None, "", [], {}):
                return obj[k]
        for v in obj.values():
            hit = find(v, *keys)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = find(v, *keys)
            if hit is not None:
                return hit
    return None


# --- Direct YouTube Data API (own Google OAuth app => own daily quota, not Composio's shared one) ---
TOKEN_DIR = Path("work/yt_tokens")  # minted by `yt_auth.py` (workflow "YouTube auth"); persisted by the run cache


def _slug(target: dict) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in (target.get("channel") or "").upper()).strip("_")


def _norm(x: str) -> str:
    return "".join(ch for ch in str(x).upper() if ch.isalnum())


_WARNED: set = set()
_TOKEN_RE = re.compile(r"1//[0-9A-Za-z_\-]{40,}")


def _extract_token(raw: str, slug: str) -> tuple[str, tuple[str, str] | None]:
    """The refresh token for `slug` out of a secret value, plus the OAuth client stored beside it.

    A plain token is returned as is. If the secret holds pasted JSON (e.g. the whole tokens file),
    the entry whose "channel" matches the slug is used; with no channel information it is only
    accepted when the text contains exactly one token, so one channel's clips can never be uploaded
    to the other channel.
    """
    raw = raw.strip()
    if not raw:
        return "", None
    if not raw.startswith(("{", "[")) and len(raw) < 400 and not any(c.isspace() for c in raw):  # a plain token
        return raw, None
    client = None
    try:
        data = _json.loads(raw)
    except ValueError:
        data = None
    if data is not None:
        top = data if isinstance(data, dict) else {}
        if top.get("client_id") and top.get("client_secret"):
            client = (top["client_id"], top["client_secret"])
        entries = []

        def walk(o):
            if isinstance(o, dict):
                if isinstance(o.get("refresh_token"), str):
                    entries.append((o.get("channel") or o.get("name") or "", o["refresh_token"].strip()))
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)

        walk(data)
        want = _norm(slug)
        for ch, tok in entries:
            if want and ch and _norm(ch) == want:
                return tok, client
        if len(entries) == 1:
            return entries[0][1], client
        return "", client
    found = list(dict.fromkeys(_TOKEN_RE.findall(raw)))
    return (found[0], None) if len(found) == 1 else ("", None)


def _env_refresh_all(slug: str) -> list[tuple[str, tuple[str, str] | None]]:
    """Every refresh token for the channel (with any client stored beside it) found under the secret
    names people use, most specific name first. A stale secret must not hide a good one."""
    flat = slug.replace("_", "")
    names = [f"YOUTUBE_REFRESH_TOKEN_{slug}", f"REFRESH_TOKEN_{slug}", f"REFRESHTOKEN_{flat}",
             f"YOUTUBE_REFRESHTOKEN_{flat}"] if slug else []
    out: list[tuple[str, tuple[str, str] | None]] = []
    for n in names + ["YOUTUBE_REFRESH_TOKEN"]:
        raw = os.environ.get(n, "")
        tok, client = _extract_token(raw, slug)
        if tok:
            if tok not in [t for t, _ in out]:
                out.append((tok, client))
            continue
        if raw.strip() and (n, slug) not in _WARNED:
            _WARNED.add((n, slug))
            log.warning("youtube %s: secret %s is set but holds no usable refresh token for this channel (length=%d, "
                        "looks like %s, tokens found=%d). It should be ONLY the ~103-character token starting 1// "
                        "- or JSON with one entry per channel name.", slug, n, len(raw.strip()),
                        "JSON" if raw.lstrip().startswith(("{", "[")) else "plain text",
                        len(_TOKEN_RE.findall(raw)))
    return out


def _env_refresh(slug: str) -> tuple[str, tuple[str, str] | None]:
    """The first refresh token found for the channel (and any client stored beside it)."""
    found = _env_refresh_all(slug)
    return found[0] if found else ("", None)


def _client_from_env() -> list[tuple[str, str, str]]:
    """Candidate (client_id, client_secret, label) triples from whatever secrets exist, however named.

    A value that is a pasted Google client JSON ({"installed": {...}} / {"web": {...}}) supplies a
    complete pair by itself; plain ids and secrets are then combined, so a mismatched or misnamed
    set still works as long as one combination is the pair that issued the refresh token. The label
    names the secrets involved (never their values) so a failure can be diagnosed from the log.
    """
    ids: list[tuple[str, str]] = []
    secrets_: list[tuple[str, str]] = []
    out: list[tuple[str, str, str]] = []
    names = [("YOUTUBE_CLIENT_ID", 0), ("CLIENT_ID", 0), ("YOUTUBECLIENT_ID", 0),
             ("YOUTUBE_CLIENT_SECRET", 1), ("CLIENT_SECRET", 1), ("YOUTUBECLIENT_SECRET", 1), ("YOUTUBECLIENT_SECTRET", 1)]
    for name, kind in names:
        v = (os.environ.get(name) or "").strip()
        if not v:
            continue
        if v.startswith("{"):
            try:
                c = _json.loads(v)
                c = c.get("installed") or c.get("web") or c
                if c.get("client_id") and c.get("client_secret"):
                    out.append((c["client_id"], c["client_secret"], f"{name}(json)"))
                    ids.append((c["client_id"], f"{name}(json)"))
                    secrets_.append((c["client_secret"], f"{name}(json)"))
            except ValueError:
                pass
            continue
        (secrets_ if kind else ids).append((v, name))
    for i, iname in ids:
        for sec, sname in secrets_:
            out.append((i, sec, f"id={iname} secret={sname}"))
    seen, uniq = set(), []
    for t in out:
        if t[:2] not in seen:
            seen.add(t[:2])
            uniq.append(t)
    return uniq


_WORKING: dict[str, tuple[str, str, str]] = {}


def _yt_creds(target: dict) -> tuple[str, str, str] | None:
    """(client_id, client_secret, refresh_token) for a channel, or None when not configured.

    1. work/yt_tokens/<CHANNEL>.json - written by the "YouTube auth" workflow together with the
       OAuth client that issued the token (a refresh token only works with its own client);
    2. YOUTUBE_REFRESH_TOKEN_<CHANNEL> (else YOUTUBE_REFRESH_TOKEN) + the client secrets found in the
       environment; when several id/secret combinations exist, the first one Google accepts is used.
    """
    slug = _slug(target)
    f = TOKEN_DIR / f"{slug}.json"
    if slug and f.exists():
        try:
            d = _json.loads(f.read_text())
            if d.get("client_id") and d.get("client_secret") and d.get("refresh_token"):
                return d["client_id"], d["client_secret"], d["refresh_token"]
        except ValueError:
            pass
    env_pairs = _client_from_env()
    combos: list[tuple[str, str, str, str]] = []  # (client id, client secret, refresh token, label)
    for n, (refresh, stored_client) in enumerate(_env_refresh_all(slug), 1):
        pairs = list(env_pairs)
        if stored_client:  # the client that issued it, when the secret carried one
            pairs.insert(0, (*stored_client, "stored beside the refresh token"))
        for cid, sec, label in pairs:
            if (cid, sec, refresh) not in [c[:3] for c in combos]:
                combos.append((cid, sec, refresh, f"token #{n} with {label}"))
    if not combos:
        return None
    if len(combos) == 1:
        return combos[0][:3]
    key = slug + ":" + "|".join(dict.fromkeys(c[2][:12] for c in combos))
    if key not in _WORKING:
        for cid, sec, refresh, label in combos:
            try:
                _yt_access_token((cid, sec, refresh))
            except PublishError as e:
                m = re.search(r'"error":\s*"([^"]+)"', str(e))
                log.warning("youtube %s: %s -> %s", slug, label, m.group(1) if m else "rejected")
                continue
            log.info("youtube %s: using %s", slug, label)
            _WORKING[key] = (cid, sec, refresh)
            break
        else:
            for refresh in dict.fromkeys(c[2] for c in combos):
                log.warning("youtube %s: refresh token shape: length=%d, starts with '1//': %s (a Google refresh token "
                            "is ~103 characters and starts with 1//; anything else was pasted into the wrong secret)",
                            slug, len(refresh), refresh.startswith("1//"))
            log.warning("youtube %s: no client/secret combination was accepted with any refresh token "
                        "(invalid_client = wrong client id/secret pair; invalid_grant = the token was issued by "
                        "a different client, was revoked, or expired - tokens of an OAuth app left in 'Testing' "
                        "expire after 7 days)", slug)
            return combos[0][:3]  # let the caller surface Google's error
    return _WORKING[key]


def yt_direct_enabled(target: dict) -> bool:
    return _yt_creds(target) is not None


def _yt_access_token(creds: tuple[str, str, str]) -> str:
    cid, secret, refresh = creds
    body = urllib.parse.urlencode({
        "client_id": cid, "client_secret": secret,
        "refresh_token": refresh, "grant_type": "refresh_token"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=body), timeout=60) as r:
            return _json.load(r)["access_token"]
    except urllib.error.HTTPError as e:
        raise PublishError(f"YouTube token refresh failed: {e.read().decode()[:300]}")


def _yt_direct_upload(creds: tuple[str, str, str], video: Path, title: str, description: str, tags: list[str],
                      privacy: str, category: str) -> tuple[str, str]:
    """Upload and return (video id, the privacy status YouTube actually gave it)."""
    token = _yt_access_token(creds)
    meta = {"snippet": {"title": title[:100], "description": description[:4900], "tags": tags[:30], "categoryId": category},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
    size = video.stat().st_size
    init = urllib.request.Request(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        data=_json.dumps(meta).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
    try:
        with urllib.request.urlopen(init, timeout=120) as r:
            loc = r.headers["Location"]
        put = urllib.request.Request(loc, data=video.read_bytes(), method="PUT",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4"})
        with urllib.request.urlopen(put, timeout=900) as r:
            res = _json.load(r)
    except urllib.error.HTTPError as e:
        raise PublishError(f"YouTube direct upload failed ({e.code}): {e.read().decode()[:400]}")
    vid = res.get("id")
    if not vid:
        raise PublishError("YouTube direct upload returned no video id")
    return vid, str((res.get("status") or {}).get("privacyStatus") or privacy)


def _yt_direct_views(creds: tuple[str, str, str], vid: str) -> int:
    token = _yt_access_token(creds)
    req = urllib.request.Request(f"https://www.googleapis.com/youtube/v3/videos?part=statistics&id={vid}",
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            items = _json.load(r).get("items") or []
    except urllib.error.HTTPError as e:
        raise PublishError(f"YouTube views failed ({e.code}): {e.read().decode()[:300]}")
    return int(((items[0] if items else {}).get("statistics") or {}).get("viewCount") or 0)


class Composio:
    def __init__(self, api_key: str, user_id: str, upload_dirs: list[Path] | None = None):
        if not api_key:
            raise PublishError("COMPOSIO_API_KEY is not set")
        from composio import Composio as SDK

        # Local file paths are uploaded for file fields (videoFile / video_file). Newer SDKs only
        # upload from an allowlist of directories, so allow our clips folder (plus the SDK's own temp).
        dirs = [str(Path(d).resolve()) for d in upload_dirs or []] + [str(Path("~/.composio/temp").expanduser())]
        self.sdk = SDK(api_key=api_key, dangerously_allow_auto_upload_download_files=True, file_upload_dirs=dirs)
        self.user_id = user_id
        self._owner: dict[str, str] = {}  # connected account id -> the Composio user it belongs to

    def accounts(self, toolkit: str) -> list:
        """ACTIVE connected accounts for a toolkit: this user's first, then any other user's in the
        project (a reconnect made from a dashboard or MCP session lands under a different user)."""
        out, ids = [], set()
        for users in ([self.user_id] if self.user_id else [], None):
            accts = self.sdk.connected_accounts.list(user_ids=users or None, toolkit_slugs=[toolkit],
                                                     statuses=["ACTIVE"], account_type="ALL")
            for a in getattr(accts, "items", []) or []:
                if a.id in ids:
                    continue
                ids.add(a.id)
                owner = getattr(a, "user_id", None)
                if owner:
                    self._owner[a.id] = owner
                out.append(a)
        return out

    def account_for(self, toolkit: str) -> str | None:
        accts = self.accounts(toolkit)
        return accts[0].id if accts else None

    def create_own_app_config(self, toolkit: str, client_id: str, client_secret: str,
                              scopes: str = "") -> str:
        """Register your own OAuth app (e.g. your approved TikTok app) as this project's auth config."""
        creds = {"client_id": client_id, "client_secret": client_secret}
        if scopes:
            creds["scopes"] = scopes
        res = self.sdk.client.auth_configs.create(toolkit={"slug": toolkit}, auth_config={
            "type": "use_custom_auth", "auth_scheme": "OAUTH2", "name": f"clipper-{toolkit}",
            "credentials": creds})
        return res.auth_config.id

    def link(self, toolkit: str, auth_config_id: str = "", own_app: tuple[str, ...] = ()) -> str:
        """A Composio Connect link that adds a login for `toolkit` to this project and user. Uses the
        given auth config (e.g. your own Google OAuth app), the newest one for the toolkit, or -
        when there is none and own_app=(client_id, client_secret, scopes) - registers that app."""
        if not auth_config_id:
            configs = getattr(self.sdk.client.auth_configs.list(toolkit_slug=toolkit), "items", []) or []
            if configs:
                auth_config_id = max(configs, key=lambda x: str(getattr(x, "created_at", ""))).id
            elif own_app:
                auth_config_id = self.create_own_app_config(toolkit, *own_app)
            else:
                raise PublishError(f"no {toolkit} auth config in this Composio project - create one first")
        req = self.sdk.connected_accounts.link(user_id=self.user_id or "default",
                                               auth_config_id=auth_config_id, allow_multiple=True)
        return str(req.redirect_url)

    def run(self, slug: str, args: dict, account_id: str | None = None) -> dict:
        # no account_id -> Composio uses the default connected account for that toolkit;
        # an account made under another Composio user must be called as that user
        user = self._owner.get(account_id or "", self.user_id)
        res = self.sdk.tools.execute(slug, args, user_id=user or None,
                                     connected_account_id=account_id or None,
                                     dangerously_skip_version_check=True)
        res = dict(res) if not isinstance(res, dict) else res
        if not res.get("successful", True):
            raise PublishError(f"{slug} failed: {res.get('error')}")
        return res.get("data") or {}


_RESOLVED: dict[str, str] = {}


def target_key(target: dict) -> str:
    name = target.get("channel") or target.get("username") or target.get("account_id") or ""
    return f"{target['platform']}:{name}"


def resolve(c: Composio, target: dict) -> dict:
    """Find the Composio connected account behind a target by YouTube channel title or Instagram /
    TikTok username, so config.toml can say "Astro Tobby" instead of an opaque ca_... id."""
    if target.get("platform") == "youtube":
        return target  # YouTube is direct-API only (your own Google app); never looked up in Composio
    if target.get("account_id"):
        return target
    want = (target.get("channel") or target.get("username") or "").strip().lower()
    if not want:
        return target  # Composio's default account for the toolkit
    key = f"{target['platform']}:{want}"
    if key not in _RESOLVED:
        seen = []
        for a in c.accounts(target["platform"]):
            try:
                if target["platform"] == "youtube":
                    data = c.run("YOUTUBE_GET_CHANNEL_STATISTICS", {"mine": True, "part": "snippet"}, a.id)
                    name = str(find(data, "title") or "")
                elif target["platform"] == "tiktok":
                    name = tiktok_username(c, a.id)
                else:
                    data = c.run("INSTAGRAM_GET_USER_INFO", {"ig_user_id": "me", "fields": "id,username"}, a.id)
                    name = str(find(data, "username") or "")
            except Exception as e:  # keep the reason, e.g. an expired login or API quota
                seen.append(f"{a.id}: {str(e)[:160]}")
                continue
            seen.append(name.strip())
            if want == name.strip().lower() or want in name.strip().lower():
                _RESOLVED[key] = a.id
                break
        else:
            raise PublishError(f"no connected {target['platform']} account named {want!r} (found: {seen})")
    target["account_id"] = _RESOLVED[key]
    return target


def hashtags(tags: list[str]) -> str:
    return " ".join(f"#{t}" for t in tags)


def yt_not_configured(target: dict) -> "PublishError":
    return PublishError(
        f"YouTube channel {target.get('channel') or '?'!r}: no usable direct credentials. Set the secrets "
        f"YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET and YOUTUBE_REFRESH_TOKEN_{_slug(target) or '<CHANNEL>'} "
        "(Composio is deliberately not used for YouTube).")


def youtube(c: Composio, target: dict, video: Path, title: str, caption: str, tags: list[str],
            privacy: str, category: str) -> str:
    """Upload through the YouTube Data API with your own Google OAuth app. No Composio fallback."""
    t = title if "#shorts" in title.lower() else f"{title[:88]} #Shorts"
    creds = _yt_creds(target)
    if not creds:
        raise yt_not_configured(target)
    yt_verify_channel(target, creds)  # a token pasted into the wrong secret must never post to the other channel
    vid, got = _yt_direct_upload(creds, video, t, caption + chr(10) * 2 + hashtags(tags), tags, privacy, category)
    url = "https://youtube.com/shorts/" + vid
    if privacy == "public" and got != "public":
        raise UploadedNotPublic(
            f"uploaded as {url} but YouTube set it to '{got}', not public. A Google Cloud project that has not "
            "passed the YouTube API audit has its API uploads locked to private: make this video public by hand "
            "in YouTube Studio, and request the audit (YouTube API Services - Audit and Quota Extension Form) "
            "so later uploads are public.", url)
    return url


_CHANNEL_OK: dict[tuple, str] = {}


def yt_verify_channel(target: dict, creds: tuple[str, str, str]) -> str:
    """The title of the channel the credentials post to; raises unless it is the configured channel."""
    want = (target.get("channel") or "").strip()
    key = (want, creds)
    if key not in _CHANNEL_OK:
        got = _yt_channel_title(creds)
        if want and _norm(got) != _norm(want):
            raise PublishError(f"the YouTube credentials for {want!r} belong to the channel {got!r} - nothing "
                               f"uploaded. Sign in again and pick {want!r} (YouTube auth workflow), or fix the secret "
                               f"YOUTUBE_REFRESH_TOKEN_{_slug(target)}.")
        _CHANNEL_OK[key] = got
    return _CHANNEL_OK[key]


def yt_channel_title(target: dict) -> str:
    """The channel the stored credentials belong to (used by `check`)."""
    creds = _yt_creds(target)
    if not creds:
        raise yt_not_configured(target)
    return _yt_channel_title(creds)


def _yt_channel_title(creds: tuple[str, str, str]) -> str:
    req = urllib.request.Request("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
                                 headers={"Authorization": "Bearer " + _yt_access_token(creds)})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            items = _json.load(r).get("items") or []
    except urllib.error.HTTPError as e:
        raise PublishError(f"YouTube channel lookup failed ({e.code}): {e.read().decode()[:200]}")
    return str((items[0]["snippet"]["title"]) if items else "(no channel)").strip()


def ig_user_id(c: Composio, target: dict) -> str:
    """Media containers need the numeric IG user id; resolve 'me' once per target."""
    ig = str(target.get("ig_user_id") or "me")
    if ig != "me":
        return ig
    info = c.run("INSTAGRAM_GET_USER_INFO", {"ig_user_id": "me", "fields": "id,username"}, target.get("account_id"))
    resolved = find(info, "id", "user_id")
    if not resolved:
        raise PublishError(f"could not resolve Instagram user id: {str(info)[:300]}")
    target["ig_user_id"] = str(resolved)
    return target["ig_user_id"]


def instagram(c: Composio, target: dict, video: Path, caption: str, tags: list[str]) -> str:
    ig = ig_user_id(c, target)
    container = c.run("INSTAGRAM_POST_IG_USER_MEDIA", {
        "ig_user_id": ig,
        "video_file": str(video),
        "media_type": "REELS",
        "share_to_feed": True,
        "caption": f"{caption}\n\n{hashtags(tags)}",
    }, target.get("account_id"))
    creation_id = find(container, "id", "creation_id")
    if not creation_id:
        raise PublishError(f"Instagram container failed: {str(container)[:300]}")
    pub = c.run("INSTAGRAM_POST_IG_USER_MEDIA_PUBLISH", {
        "ig_user_id": ig, "creation_id": str(creation_id), "max_wait_seconds": 300,
    }, target.get("account_id"))
    media_id = find(pub, "id")
    media = c.run("INSTAGRAM_GET_IG_MEDIA", {"ig_media_id": str(media_id), "fields": "id,permalink"},
                  target.get("account_id"))
    link = find(media, "permalink")
    if not link:
        raise PublishError(f"Instagram published but no permalink: {str(media)[:300]}")
    return link


def tiktok_username(c: Composio, account_id: str | None) -> str:
    data = c.run("TIKTOK_GET_USER_STATS", {"fields": ["username", "display_name"]}, account_id)
    return str(find(data, "username") or "")


TIKTOK_POLL_SECONDS = (5, 10, 20, 30, 30, 30, 30, 30, 30, 30, 30, 30)  # ~5 min, under the 30/min limit


def tiktok(c: Composio, target: dict, video: Path, caption: str, tags: list[str], aigc: bool) -> str:
    acct = target.get("account_id")
    # direct posts must use a privacy level the account offers; a private post is useless for Whop
    info = c.run("TIKTOK_QUERY_CREATOR_INFO", {}, acct)
    options = find(info, "privacy_level_options") or []
    if "PUBLIC_TO_EVERYONE" not in options:
        raise PublishError(f"TikTok only allows {options or 'no'} privacy for this app - public posting needs "
                           "the app's Content Posting API audit in the TikTok developer portal")
    up = c.run("TIKTOK_UPLOAD_VIDEO", {
        "file_to_upload": str(video),
        "caption": f"{caption}\n\n{hashtags(tags)}"[:2200],
        "publish": True,
        "privacy_level": "PUBLIC_TO_EVERYONE",
        "is_aigc": aigc,
    }, acct)
    publish_id = find(up, "publish_id")
    if not publish_id:
        raise PublishError(f"TikTok upload returned no publish_id: {str(up)[:300]}")
    for wait in TIKTOK_POLL_SECONDS:
        time.sleep(wait)
        st = c.run("TIKTOK_FETCH_PUBLISH_STATUS", {"publish_id": str(publish_id)}, acct)
        status = str(find(st, "status") or "")
        if status == "FAILED":
            raise PublishError(f"TikTok rejected the post: {find(st, 'fail_reason') or str(st)[:300]}")
        if status == "PUBLISH_COMPLETE":
            ids = find(st, "publicaly_available_post_id", "publicly_available_post_id") or []
            post_id = ids[0] if isinstance(ids, list) and ids else ids
            if not post_id:
                break  # still in moderation; TikTok assigns the public id later
            user = target.get("username") or tiktok_username(c, acct)
            return f"https://www.tiktok.com/@{user}/video/{post_id}"
    raise PublishError(f"TikTok post {publish_id} not public yet (still processing or in moderation)")


def post(c: Composio, target: dict, video: Path, title: str, caption: str, tags: list[str],
         privacy: str = "public", category: str = "24", aigc: bool = False) -> str:
    target = resolve(c, dict(target))
    platform = target["platform"]
    if platform == "youtube":
        return youtube(c, target, video, title, caption, tags, privacy, category)
    if platform == "instagram":
        return instagram(c, target, video, caption, tags)
    if platform == "tiktok":
        return tiktok(c, target, video, caption, tags, aigc)
    raise PublishError(f"unsupported platform {platform}")


def views(c: Composio, target: dict, url: str) -> int:
    target = resolve(c, dict(target))
    if target["platform"] == "youtube":
        vid = url.rstrip("/").split("/")[-1]
        creds = _yt_creds(target)
        if not creds:
            raise yt_not_configured(target)
        return _yt_direct_views(creds, vid)
    if target["platform"] == "instagram":
        listing = c.run("INSTAGRAM_GET_IG_USER_MEDIA", {
            "ig_user_id": target.get("ig_user_id") or "me", "fields": "id,permalink", "limit": 50,
        }, target.get("account_id"))
        items = find(listing, "data") or []
        media_id = next((m["id"] for m in items if isinstance(m, dict) and m.get("permalink") == url), None)
        if not media_id:
            return 0
        # view counts are an insights metric, not a media field (Business/Creator accounts only)
        data = c.run("INSTAGRAM_GET_IG_MEDIA_INSIGHTS", {"ig_media_id": media_id, "metric": ["views"]},
                     target.get("account_id"))
        return int(find(data, "value") or 0)
    if target["platform"] == "tiktok":
        vid = url.rstrip("/").split("/")[-1]
        listing = c.run("TIKTOK_LIST_VIDEOS", {"max_count": 20}, target.get("account_id"))
        items = find(listing, "videos") or []
        hit = next((v for v in items if isinstance(v, dict) and str(v.get("id")) == vid), None)
        return int((hit or {}).get("view_count") or 0)
    return 0

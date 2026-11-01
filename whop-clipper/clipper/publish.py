"""Post clips and read their view counts through Composio (YouTube Shorts, Instagram Reels,
TikTok)."""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Any

STAGING = Path("~/.composio/temp").expanduser()
QUOTA_RE = re.compile(r"quota|rate.?limit|\b429\b|too many requests|exceeded your", re.I)


class PublishError(RuntimeError):
    pass


class QuotaError(PublishError):
    """The platform's daily limit is used up; retrying before it resets only wastes attempts."""


def is_quota(err: BaseException | str) -> bool:
    return isinstance(err, QuotaError) or bool(QUOTA_RE.search(str(err)))


def stage(video: Path) -> Path:
    """Copy a clip into Composio's own temp folder. Newer SDKs refuse uploads from anywhere
    outside their allowlist, and the SDK's temp dir is always on it."""
    video = Path(video)
    if not video.exists():
        raise PublishError(f"clip file missing: {video}")
    STAGING.mkdir(parents=True, exist_ok=True)
    dst = STAGING / video.name
    if not dst.exists() or dst.stat().st_size != video.stat().st_size:
        shutil.copyfile(video, dst)
    return dst


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


class Composio:
    def __init__(self, api_key: str, user_id: str, upload_dirs: list[Path] | None = None):
        if not api_key:
            raise PublishError("COMPOSIO_API_KEY is not set")
        from composio import Composio as SDK

        # Local file paths are uploaded for file fields (videoFile / video_file). Newer SDKs only
        # upload from an allowlist of directories, so allow our clips folder (plus the SDK's own temp).
        dirs = [str(Path(d).resolve()) for d in upload_dirs or []] + [str(STAGING)]
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

    def link(self, toolkit: str, auth_config_id: str = "") -> str:
        """A Composio Connect link that adds a login for `toolkit` to this project and user. Uses the
        given auth config (e.g. your own Google OAuth app) or the newest one for the toolkit."""
        if not auth_config_id:
            configs = getattr(self.sdk.client.auth_configs.list(toolkit_slug=toolkit), "items", []) or []
            if not configs:
                raise PublishError(f"no {toolkit} auth config in this Composio project - create one first")
            auth_config_id = max(configs, key=lambda x: str(getattr(x, "created_at", ""))).id
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
            msg = f"{slug} failed: {res.get('error')}"
            raise (QuotaError if is_quota(msg) else PublishError)(msg)
        return res.get("data") or {}


# "platform:name" -> connected account id. Pipeline points this at the ledger so lookups survive
# between runs: each YouTube lookup costs API quota, and failed lookups hid quota errors before.
_RESOLVED: dict[str, str] = {}


def use_cache(store: dict[str, str]) -> None:
    global _RESOLVED
    _RESOLVED = store


def forget(target: dict) -> None:
    """Drop a cached account (e.g. after a reconnect the old id stops working)."""
    want = (target.get("channel") or target.get("username") or "").strip().lower()
    _RESOLVED.pop(f"{target['platform']}:{want}", None)


def target_key(target: dict) -> str:
    name = target.get("channel") or target.get("username") or target.get("account_id") or ""
    return f"{target['platform']}:{name}"


def resolve(c: Composio, target: dict) -> dict:
    """Find the Composio connected account behind a target by YouTube channel title or Instagram
    username, so config.toml can say "Astro Tobby" instead of an opaque ca_... id."""
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
                    data = c.run("TIKTOK_QUERY_CREATOR_INFO", {}, a.id)
                    name = str(find(data, "creator_username", "creator_nickname") or "")
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
            msg = f"no connected {target['platform']} account named {want!r} (found: {seen})"
            if seen and all(is_quota(x) for x in seen):
                raise QuotaError(f"{target['platform']} daily API quota used up - could not look up "
                                 f"{want!r}; retrying after the reset. {seen[0][:200]}")
            raise PublishError(msg)
    target["account_id"] = _RESOLVED[key]
    return target


def hashtags(tags: list[str]) -> str:
    return " ".join(f"#{t}" for t in tags)


def youtube(c: Composio, target: dict, video: Path, title: str, caption: str, tags: list[str],
            privacy: str, category: str) -> str:
    t = title if "#shorts" in title.lower() else f"{title[:88]} #Shorts"
    data = c.run("YOUTUBE_MULTIPART_UPLOAD_VIDEO", {
        "title": t[:100],
        "description": f"{caption}\n\n{hashtags(tags)}",
        "tags": tags,
        "categoryId": category,
        "privacyStatus": privacy,
        "videoFile": str(stage(video)),
    }, target.get("account_id"))
    vid = find(data, "id", "videoId", "video_id")
    if not isinstance(vid, str):
        raise PublishError(f"YouTube upload returned no video id: {str(data)[:300]}")
    return f"https://youtube.com/shorts/{vid}"


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
        "video_file": str(stage(video)),
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


def tiktok(c: Composio, target: dict, video: Path, caption: str, tags: list[str], ai: bool = False) -> str:
    """Direct-post a video. TikTok only lets audited apps post publicly; a private post earns no
    views, so refuse instead of silently posting SELF_ONLY."""
    acct = target.get("account_id")
    info = c.run("TIKTOK_QUERY_CREATOR_INFO", {}, acct)
    options = find(info, "privacy_level_options") or []
    if "PUBLIC_TO_EVERYONE" not in options:
        raise PublishError(f"TikTok only allows {options or 'private'} posts for this connection (the "
                           "Composio TikTok app is not audited for public posting) - post manually or use "
                           "your own audited TikTok app in Composio")
    user = str(find(info, "creator_username") or target.get("username") or "")
    up = c.run("TIKTOK_UPLOAD_VIDEO", {
        "file_to_upload": str(stage(video)),
        "caption": f"{caption}\n\n{hashtags(tags)}"[:2200],
        "publish": True,
        "privacy_level": "PUBLIC_TO_EVERYONE",
        "is_aigc": ai,
    }, acct)
    publish_id = find(up, "publish_id")
    if not publish_id:
        raise PublishError(f"TikTok upload returned no publish_id: {str(up)[:300]}")
    delay = 5
    for _ in range(12):  # TikTok processes asynchronously; about 4 minutes in total
        time.sleep(delay)
        st = c.run("TIKTOK_FETCH_PUBLISH_STATUS", {"publish_id": str(publish_id)}, acct)
        ids = find(st, "publicaly_available_post_id") or []
        status = str(find(st, "status") or "")
        if ids:
            return f"https://www.tiktok.com/@{user}/video/{ids[0]}"
        if "FAIL" in status.upper():
            raise PublishError(f"TikTok publish failed: {find(st, 'fail_reason') or status}")
        delay = min(delay * 2, 30)
    raise PublishError(f"TikTok still processing publish {publish_id}; check the app")


def post(c: Composio, target: dict, video: Path, title: str, caption: str, tags: list[str],
         privacy: str = "public", category: str = "24", ai: bool = False) -> str:
    target = resolve(c, dict(target))
    platform = target["platform"]
    try:
        if platform == "youtube":
            return youtube(c, target, video, title, caption, tags, privacy, category)
        if platform == "instagram":
            return instagram(c, target, video, caption, tags)
        if platform == "tiktok":
            return tiktok(c, target, video, caption, tags, ai)
    except PublishError as e:
        if not is_quota(e) and not str(e).startswith("clip file missing"):
            forget(target)  # maybe a stale cached account; look it up afresh next time
        raise
    raise PublishError(f"unsupported platform {platform}")


def views(c: Composio, target: dict, url: str) -> int:
    target = resolve(c, dict(target))
    if target["platform"] == "youtube":
        vid = url.rstrip("/").split("/")[-1]
        data = c.run("YOUTUBE_GET_VIDEO_DETAILS_BATCH", {"id": [vid], "parts": ["statistics"]},
                     target.get("account_id"))
        return int(find(data, "viewCount") or 0)
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
        cursor = None
        for _ in range(5):  # newest first, 20 per page; clips are tracked for 14 days at most
            args = {"max_count": 20} | ({"cursor": cursor} if cursor else {})
            data = c.run("TIKTOK_LIST_VIDEOS", args, target.get("account_id"))
            for v in find(data, "videos") or []:
                if isinstance(v, dict) and str(v.get("id")) == vid:
                    return int(v.get("view_count") or 0)
            cursor = find(data, "cursor")
            if not find(data, "has_more") or not cursor:
                break
        return 0
    return 0

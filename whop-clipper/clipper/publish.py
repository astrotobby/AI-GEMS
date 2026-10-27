"""Post clips and read their view counts through Composio (YouTube Shorts, Instagram Reels)."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any


class PublishError(RuntimeError):
    pass


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
            raise PublishError(f"{slug} failed: {res.get('error')}")
        return res.get("data") or {}


_RESOLVED: dict[str, str] = {}


def target_key(target: dict) -> str:
    name = target.get("channel") or target.get("username") or target.get("account_id") or ""
    return f"{target['platform']}:{name}"


def resolve(c: Composio, target: dict) -> dict:
    """Find the Composio connected account behind a target by YouTube channel title or Instagram /
    TikTok username, so config.toml can say "Astro Tobby" instead of an opaque ca_... id."""
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


def youtube(c: Composio, target: dict, video: Path, title: str, caption: str, tags: list[str],
            privacy: str, category: str) -> str:
    t = title if "#shorts" in title.lower() else f"{title[:88]} #Shorts"
    data = c.run("YOUTUBE_MULTIPART_UPLOAD_VIDEO", {
        "title": t[:100],
        "description": f"{caption}\n\n{hashtags(tags)}",
        "tags": tags,
        "categoryId": category,
        "privacyStatus": privacy,
        "videoFile": str(video),
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
        listing = c.run("TIKTOK_LIST_VIDEOS", {"max_count": 20}, target.get("account_id"))
        items = find(listing, "videos") or []
        hit = next((v for v in items if isinstance(v, dict) and str(v.get("id")) == vid), None)
        return int((hit or {}).get("view_count") or 0)
    return 0

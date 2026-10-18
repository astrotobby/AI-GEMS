"""Post clips and read their view counts through Composio (YouTube Shorts, Instagram Reels)."""
from __future__ import annotations

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

    def account_for(self, toolkit: str) -> str | None:
        """An ACTIVE connected account for a toolkit: this user's first, else any in the project
        (connections made from a different Composio user/session still work)."""
        for users in ([self.user_id] if self.user_id else [], None):
            accts = self.sdk.connected_accounts.list(user_ids=users or None, toolkit_slugs=[toolkit],
                                                     statuses=["ACTIVE"], account_type="ALL")
            items = getattr(accts, "items", []) or []
            if items:
                return items[0].id
        return None

    def run(self, slug: str, args: dict, account_id: str | None = None) -> dict:
        # no account_id -> Composio uses the default connected account for that toolkit
        res = self.sdk.tools.execute(slug, args, user_id=self.user_id or None,
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
    """Find the Composio connected account behind a target by YouTube channel title or Instagram
    username, so config.toml can say "Astro Tobby" instead of an opaque ca_... id."""
    if target.get("account_id"):
        return target
    want = (target.get("channel") or target.get("username") or "").strip().lower()
    if not want:
        return target  # Composio's default account for the toolkit
    key = f"{target['platform']}:{want}"
    if key not in _RESOLVED:
        accts = c.sdk.connected_accounts.list(
            user_ids=[c.user_id] if c.user_id else None,
            toolkit_slugs=[target["platform"]], statuses=["ACTIVE"])
        seen = []
        for a in getattr(accts, "items", []) or []:
            try:
                if target["platform"] == "youtube":
                    data = c.run("YOUTUBE_GET_CHANNEL_STATISTICS", {"mine": True, "part": "snippet"}, a.id)
                    name = str(find(data, "title") or "")
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


def post(c: Composio, target: dict, video: Path, title: str, caption: str, tags: list[str],
         privacy: str = "public", category: str = "24") -> str:
    target = resolve(c, dict(target))
    platform = target["platform"]
    if platform == "youtube":
        return youtube(c, target, video, title, caption, tags, privacy, category)
    if platform == "instagram":
        return instagram(c, target, video, caption, tags)
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
    return 0

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
    def __init__(self, api_key: str, user_id: str):
        if not api_key:
            raise PublishError("COMPOSIO_API_KEY is not set")
        from composio import Composio as SDK

        # auto-upload lets us pass a local file path for file fields (videoFile / video_file)
        self.sdk = SDK(api_key=api_key, dangerously_allow_auto_upload_download_files=True)
        self.user_id = user_id

    def run(self, slug: str, args: dict, account_id: str | None = None) -> dict:
        res = self.sdk.tools.execute(slug, args, user_id=self.user_id or None,
                                     connected_account_id=account_id,
                                     dangerously_skip_version_check=True)
        res = dict(res) if not isinstance(res, dict) else res
        if not res.get("successful", True):
            raise PublishError(f"{slug} failed: {res.get('error')}")
        return res.get("data") or {}


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


def instagram(c: Composio, target: dict, video: Path, caption: str, tags: list[str]) -> str:
    ig = target.get("ig_user_id") or "me"
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
    platform = target["platform"]
    if platform == "youtube":
        return youtube(c, target, video, title, caption, tags, privacy, category)
    if platform == "instagram":
        return instagram(c, target, video, caption, tags)
    raise PublishError(f"unsupported platform {platform}")


def views(c: Composio, target: dict, url: str) -> int:
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
        data = c.run("INSTAGRAM_GET_IG_MEDIA", {"ig_media_id": media_id,
                                                "fields": "id,view_count,total_views_count"},
                     target.get("account_id"))
        return int(find(data, "total_views_count", "view_count") or 0)
    return 0

"""Mint a YouTube refresh token from inside GitHub Actions (Google "device" sign-in, no local setup).

    python yt_auth.py "Astro Tobby"

Sends you a sign-in link + short code on Telegram (and prints them to the log if Telegram is not
configured). Open the link on your phone, sign in, pick the channel, approve. The token is checked
(it must belong to the channel you asked for) and stored, with the OAuth client that issued it, in
work/yt_tokens/<CHANNEL>.json - which the workflow cache keeps for the clipper. The token itself is
never printed. Standard library only.

The OAuth client must be of type "TVs and Limited Input devices" (Google's device flow); desktop /
web clients are rejected by Google, in which case the next candidate client is tried.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SCOPE = "https://www.googleapis.com/auth/youtube"  # upload + read statistics; allowed for device sign-in
OUT = Path("work/yt_tokens")


def post(url: str, data: dict) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=urllib.parse.urlencode(data).encode()), timeout=60) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except ValueError:
            return e.code, {}


def candidates(channel: str) -> list[tuple[str, str, str]]:
    """OAuth client pairs to try, most likely first for this channel: (label, id, secret)."""
    env = os.environ.get
    pairs = {
        "CLIENT_ID/CLIENT_SECRET": (env("CLIENT_ID", ""), env("CLIENT_SECRET", "")),
        "YOUTUBECLIENT_ID/YOUTUBECLIENT_SECTRET": (env("YOUTUBECLIENT_ID", ""),
                                                   env("YOUTUBECLIENT_SECTRET", "") or env("YOUTUBECLIENT_SECRET", "")),
        "YOUTUBE_CLIENT_ID/YOUTUBE_CLIENT_SECRET": (env("YOUTUBE_CLIENT_ID", ""), env("YOUTUBE_CLIENT_SECRET", "")),
    }
    order = list(pairs)
    if "nomad" in channel.lower():
        order = [order[1], order[0], order[2]]
    return [(k, *pairs[k]) for k in order if all(pairs[k])]


def telegram(text: str) -> bool:
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (tok and chat):
        return False
    code, _ = post(f"https://api.telegram.org/bot{tok}/sendMessage", {"chat_id": chat, "text": text})
    return code == 200


def main() -> int:
    channel = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if not channel:
        print(__doc__)
        return 2
    slug = "".join(ch if ch.isalnum() else "_" for ch in channel.upper()).strip("_")
    for label, cid, secret in candidates(channel):
        code, dev = post("https://oauth2.googleapis.com/device/code", {"client_id": cid, "scope": SCOPE})
        if code != 200:
            print(f"client {label}: device sign-in refused ({code} {dev.get('error')}: "
                  f"{dev.get('error_description', '')}) - trying next")
            continue
        msg = (f"YouTube sign-in for channel \"{channel}\":\n1) open {dev['verification_url']}\n"
               f"2) enter code {dev['user_code']}\n3) sign in, choose the \"{channel}\" channel, approve.\n"
               f"Valid ~{dev.get('expires_in', 1800) // 60} min.")
        if telegram(msg):
            print(f"Sign-in link + code sent to Telegram (client {label}). Waiting for approval...")
        else:
            print("Telegram not configured - do this now:\n" + msg)
        deadline = time.time() + int(dev.get("expires_in", 1800))
        interval = int(dev.get("interval", 5))
        tok: dict = {}
        while time.time() < deadline:
            time.sleep(interval)
            code, tok = post("https://oauth2.googleapis.com/token", {
                "client_id": cid, "client_secret": secret, "device_code": dev["device_code"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
            err = tok.get("error")
            if code == 200 and tok.get("refresh_token"):
                break
            if err == "slow_down":
                interval += 5
            elif err not in (None, "authorization_pending"):
                print("sign-in failed:", err, tok.get("error_description", ""))
                return 1
        else:
            print("timed out waiting for approval - run the workflow again")
            return 1
        req = urllib.request.Request("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
                                     headers={"Authorization": "Bearer " + tok["access_token"]})
        with urllib.request.urlopen(req, timeout=60) as r:
            titles = [i["snippet"]["title"] for i in json.load(r).get("items", [])]
        if not any(channel.lower() in t.lower() or t.lower() in channel.lower() for t in titles):
            print(f"signed in as {titles}, not \"{channel}\" - nothing saved. Run again and pick the right channel.")
            return 1
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{slug}.json").write_text(json.dumps(
            {"client_id": cid, "client_secret": secret, "refresh_token": tok["refresh_token"], "channel": titles[0]}))
        print(f"OK: saved credentials for {titles[0]!r} (client {label}). The clipper uses them from the next run.")
        telegram(f"YouTube \"{channel}\" connected. The clipper can upload now.")
        return 0
    print("No OAuth client accepted device sign-in. In Google Cloud > APIs & Services > Credentials create a client "
          "of type \"TVs and Limited Input devices\" and save its ID/secret as CLIENT_ID / CLIENT_SECRET.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

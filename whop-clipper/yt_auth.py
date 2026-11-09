"""Mint a YouTube refresh token from inside GitHub Actions - no local setup, works with a normal
"Desktop app" OAuth client (the kind you already have).

Step 1 - get a sign-in link:      python yt_auth.py "Astro Tobby"
Step 2 - hand back the result:    python yt_auth.py "Astro Tobby" "<URL your browser ended up on>"

Step 1 sends you a Google sign-in link (Telegram, and the log). Open it, sign in, pick the channel,
approve. Your browser then lands on a http://localhost/?code=... page that says it cannot connect -
that is expected. Copy that whole address and give it to step 2 (the workflow's "code" box).
The token is checked (it must belong to the channel you asked for) and stored, with the OAuth
client that issued it, in work/yt_tokens/<CHANNEL>.json, which the run cache keeps for the clipper.
The token itself is never printed. Standard library only.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SCOPE = "https://www.googleapis.com/auth/youtube"  # upload + read statistics
REDIRECT = "http://localhost"
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
    """OAuth client pairs worth trying, most likely first for this channel: (label, id, secret)."""
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


def valid_client(cid: str, secret: str) -> bool:
    """Google answers a bogus code with invalid_grant for a real client and invalid_client otherwise."""
    _, r = post("https://oauth2.googleapis.com/token", {
        "client_id": cid, "client_secret": secret, "code": "x", "redirect_uri": REDIRECT, "grant_type": "authorization_code"})
    return r.get("error") != "invalid_client"


def pick_client(channel: str) -> tuple[str, str, str] | None:
    for label, cid, secret in candidates(channel):
        if valid_client(cid, secret):
            return label, cid, secret
        print(f"client {label}: rejected by Google (wrong id/secret) - skipping")
    return None


def telegram(text: str) -> bool:
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (tok and chat):
        return False
    code, _ = post(f"https://api.telegram.org/bot{tok}/sendMessage", {"chat_id": chat, "text": text})
    return code == 200


def main() -> int:
    channel = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    given = (sys.argv[2] if len(sys.argv) > 2 else "").strip()
    if not channel:
        print(__doc__)
        return 2
    slug = "".join(ch if ch.isalnum() else "_" for ch in channel.upper()).strip("_")
    picked = pick_client(channel)
    if not picked:
        print("No usable OAuth client. Save a Google OAuth client (type Desktop app) as CLIENT_ID / CLIENT_SECRET.")
        return 1
    label, cid, secret = picked

    if not given:  # step 1: the sign-in link
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
            "client_id": cid, "redirect_uri": REDIRECT, "response_type": "code", "scope": SCOPE,
            "access_type": "offline", "prompt": "consent select_account"})
        msg = (f"YouTube sign-in for \"{channel}\":\n1) open this link and sign in as the account that owns the channel, "
               f"choosing \"{channel}\" if asked\n{url}\n2) approve. The next page says it can't connect to localhost - "
               f"that is expected.\n3) copy that page's full address and run the \"YouTube auth\" workflow again, "
               f"pasting it in the \"code\" box. Do it within ~10 minutes.")
        print(f"using client {label}")
        print("Sign-in link sent to Telegram." if telegram(msg) else msg)
        return 0

    # step 2: exchange what the browser ended up on
    q = urllib.parse.parse_qs(urllib.parse.urlparse(given).query)
    code = q.get("code", [given])[0] if "code" in q or "://" in given else given
    status, tok = post("https://oauth2.googleapis.com/token", {
        "client_id": cid, "client_secret": secret, "code": code, "redirect_uri": REDIRECT, "grant_type": "authorization_code"})
    if status != 200 or not tok.get("refresh_token"):
        print("exchange failed:", tok.get("error"), tok.get("error_description", ""),
              "- the code is single-use and expires in minutes; run step 1 again for a fresh link")
        return 1
    req = urllib.request.Request("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
                                 headers={"Authorization": "Bearer " + tok["access_token"]})
    with urllib.request.urlopen(req, timeout=60) as r:
        titles = [i["snippet"]["title"] for i in json.load(r).get("items", [])]
    if not any(channel.lower() in t.lower().strip() or t.lower().strip() in channel.lower() for t in titles):
        print(f"signed in as {titles}, not \"{channel}\" - nothing saved. Start again and pick the right channel.")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{slug}.json").write_text(json.dumps(
        {"client_id": cid, "client_secret": secret, "refresh_token": tok["refresh_token"], "channel": titles[0]}))
    print(f"OK: saved credentials for {titles[0]!r} (client {label}). The clipper uses them from the next run.")
    telegram(f"YouTube \"{channel}\" connected. The clipper can upload now.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

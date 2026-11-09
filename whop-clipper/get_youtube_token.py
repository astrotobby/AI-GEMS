"""One-time helper: mint a YouTube refresh token for one channel (run on your own computer).

    python whop-clipper/get_youtube_token.py <CLIENT_ID> <CLIENT_SECRET>

A browser opens; sign in and pick the channel you want (e.g. "Astro Tobby"), approve, and the
refresh token is printed. Save it as the GitHub secret YOUTUBE_REFRESH_TOKEN_ASTRO_TOBBY
(then repeat, choosing "DIJITOL NOMAD", for YOUTUBE_REFRESH_TOKEN_DIJITOL_NOMAD).

Your Google OAuth client must be type "Desktop app" (or have http://localhost:8765/ as an
authorised redirect URI), and your Google account must be a test user while the app is in
"Testing" status. Standard library only.
"""
import http.server
import json
import sys
import urllib.parse
import urllib.request
import webbrowser

PORT = 8765
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    cid, secret = sys.argv[1:3]
    redirect = f"http://localhost:{PORT}/"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent select_account"})
    got: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            got.update(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query))
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Done - you can close this tab and return to the terminal.")

        def log_message(self, *a):
            pass

    print("Opening browser... if it does not open, visit:\n" + url)
    webbrowser.open(url)
    srv = http.server.HTTPServer(("localhost", PORT), H)
    while "code" not in got and "error" not in got:
        srv.handle_request()
    if "error" in got:
        sys.exit("Authorisation failed: " + got["error"][0])
    body = urllib.parse.urlencode({"code": got["code"][0], "client_id": cid, "client_secret": secret,
                                   "redirect_uri": redirect, "grant_type": "authorization_code"}).encode()
    with urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=body)) as r:
        tok = json.load(r)
    if not tok.get("refresh_token"):
        sys.exit("No refresh token returned - revoke the app at myaccount.google.com/permissions and retry.")
    print("\nREFRESH TOKEN (store as a GitHub secret, never share it):\n" + tok["refresh_token"])


if __name__ == "__main__":
    main()

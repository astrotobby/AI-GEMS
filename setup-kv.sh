#!/usr/bin/env bash
# setup-kv.sh — creates all 5 KV namespaces and prints the ids for wrangler.toml.
# Run:  bash setup-kv.sh   (after: npm i  &&  npx wrangler login)
set -e
for b in MZ_SESSIONS MZ_LEADS MZ_CARTS MZ_VIDEOS MZ_ANALYTICS; do
  echo ""
  echo "--- $b ---"
  npx wrangler kv namespace create "$b"
done
echo ""
echo "Copy each 'id' value above into the matching binding in wrangler.toml."

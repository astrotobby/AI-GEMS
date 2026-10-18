# Whop connector task

Instructions for the scheduled Claude task that does the Whop half of the clipper with the
owner's **Whop connector** (signed in as the owner, so it can submit; GitHub Actions can't).
It runs shortly before each GitHub Actions run and only ever talks to Whop and this repo.

Repository: `astrotobby/AI-GEMS`, default branch `main`, files under `whop-clipper/state/`.

## 1. Refresh the campaign snapshot

1. List open bounties workable from Kenya with the Whop connector's bounty-list tool:
   `status=open`, `country=KE`, `first=100`, following `page_info.end_cursor` until
   `has_next_page` is false.
2. Write `whop-clipper/state/campaigns.json`:
   ```json
   {"fetched_at": "<UTC ISO-8601 now>", "country": "KE", "data": [ <bounty objects> ]}
   ```
   Keep each bounty object as returned, minus the `poster` and `funding_account` fields.
   The pipeline refuses snapshots older than 12 hours, so always rewrite `fetched_at`.

## 2. Submit queued jobs

Read `whop-clipper/state/ledger.json`. For every job with `"status": "awaiting_submission"`:

1. Read the bounty with the connector's get-bounty tool. If it is no longer `open` or has
   `spots_remaining` 0, set the job's status to `skipped` with `"reason": "closed before submission"`.
2. Otherwise create the submission with the connector's create-bounty-submission tool:
   `bounty_id` = `job.submission.bounty_id`, deliverable `urls` = `job.submission.urls`,
   `caption` = `job.submission.caption`, and `idempotency_key` = `submit-<job.id>` so a retry
   can never double-submit.
3. Set the job's status to `submitted`, record `submission_id` (the returned `btys_...` id),
   set `updated` to now (UTC ISO-8601), and append
   `{"at": <now>, "from": "awaiting_submission", "to": "submitted"}` to its `history`.
   `updated` matters: when GitHub Actions and this task both touched the ledger, the copy of each
   job with the newer `updated` wins.

Never submit jobs in any other status. In particular, `needs_livestream` jobs are for the owner
to submit with a livestream, and must not be submitted without one.

## 3. Commit

Commit only `whop-clipper/state/campaigns.json` and `whop-clipper/state/ledger.json` to `main`
with the message `whop connector: refresh campaigns[, submit N]` and push. If the push is
rejected because Actions committed meanwhile, fetch, re-apply only your job changes to the
fresh ledger (never revert jobs you did not touch), and push again.
If nothing changed except `fetched_at`, still commit (the pipeline needs a fresh timestamp).

## 4. Report

If anything was submitted or skipped, send one short Telegram summary through the clipper's
own notifier (or say so in the session). Stay silent otherwise.

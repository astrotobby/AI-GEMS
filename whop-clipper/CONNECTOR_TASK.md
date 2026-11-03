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

Read `whop-clipper/state/ledger.json`. Jobs in `awaiting_submission` and `needs_livestream`
carry a `submission` object: `bounty_id`, `urls`, `caption`, and `clip_url` (a public download
of the rendered clip, may be missing or `null` on older jobs).

**Attaching the clip.** When `submission.clip_url` is set: download it, create a file with the
connector's create-file tool (`filename` = `<job.id>.mp4`, `idempotency_key` = `clip-<job.id>`),
PUT the bytes to the returned `upload_url`, and wait until the file's status is `ready` before
using its `file_...` id. If the download 404s (the workflow has not published it yet), skip the
job this run and try again next run. Without a `clip_url`, submit links only.

### 2a. `awaiting_submission` (no livestream needed)

1. Read the bounty with the connector's get-bounty tool. If it is no longer `open` or has
   `spots_remaining` 0, set the job's status to `skipped` with `"reason": "closed before submission"`.
2. Otherwise create the submission with the connector's create-bounty-submission tool:
   `bounty_id` = `job.submission.bounty_id`, deliverable `urls` = `job.submission.urls`,
   `file_ids` = the clip's file id (if any), `caption` = `job.submission.caption`, and
   `idempotency_key` = `submit-<job.id>` so a retry can never double-submit.
3. Mark it submitted (see "Marking a job" below).

### 2b. `needs_livestream` (the owner streams, you attach and submit)

The proof livestream can only be done by the owner in the Whop app; the API cannot start or fake
one. These jobs wait until the owner has streamed:

1. List the owner's submissions on `job.submission.bounty_id` with the connector's
   list-bounty-submissions tool.
2. If there is none, do nothing (the owner has not streamed yet). If one is `approved`, `denied`
   or already has the job's URLs attached, just mark the job submitted with that id.
3. If an attempt exists whose proof livestream has **ended** (read it with the
   retrieve-bounty-submission tool; `latest_proof_livestream_feed.ended_at` is set), submit it
   with the connector's submit-bounty-submission tool: `id` = the `btys_...` id, deliverable
   `urls` = `job.submission.urls`, `file_ids` = the clip's file id, `caption` =
   `job.submission.caption`, `idempotency_key` = `submit-<job.id>`. (If the attempt already went
   to review when the stream ended, this attaches the deliverable to it, which is what we want.)
   A stream that is still live: leave it for the next run.
4. Mark it submitted.

Never create a new submission for a `needs_livestream` job, and never submit one whose
livestream has not ended: without the owner's real livestream it would be denied.

### Marking a job

Set the job's status to `submitted`, record `submission_id` (the `btys_...` id), set `updated` to
now (UTC ISO-8601), and append `{"at": <now>, "from": <old status>, "to": "submitted"}` to its
`history`. `updated` matters: when GitHub Actions and this task both touched the ledger, the copy
of each job with the newer `updated` wins.

Never submit jobs in any other status.

## 3. Commit

Commit only `whop-clipper/state/campaigns.json` and `whop-clipper/state/ledger.json` to `main`
with the message `whop connector: refresh campaigns[, submit N]` and push. If the push is
rejected because Actions committed meanwhile, fetch, re-apply only your job changes to the
fresh ledger (never revert jobs you did not touch), and push again.
If nothing changed except `fetched_at`, still commit (the pipeline needs a fresh timestamp).

## 4. Report

If anything was submitted or skipped, send one short Telegram summary through the clipper's
own notifier (or say so in the session). Stay silent otherwise.

# Required Whop MCP connector runner

This is the Whop half of the end-to-end clipper and MUST run automatically before the scheduled
GitHub Actions clipper cycles. GitHub Actions' saved WHOP_TOKEN returned 401 Authentication
failed, so direct API mode is disabled. Use the authenticated Whop MCP session in local Codex
(the official hosted endpoint https://mcp.whop.com/mcp; OAuth was completed) to act as the owner.

The runner refreshes the Kenya campaign snapshot, submits only jobs explicitly queued in the ledger,
attaches the public rendered MP4 and live post links, verifies each result, and commits/pushes the
campaign snapshot and ledger. Never mark a job submitted without a confirmed Whop response.


Repository: `astrotobby/AI-GEMS`, default branch `main`, files under `whop-clipper/state/`.

## 1. Refresh the campaign snapshot

1. List open bounties workable from Kenya with the Whop connector's bounty-list tool:
   `status=open`, `country=KE`, `first=100`, following `page_info.end_cursor` until
   `has_next_page` is false. If the connector call errors, authorization is expired, or pagination
   cannot complete, **do not claim a refresh succeeded** and do not overwrite the last good snapshot.
   Report the exact connector error and the last known `fetched_at`; the GitHub workflow now fails
   visibly when this snapshot is stale instead of appearing healthy.
2. Write `whop-clipper/state/campaigns.json`:
   ```json
   {"fetched_at": "<UTC ISO-8601 now>", "country": "KE", "data": [ <bounty objects> ]}
   ```
   Keep each bounty object as returned, minus the `poster` and `funding_account` fields.
   The pipeline refuses snapshots older than 12 hours, so always rewrite `fetched_at`.

## 2. Submit queued jobs

Read `whop-clipper/state/ledger.json`. Jobs in awaiting_submission and needs_livestream carry a submission object with bounty_id,
urls, caption, clip_url, clip_filename and source timestamps. The pipeline now fills these for
both statuses. For older jobs without clip_url, derive the release asset URL as:
https://github.com/astrotobby/AI-GEMS/releases/download/clips/<job.id>.mp4

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

Never create a new submission for a needs_livestream job, and never submit one whose
livestream has not ended: without the owner's real livestream it would be denied. This is the
only remaining human gate. After the owner completes the genuine stream, attach and submit on
the next connector run; do not require a local "done" command.

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

## 4. Verify and report

After the commit/push, re-read the committed snapshot and confirm `fetched_at` is the current UTC
run time, `country` is `KE`, and the `data` array reflects the completed pagination. Confirm the
commit reached `main`. If any check fails, explicitly report **REFRESH FAILED** with the exact
reason and last successful timestamp; never report success just because the file was written locally.

If anything was submitted or skipped, send one short Telegram summary through the clipper's
own notifier (or say so in the session). Stay silent otherwise.

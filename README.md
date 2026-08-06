# ACF At-Camp Dashboard (auto-updating, anonymized)

A self-refreshing TB ACF dashboard. A scheduled job pulls the CommCare feeds,
**anonymizes** them (no names, no IDs, no CommCare key), and publishes `data.json`.
The page (`index.html`) reads `data.json`, so visitors just open the URL and
refresh — no login, no downloads, no credentials.

```
CommCare Excel Dashboard feeds ──(API key, GitHub Secrets)──► GitHub Actions job
        └─ joins on caseid, computes TAT, drops caseid + all PII ─► data.json ─► index.html
```

## Files
- `index.html` — the dashboard shell (fetches `data.json` on load, cache-busted).
- `data.json` — anonymized data `{generated, count, records}`, refreshed nightly.
- `scripts/commcare_pull_anonymize.py` — the pull + anonymize job.
- `.github/workflows/update-data.yml` — nightly schedule + manual "Run workflow".
- `requirements.txt` — Python deps for the job.

## One-time setup

1. **Create the repo** and upload these files (keep the folder structure).

2. **Add the six secrets.** Repo → *Settings → Secrets and variables → Actions → New repository secret*:
   - `COMMCARE_EMAIL` — your CommCare login email
   - `COMMCARE_API_KEY` — from CommCare *My Account Settings → API Keys*
   - `CASE_FEED_URL`, `SAMPLE_FEED_URL`, `PCR_FEED_URL`, `NAAT_FEED_URL` — the four
     "Copy Dashboard Feed Link" URLs (case feed + the 3 lab-form feeds)

   Secrets are encrypted and never appear in the page, the repo files, or logs.

3. **Enable Pages.** *Settings → Pages → Build and deployment → Deploy from a branch →
   `main` / root*. Your URL will be `https://<user>.github.io/<repo>/`.

4. **First run.** *Actions → Update dashboard data → Run workflow.* It pulls the feeds,
   writes `data.json`, and commits it. Open your Pages URL and refresh.

After this, the job runs itself nightly; visitors just refresh the page.

## Adjusting field names
Dashboard feeds emit whatever *display* column names you set in "Edit Columns".
Run the script once locally with `VERIFY=1` to print the columns each feed returns,
and reconcile the `*_COLS` maps at the top of `scripts/commcare_pull_anonymize.py`
if any names differ. (In GitHub Actions you can see the same by adding `VERIFY: '1'`
to the job's `env:` block temporarily.)

## IMPORTANT — repository visibility (data governance)
`data.json` is anonymized (no names/IDs/key), but it is still **patient-level rows**.
- **Public repo** → free GitHub Pages, but `data.json` is world-readable.
- **Private repo** → Pages requires a paid plan (Pro/Team/Enterprise) and can be
  access-restricted to org members.

Choose per CHAI/WJCF data policy for where de-identified patient data may live. Do NOT
put the full (PII) export in this repo — the internal anomaly follow-up list stays
in a separate, staff-only location.

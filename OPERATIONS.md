# ACF At-Camp Dashboard — Operations & Handover Runbook

This document explains how the auto-updating dashboard works, how to change it, how to
fix it when a run fails, and how to hand it over cleanly. It is written for whoever
maintains this after the original setup — no prior context assumed.

> Companion documents: `README.md` (first-time setup) and the separate
> **Metrics Definitions** doc (what each dashboard number means and how it's calculated).

---

## 1. What this is (the daily flow)

A public dashboard that refreshes itself once a day with **anonymized** TB active-case-finding
data from CommCare. No one runs anything day to day.

```
CommCare Excel Dashboard feeds  (refresh ~7 AM IST daily)
        │   4 feeds: 1 case + 3 lab forms; each PII-trimmed at the feed
        ▼
GitHub Actions job "Update dashboard data"  (runs ~9 AM IST daily)
        │   holds the API key (GitHub Secrets); joins on caseid, computes TAT,
        │   drops caseid + identifiers, writes data.json
        ▼
data.json  (committed to the repo)
        ▼
GitHub Pages  →  the dashboard (index.html reads data.json)
        ▼
Anyone with the link refreshes the page and sees the latest data
```

- **Live URL:** `https://<owner>.github.io/acf-dashboard/`
- **Refresh cadence:** once daily (data is only as fresh as CommCare's daily feed).
- The dashboard header shows a **"Data updated: …"** timestamp = when the job last pulled.
- GitHub cron works on a best-effort basis depending on server availability; autoupdates can thus be 15–90 min late or occasionally skipped — this is not a reason for panic and is acceptable because the source (CommCare) is daily

---

## 2. Key facts (fill in / keep current)

| Item | Value |
|---|---|
| GitHub repo | `gavishsaraf/acf-dashboard` (currently a **public** repo) |
| CommCare server | `india.commcarehq.org` |
| CommCare project space | `operational-feasibility` |
| Patient case type | **`test`** (yes, really — that is the app's case type) |
| Feed type | CommCare **Excel Dashboard Integration** (a.k.a. Daily Saved Export) |
| Feeds used | 1 case feed + 3 form feeds: Sputum Sample Collection, Sputum–PCR, NAAT Testing |
| Join key across feeds | `caseid` (in form feeds: `form.case.@case_id`) — used only in processing, never published |
| Auth | HTTP header `Authorization: ApiKey <email>:<api_key>` |
| Schedule | cron `30 3 * * *` (UTC) = **9:00 AM IST** |

---

## 3. What lives where (everything is in the repo)

| File | Purpose |
|---|---|
| `index.html` | The dashboard. Fetches `data.json` on load. |
| `data.json` | The anonymized data (`{generated, count, records}`). **Overwritten by the job — never hand-edit.** |
| `scripts/commcare_pull_anonymize.py` | The pull → join → TAT → anonymize job. |
| `.github/workflows/update-data.yml` | The schedule + the steps GitHub runs. |
| `requirements.txt` | Python deps for the job (`requests`, `pandas`, `lxml`). |
| `README.md` | First-time setup. |
| `OPERATIONS.md` | This file. |

**GitHub Secrets** (Settings → Secrets and variables → Actions) — the only things not in the repo:
`COMMCARE_EMAIL`, `COMMCARE_API_KEY`, `CASE_FEED_URL`, `SAMPLE_FEED_URL`, `PCR_FEED_URL`, `NAAT_FEED_URL`.

**Repo setting that must stay on:** Settings → Actions → General → Workflow permissions →
**Read and write** (lets the job commit the refreshed `data.json`).

---

## 4. How to make changes

All edits are done in the browser: open the file → pencil (Edit) → change → **Commit changes**.

| To change… | Edit | Takes effect |
|---|---|---|
| Dashboard look/logic (charts, KPIs, layout, text) | `index.html` | Next page refresh |
| Which fields are pulled / anonymization rules | `scripts/commcare_pull_anonymize.py` | Next scheduled run (or Actions → Run workflow) |
| The schedule | `.github/workflows/update-data.yml` (the `cron:` line, in **UTC**) | Immediately |
| Feeds / account / key | the six **Secrets** | Next run |

**Before committing a change to `commcare_pull_anonymize.py`, test it locally** (see §7) and
confirm the output `data.json` has no names/phones. A broken script fails the nightly job;
the page keeps showing the last good data until the next success.

**After any change, do the 10-second check:** page loads · "Data updated" is recent ·
open `…/data.json` and confirm no names/phone numbers, only analytic/clinical fields.

> `index.html` is a single large file. Small text tweaks are fine by hand; structural changes
> are best done by someone comfortable with the code (or an AI assistant) who regenerates the file.

---

## 5. Anonymization model (read before touching the script)

The script uses a **whitelist**: it copies only the fields named in `CASE_COLS` (plus the TAT
timestamps) into `data.json`. Anything not on the list — including any new column the app adds
later, or a free-text box someone types a name into — is dropped automatically. A final
assertion aborts the run if a known direct identifier ever appears in the output.

**Deliberately excluded** (documented in the script):
- Direct identifiers: `name`, `full_name`, `phone_number`, `nikshay_id`, `patient_id`, `id_prefix`, `caseid` (join only).
- **Free text** (`remarks_if_any`, `if_other_please_specify`, `x-ray_taken_by_who`) — highest leak risk; only add back with a scrubbing/review step.
- System/audit fields.

**Included quasi-identifiers:** `age` and `gender` are intentionally in the file for analysis.
Because the repo is **public**, this is a live data-governance decision (see §8).

---

## 6. When a run fails (GitHub emails the repo owner)

Actions tab → open the red run → open the **"Pull feeds, anonymize, write data.json"** step →
read the last ~15 lines. Common causes:

| Symptom in the log | Cause | Fix |
|---|---|---|
| `KeyError: '..._FEED_URL'` (or EMAIL/KEY) | A Secret is missing or misnamed | Settings → Secrets; add/rename to the exact six names |
| `401 Unauthorized` | API key wrong/expired, **or** key has an IP allow-list | Re-check `COMMCARE_API_KEY`; if IP-locked, GitHub runners use changing IPs → see §8 |
| `IDENTIFIER LEAKED INTO OUTPUT: [...]` | A field on the block-list appeared | Expected safety stop; adjust the whitelist/guard deliberately, don't bypass |
| Fails on the **commit** step | Workflow permissions not "Read and write" | Settings → Actions → General → set it, re-run |
| No commit, run is green | No new CommCare data that day | Normal — not a failure |

The dashboard keeps serving the last good `data.json` through any failure, so a one-off red
run does not blank the page.

---

## 7. Running / testing the job locally

Useful for testing script changes before committing, or diagnosing a failure.

```bash
pip install requests pandas lxml
# set the six values in your shell (never hard-code them in the file):
#   COMMCARE_EMAIL, COMMCARE_API_KEY, CASE_FEED_URL, SAMPLE_FEED_URL, PCR_FEED_URL, NAAT_FEED_URL
# optional: VERIFY=1  prints the columns each feed returned
python scripts/commcare_pull_anonymize.py
```
Then open the produced `data.json` and confirm the fields are what you expect and there are no
identifiers. If CommCare ever renames feed columns, update the `*_COLS` maps at the top of the
script (run once with `VERIFY=1` to see the current names).

---

## 8. Open decisions (parked — revisit)

1. **Public `age`/`gender`.** `data.json` is world-readable and now contains age and gender,
   which are quasi-identifiers. Pending an expert/governance review. Options if it needs to
   change: make the repo **private** (needs a paid GitHub plan for Pages), or **band age**
   (e.g. 5/10-year groups) — a small script change.
2. **Repeat-sample-collection step.** The discordance-resolution flow was to have a "samples
   collected" step before the repeat-test branches, but the app does **not** capture a
   repeat sputum-collection field. The CommCare team would need to add one to the Repeat
   PCR/NAAT forms before that step can be built.

---

## 9. Handover / continuity

**The system currently depends on one person in three ways — remove each on exit:**

1. **Repo under a personal account.** → Transfer to a CHAI/WJCF **GitHub organization**
   (Settings → General → Transfer ownership). Moves repo, history, Actions, and Pages intact.
   *Note: the Pages URL changes to the org's after transfer — reshare it.*
2. **A personal CommCare API key.** → Move to a **service / shared CommCare account** that
   holds the key and owns the four feeds; put that account's email + key in the Secrets.
   If not possible, the successor creates their own key + feeds and updates the Secrets.
3. **Know-how.** → This runbook + the README + the Metrics Definitions doc.

**Exit checklist:**
- [ ] Repo transferred to the CHAI/WJCF org (or confirmed already there)
- [ ] `COMMCARE_API_KEY` (+ email, feed URLs) switched to a service/successor account; one green run confirmed
- [ ] Successor granted admin on the repo/org; no sole dependence on the departing person's account
- [ ] This runbook and the README confirmed current
- [ ] New owner does one manual **Actions → Run workflow** to prove the chain works end-to-end
- [ ] Re-share the (possibly new) Pages URL with stakeholders

---

*Last reviewed: update this date whenever you change the system.*

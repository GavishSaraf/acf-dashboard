#!/usr/bin/env python3
"""
ACF dashboard data job -- for CommCare *Excel Dashboard Integration* feeds.

Pulls the 4 dashboard feeds (case + 3 lab forms), joins them on caseid, computes
TAT, strips identifiers + caseid, and writes ONE anonymized flat data.json.

Runs SERVER-SIDE only (your machine or a scheduled runner). It holds the API key
via an environment variable. The published data.json has no key, no caseid, no PII.

    CommCare feed --(ApiKey, here)--> this job --(anonymized)--> data.json --> dashboard

Dependencies:  pip install requests pandas lxml

Environment variables (never hard-code the key):
    COMMCARE_EMAIL     your CommCare login email
    COMMCARE_API_KEY   API key value (My Account Settings -> API Keys)
    CASE_FEED_URL      "Copy Dashboard Feed Link" of the anonymized CASE feed
    SAMPLE_FEED_URL    ... Sputum Sample Collection form feed
    PCR_FEED_URL       ... Sputum - PCR testing form feed
    NAAT_FEED_URL      ... NAAT Testing form feed
    OUT_PATH           output path (default ./data.json)
    VERIFY=1           print the columns each feed returned, then continue

First run: set VERIFY=1 and check the printed column names against the *_COLS maps
below. Dashboard feeds emit whatever *display* names you set in "Edit Columns", so
adjust the maps if yours differ. Logic is identical to the validated Excel ETL.
"""
import os, io, math, json, datetime
import requests
from requests.auth import HTTPBasicAuth
import pandas as pd

EMAIL   = os.environ['COMMCARE_EMAIL'].strip()
API_KEY = os.environ['COMMCARE_API_KEY'].strip()
OUT     = os.environ.get('OUT_PATH', 'data.json')
VERIFY  = os.environ.get('VERIFY', '0') == '1'

FEEDS = {k: os.environ[k] for k in
         ('CASE_FEED_URL', 'SAMPLE_FEED_URL', 'PCR_FEED_URL', 'NAAT_FEED_URL')}

# ---- column names as they appear in each feed (adjust after a VERIFY run) ----
CASE_ID_CASE = 'caseid'
CASE_ID_FORM = 'form.case.@case_id'
COMPLETED    = 'completed_time'
CASE_COLS = {  # feed column -> output key  (WHITELIST: only these leave the machine)
    # -- core programme fields --
    'camp_location':'camp_location','type_of_case_finding':'type_of_case_finding',
    'site_of_the_camp_prison_hospital_etc':'site_of_the_camp_prison_hospital_etc',
    'date_of_registration':'date_of_registration',
    # -- screening --
    'has_symptom':'has_symptom','has_risk':'has_risk',
    'clinical':'clinical','common_health':'common_health','social_factors':'social_factors',
    'select_ones_that_apply':'select_ones_that_apply',
    'consumed_any_tablet_that_turned_urine_red':'consumed_any_tablet_that_turned_urine_red',
    'past_tb':'past_tb',
    # -- demographics / vitals (quasi-identifiers: see note in the response) --
    'age':'age','gender':'gender',
    'height_in_cm':'height_in_cm','weight':'weight',
    'systolic_bp':'systolic_bp','diastolic_bp':'diastolic_bp',
    'blood_sugar':'blood_sugar','hemoglobin':'hemoglobin',
    # -- CXR --
    'xray_eligible':'xray_eligible','was_cxr_taken':'was_cxr_taken',
    'xray_abnormal':'xray_abnormal','result_of_x-ray':'result_of_xray',
    'date_and_time_of_x-ray':'date_and_time_of_xray',
    # -- sample / testing --
    'nature_of_sample':'nature_of_sample','sputum_collected':'sputum_collected',
    'date_and_time_of_sample_splitting':'date_and_time_of_sample_splitting',
    'date_and_time_of_pcr_test_start':'date_and_time_of_pcr_test_start',
    'pcr_result':'pcr_result','result_of_repeat_pcr_test':'result_of_repeat_pcr_test',
    'ct_value':'ct_value','ct_value_repeat':'ct_value_repeat',
    'naat_done':'naat_done','naat_result':'naat_result',
    'result_of_repeat_naat_test':'result_of_repeat_naat_test','type_of_test_done':'type_of_test_done',
    'if_positive_on_truenat_ct_value':'if_positive_on_truenat_ct_value',
    'if_positive_on_cbnaat':'if_positive_on_cbnaat',
    # -- Rif --
    'rif_test_completed':'rif_test_completed','result_of_rif_test':'result_of_rif_test',
    'result_of_repeat_rif_test':'result_of_repeat_rif_test','date_and_time_of_rif_test':'date_and_time_of_rif_test',
    # -- treatment / outcome --
    'decision_to_treat_as_tb':'decision_to_treat_as_tb','treated':'treated',
    'date_of_treatment_initiation':'date_of_treatment_initiation',
    'duration_of_treatment_in_months':'duration_of_treatment_in_months',
    'how_long_ago_did_treatment_end_in_months':'how_long_ago_did_treatment_end_in_months',
    'regimen_used':'regimen_used',
}
# DELIBERATELY EXCLUDED (do not add without a reason):
#   caseid           -> join key only; used during processing, never written out
#   id_prefix, number-> patient-ID fragment / row serial (identifier-like)
#   name, full_name, phone_number, nikshay_id, patient_id, owner_name, *_username
#                    -> direct identifiers (removed at the feed too)
#   remarks_if_any, if_other_please_specify, x-ray_taken_by_who
#                    -> FREE TEXT: a data-entry person can type a name/phone here, so
#                       including it would defeat anonymization. Add back only if the
#                       team commits to reviewing/scrubbing it first.
#   closed, closed_date, last_modified_date, opened_date, pcr_result_time_text
#                    -> system/audit/derived; not needed for analysis
SAMPLE_TS = {'form.date_and_time_of_sputum_collection':'tat_sputum_collection'}
PCR_TS    = {'form.date_and_time_of_sputum_sample_received_by_lab':'tat_pcr_received',
             'form.date_and_time_of_pcr_sputum_result':'tat_pcr_result'}
NAAT_TS   = {'form.date_and_time_of_naat_test':'tat_naat_start',
             'form.date_and_time_of_naat_sputum_result':'tat_naat_result'}

def clean(v):
    if v is None: return ''
    if isinstance(v, float) and math.isnan(v): return ''
    s = str(v).strip()
    return '' if s in ('','---','nan','None','NaN','NaT') else s

def ts(v):
    s = clean(v)
    if not s: return ''
    t = pd.to_datetime(s, errors='coerce')
    return '' if pd.isna(t) else t.strftime('%Y-%m-%d %H:%M:%S')

def fetch_table(url):
    """A dashboard feed link returns an HTML page with one data table.
    CommCare accepts either the Tastypie 'ApiKey' header or HTTP Basic auth
    (email + API key, the same thing the browser login uses). Try both."""
    attempts = [
        ('ApiKey header', dict(headers={'Authorization': f'ApiKey {EMAIL}:{API_KEY}'})),
        ('HTTP Basic',    dict(auth=HTTPBasicAuth(EMAIL, API_KEY))),
    ]
    last = None
    for label, kw in attempts:
        r = requests.get(url, timeout=180, **kw)
        if r.status_code == 401:
            last = r
            continue
        r.raise_for_status()
        tables = pd.read_html(io.StringIO(r.text))
        if not tables:
            raise SystemExit(f'Reached {url} but found no data table -- has the feed been "Update Data"-ed?')
        return max(tables, key=lambda d: d.shape[0] * d.shape[1])
    raise SystemExit(
        "401 Unauthorized -- CommCare rejected the email/API key (both auth methods failed).\n"
        "  1. Open the feed link in a browser; log in with your CommCare EMAIL and the\n"
        "     API KEY VALUE as the password. If that fails, the key/email is the problem.\n"
        "  2. Regenerate the key under My Account Settings > API Keys and copy the VALUE\n"
        "     (not the key's name). Re-set COMMCARE_API_KEY with the new value.\n"
        "  3. If the key has an IP allow-list, add this machine's IP (and GitHub later).\n"
        "  4. If your CHAI login is SSO, confirm API-key access is enabled for your account.")  # largest table

def dedup(df, key):
    df = df.copy()
    df['_ct'] = pd.to_datetime(df[COMPLETED], errors='coerce')
    df = df.sort_values('_ct').drop_duplicates(subset=key, keep='last')
    return df.set_index(key)

case = fetch_table(FEEDS['CASE_FEED_URL'])
samp = fetch_table(FEEDS['SAMPLE_FEED_URL'])
pcr  = fetch_table(FEEDS['PCR_FEED_URL'])
naat = fetch_table(FEEDS['NAAT_FEED_URL'])

if VERIFY:
    for name, df in [('case',case),('sample',samp),('pcr',pcr),('naat',naat)]:
        print(name, 'columns ->', list(df.columns))

samp_d = dedup(samp, CASE_ID_FORM)
pcr_d  = dedup(pcr,  CASE_ID_FORM)
naat_d = dedup(naat, CASE_ID_FORM)

def look(idx, cid, col):
    return idx.loc[cid, col] if cid in idx.index else None

records = []
for _, r in case.iterrows():
    cid = clean(r.get(CASE_ID_CASE))
    rec = {}
    for src, dst in CASE_COLS.items():
        rec[dst] = clean(r.get(src))[:10] if dst == 'date_of_registration' else clean(r.get(src)).lower()
    for m, idx in [(SAMPLE_TS, samp_d), (PCR_TS, pcr_d), (NAAT_TS, naat_d)]:
        for src, dst in m.items():
            rec[dst] = ts(look(idx, cid, src))
    records.append(rec)   # caseid intentionally NOT written

payload = {
    'generated': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
    'count': len(records),
    'records': records,
}
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))

leaked = [k for k in ('caseid','patient_id','full_name','name','phone_number','nikshay_id','id_prefix')
          if records and k in records[0]]
assert not leaked, f'IDENTIFIER LEAKED INTO OUTPUT: {leaked}'
print(f'Wrote {len(records)} anonymized records to {OUT} (generated {payload["generated"]}). No identifiers in output.')

# Deploying to Google Cloud (asia-south1, free tier)

Target: a public API on Cloud Run and an hourly ETL as a Cloud Run job started by Cloud
Scheduler. Postgres is on Neon (Mumbai). Secrets live only in Secret Manager.
Region: **`asia-south1` (Mumbai)**.

| Piece | GCP service | Name |
|---|---|---|
| Public API | Cloud Run service, `min-instances 0`, `max-instances 2`, `RUN_ETL_ON_STARTUP=false` | `kasparro-api` |
| ETL | Cloud Run job, `python scripts/run_etl.py` | `kasparro-etl` |
| Hourly trigger | Cloud Scheduler `0 * * * *` (HTTP → Run Admin API) | `kasparro-etl-hourly` |
| Images | Artifact Registry, cleanup policy keeps the 2 newest | `asia-south1-docker.pkg.dev/PROJECT/kasparro/kasparro-backend` |
| Secrets | Secret Manager (all four required) | `kasparro-database-url`, `kasparro-app-api-key`, `kasparro-coingecko-api-key`, `kasparro-coinpaprika-api-key` |
| Database | Neon Postgres, AWS ap-south-1 (Mumbai), direct endpoint, `sslmode=require` | — |
| Logs / metrics | Cloud Logging + 4 log-based metrics | `kasparro_etl_run_success`, `_failure`, `_records_in`, `_records_inserted` |

```text
GitHub (push to main or manual run) ──OIDC──▶ Workload Identity Federation ──▶ kasparro-deployer SA
   build image → Artifact Registry → deploy service + job → upsert scheduler → /health must be 200 → smoke test

Cloud Scheduler ──hourly──▶ Cloud Run job (ETL) ──┐
                                                  ├──▶ Neon Postgres (Mumbai, TLS)
Internet ──HTTPS──▶ Cloud Run service (API) ──────┘
     both write JSON logs (with severity) ──▶ Cloud Logging ──▶ log-based metrics
```

There are no JSON service-account keys anywhere. The image contains no secrets
(`.dockerignore` excludes `.env`). Secrets are injected at runtime with `--set-secrets`.

---

## Free-tier budget

| Item | This deployment | Monthly free allowance | Source |
|---|---|---|---|
| Cloud Run service | request-based billing, scales to 0 | 2M requests, 180,000 vCPU-s, 360,000 GiB-s | [Cloud Run pricing](https://cloud.google.com/run/pricing) |
| Cloud Run job | 720 runs × ~1 min (billed at least 1 min each) × 1 vCPU ≈ 43,200 vCPU-s, 21,600 GiB-s | 240,000 vCPU-s, 450,000 GiB-s, **unverified**: seen in Google's pricing page snippets, but cached copies disagree (180k / 200k) and the live page could not be read | [Cloud Run pricing](https://cloud.google.com/run/pricing) |
| asia-south1 | Tier 1 since Nov 2024; free tier applied at Tier 1 prices | — | [Release notes](https://docs.cloud.google.com/run/docs/release-notes), [Locations](https://docs.cloud.google.com/run/docs/locations) |
| Network egress | API responses to clients | 1 GB free **in North America only**; Mumbai egress is billed (fractions of a cent at demo volume) | [Cloud Run pricing](https://cloud.google.com/run/pricing) |
| Cloud Scheduler | 1 job | 3 jobs per billing account | [GCP free tier](https://cloud.google.com/free) |
| Secret Manager | 4 secrets × 1 version; ~3–4k reads/month | 6 active versions, 10,000 access ops | [GCP free tier](https://cloud.google.com/free) |
| Artifact Registry | ~150–250 MB/image, 2 kept | 0.5 GB storage | [GCP free tier](https://cloud.google.com/free) |
| Logging | small | 50 GiB/month | [GCP free tier](https://cloud.google.com/free) |

Keep secret versions at 6 or fewer: when you rotate a secret, **destroy** the old version
(`gcloud secrets versions destroy N --secret NAME`).

---

## Billable resources: what each step creates

Every resource below has a free allowance, but all of them are billable SKUs. Nothing is
created until you run the commands yourself.

| Step | Creates | Billable? |
|---|---|---|
| 1 | Neon project (outside GCP) | Neon free plan |
| 2 | GCP project, billing link, **budget + alerts** | Budgets are free |
| 3 `bootstrap.sh` | APIs enabled; Artifact Registry repo + cleanup policy; 3 service accounts; WIF pool/provider; 4 secrets + versions; 4 log-based metrics | AR storage, Secret Manager, Monitoring (all within the free allowance at this size). SAs, WIF and IAM are free |
| 5 deploy workflow | Cloud Run service, Cloud Run job, Cloud Scheduler job; one job execution | Cloud Run, Scheduler (within the free allowance) |

---

## 1. Neon (Mumbai)

1. In the Neon console, create a project in **AWS Asia Pacific (Mumbai) `aws-ap-south-1`**.
2. Under *Connection details*, turn **Connection pooling OFF** and copy the **direct** string.
   The host must not contain `-pooler`:

   ```text
   postgresql://USER:PASSWORD@ep-xxxx-xxxx.ap-south-1.aws.neon.tech/neondb?sslmode=require
   ```

   The ETL uses session-level advisory locks, and Neon's pooled (PgBouncer transaction mode)
   endpoint drops them. `sslmode=require` is mandatory.
3. You don't need to create any tables. Alembic migrations run on API startup and before
   every ETL run.

Check Neon's current free-plan limits (storage, compute hours) in its pricing page; they
weren't verified here. The hourly job wakes the compute for a few minutes each hour.

## 2. GCP project, billing and a budget alert

In [Cloud Shell](https://shell.cloud.google.com):

```bash
export PROJECT_ID=kasparro-<something-unique> REGION=asia-south1
gcloud projects create "$PROJECT_ID"
gcloud billing accounts list                                   # note ACCOUNT_ID and its currency
export BILLING_ACCOUNT=XXXXXX-XXXXXX-XXXXXX
gcloud billing projects link "$PROJECT_ID" --billing-account "$BILLING_ACCOUNT"
gcloud config set project "$PROJECT_ID"
```

**Budget alert (do this before deploying anything).** It emails the billing admins at 50%,
90% and 100% of a tiny budget. Use your billing account's currency: `100INR`, or `1USD`
for a USD account.

```bash
gcloud services enable billingbudgets.googleapis.com
gcloud billing budgets create --billing-account "$BILLING_ACCOUNT" \
  --display-name "kasparro free-tier guard" --budget-amount 100INR \
  --filter-projects "projects/$PROJECT_ID" \
  --threshold-rule percent=0.5 --threshold-rule percent=0.9 --threshold-rule percent=1.0
```

A budget alerts; it does **not** stop spending. Kill switch, if an alert ever fires
unexpectedly: `gcloud billing projects unlink "$PROJECT_ID"`. This disables billing and stops
all paid services in the project.

## 3. Bootstrap

Get the upstream keys first:

- **CoinGecko demo key**: CoinGecko developer dashboard → *Demo* plan → API key. It is sent
  as `x-cg-demo-api-key` to `api.coingecko.com`.
- **CoinPaprika key**: from your CoinPaprika API dashboard. It is sent as `Authorization`.
  The free host `api.coinpaprika.com` accepts requests **without** a key and ignores the
  header (tested), so a key only matters on the host your plan uses. If your key is for
  the Pro host, set the repo variable `COINPAPRIKA_BASE_URL=https://api-pro.coinpaprika.com/v1`
  (step 4).

  Check your keys before deploying. CoinPaprika Pro should print 200. CoinGecko printing 200
  only means the host is reachable: it did not reject a made-up demo key in testing, so
  confirm the key through the request counter in the CoinGecko dashboard after the first ETL run.

  ```bash
  read -rs CP_KEY
  curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: $CP_KEY" https://api-pro.coinpaprika.com/v1/tickers/btc-bitcoin
  read -rs CG_KEY
  curl -s -o /dev/null -w '%{http_code}\n' -H "x-cg-demo-api-key: $CG_KEY" "https://api.coingecko.com/api/v3/ping"
  ```

Then, from a clone of the repo in Cloud Shell:

```bash
git clone https://github.com/manojkumargudge/kasparro-backend-manojkumar-gudge && cd kasparro-backend-manojkumar-gudge
export GITHUB_REPO=manojkumargudge/kasparro-backend-manojkumar-gudge
read -rs DATABASE_URL && export DATABASE_URL                 # Neon direct URL from step 1
export APP_API_KEY="$(openssl rand -hex 24)"; echo "save this: $APP_API_KEY"
export COINGECKO_API_KEY="$CG_KEY" COINPAPRIKA_API_KEY="$CP_KEY"
./deploy/gcp/bootstrap.sh
```

`bootstrap.sh` is idempotent and prints nothing secret. Values are piped to Secret Manager.
It creates:

- the Artifact Registry repo `kasparro` and its 2-image cleanup policy;
- three service accounts, each with the least access it needs: `kasparro-runtime` (reads
  secrets, writes logs and metrics), `kasparro-deployer` (used by GitHub Actions via WIF),
  and `kasparro-scheduler` (`run.invoker`);
- the WIF pool `github` and provider `github-oidc`, restricted to `GITHUB_REPO`;
- the 4 secrets;
- the 4 log-based metrics.

## 4. Connect GitHub

Run the 6 `gh variable set …` lines that `bootstrap.sh` prints, or add them under
*Settings → Secrets and variables → Actions → Variables*. They are not secret. Optionally
also set `COINPAPRIKA_BASE_URL` (see step 3).

## 5. Deploy

```bash
gh workflow run deploy.yml --ref main      # or push to main
gh run watch
```

The deploy job does the following, and fails if anything goes wrong:

1. Checks that all four secrets have a version.
2. Builds and pushes the image.
3. Deploys the service with `RUN_ETL_ON_STARTUP=false`, so the ETL runs only from the job.
4. Deploys the job.
5. Upserts the hourly scheduler.
6. Requires **`/health` → HTTP 200**.
7. Starts one ETL execution if none has succeeded yet.
8. Runs `scripts/smoke_test.py`.

The service URL is in the job summary.

## 6. Verify (definition of done)

```bash
URL=$(gcloud run services describe kasparro-api --region $REGION --format 'value(status.url)')
curl -s -o /dev/null -w '%{http_code}\n' "$URL/health"                       # 200
curl -s "$URL/health" | jq '.etl.sources'                                     # one run per source
curl -s -H "X-API-KEY: $APP_API_KEY" "$URL/runs?limit=4" | jq '.runs[] | {source,status,records_in,records_inserted}'
gcloud logging read 'resource.type="cloud_run_job" AND jsonPayload.event="etl_run_finished"' --limit 4 \
  --format 'value(jsonPayload.source,jsonPayload.status,jsonPayload.records_in)'
gcloud logging read 'resource.type="cloud_run_job" AND jsonPayload.auth="api_key"' --limit 2 \
  --format 'value(jsonPayload.message)'                                      # "coingecko auth: API key via ..."
```

To confirm the real keys are used, check the request counters in the CoinGecko and
CoinPaprika dashboards after a run. The log line above shows only the header name, never
the value.

**Cron runs:** *Cloud Scheduler → kasparro-etl-hourly* shows last run and status;
*Cloud Run → Jobs → kasparro-etl → Executions* shows one execution per hour. To force a run:
`gcloud scheduler jobs run kasparro-etl-hourly --location $REGION`.

**Logs Explorer queries:**

- `jsonPayload.event="etl_run_finished"`
- `jsonPayload.status="failed"`
- `severity>=WARNING`

**Metrics Explorer:** *Logging → User-defined* lists `logging/user/kasparro_etl_run_success`,
`…_failure`, `…_records_in` and `…_records_inserted`.

**Crash and resume:**

```bash
gcloud run jobs execute kasparro-etl --region $REGION --wait \
  --update-env-vars ETL_SOURCES=coinpaprika,ETL_BATCH_SIZE=10,FAIL_AFTER_N=25,FAIL_MODE=exit   # fails (exit 70)
gcloud run jobs execute kasparro-etl --region $REGION --wait --update-env-vars ETL_SOURCES=coinpaprika
```

## 7. Measure cold start

Cloud Run scales an idle service to zero, after roughly 15 idle minutes. To measure a cold
start, wait **≥ 20 minutes** with no traffic, then:

```bash
curl -s -o /dev/null -w 'cold: %{http_code} in %{time_total}s\n' "$URL/health"
curl -s -o /dev/null -w 'warm: %{http_code} in %{time_total}s\n' "$URL/health"
```

Repeat 3 times, idling between tries. A cold start includes container start, the migration
check, and waking a suspended Neon compute.

## 8. Teardown

```bash
gcloud scheduler jobs delete kasparro-etl-hourly --location $REGION
gcloud run jobs delete kasparro-etl --region $REGION
gcloud run services delete kasparro-api --region $REGION
gcloud artifacts repositories delete kasparro --location $REGION
# or delete the whole project:  gcloud projects delete "$PROJECT_ID"
```

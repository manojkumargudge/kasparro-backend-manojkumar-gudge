# Deploying to Google Cloud

The deployment covers brief items **F.3** (public API), **F.4** (scheduled ETL) and
**F.5** (logs and metrics in the cloud console). The same container image runs both
workloads:

| Piece | GCP service | Name |
|---|---|---|
| Public API | Cloud Run **service** | `kasparro-api` |
| ETL | Cloud Run **job** (`python scripts/run_etl.py`) | `kasparro-etl` |
| Hourly trigger | Cloud Scheduler (HTTP → Run Admin API) | `kasparro-etl-hourly` |
| Images | Artifact Registry (also published to GHCR) | `REGION-docker.pkg.dev/PROJECT/kasparro/kasparro-backend` |
| Secrets | Secret Manager | `kasparro-database-url`, `kasparro-app-api-key`, `kasparro-coingecko-api-key`, `kasparro-coinpaprika-api-key` |
| Database | Neon Postgres (`sslmode=require`), or Cloud SQL | — |
| Logs / metrics | Cloud Logging + log-based metrics | `kasparro_etl_run_success`, `kasparro_etl_run_failure`, `kasparro_etl_records_in`, `kasparro_etl_records_inserted` |

```text
GitHub push to main
  └─ .github/workflows/deploy.yml
       ├─ publish:    build → ghcr.io/<owner>/kasparro-backend:{sha-…,latest}   (always)
       └─ deploy-gcp: OIDC → Workload Identity Federation → deployer SA          (only if GCP vars are set)
            ├─ build → Artifact Registry
            ├─ gcloud run deploy kasparro-api          (RUN_ETL_ON_STARTUP=0, secrets from Secret Manager)
            ├─ gcloud run jobs deploy kasparro-etl     (max-retries 1, 15 min timeout)
            ├─ Cloud Scheduler "0 * * * *" → jobs/kasparro-etl:run
            └─ scripts/smoke_test.py against the service URL

Cloud Scheduler ──hourly──▶ Cloud Run job ──▶ Neon Postgres ◀── Cloud Run service ◀── public HTTPS
                                 │                                    │
                                 └──── JSON logs (severity) ──────────┴──▶ Cloud Logging ──▶ log-based metrics
```

There are no JSON service-account keys anywhere. GitHub Actions authenticates through
Workload Identity Federation, and the trust is restricted to this repository. The deploy job
is **skipped**, not failed, until the GCP repository variables exist.

---

## 1. Prerequisites (one time)

1. A GCP project with billing enabled. Note its **project ID**.
2. The `gcloud` CLI, logged in as a project owner (`gcloud auth login`). Cloud Shell works
   and already has everything installed.
3. A Postgres database (next step).

## 2. Create the database

### Option A: Neon (recommended, free tier)

1. Create a project at neon.tech and pick the region closest to your Cloud Run region.
2. Copy the **direct** connection string, not the pooled one (no `-pooler` in the host):

   ```text
   postgresql://USER:PASSWORD@ep-xxxx-xxxx.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```

   The ETL uses session-level Postgres advisory locks so the scheduled job and a manual run
   never process the same source at once. PgBouncer in transaction mode, which is what
   Neon's pooled endpoint uses, does not keep those locks, so use the direct endpoint.
   `sslmode=require` is mandatory on Neon.
3. You don't need to create any tables. Alembic migrations run automatically on API startup
   and before every ETL run.

### Option B: Cloud SQL for PostgreSQL

1. `gcloud sql instances create kasparro-pg --database-version=POSTGRES_15 --tier=db-f1-micro --region=REGION`
2. Create a database and user: `gcloud sql databases create kasparro --instance kasparro-pg`, then
   `gcloud sql users create kasparro --instance kasparro-pg --password=…`
3. Connect Cloud Run through the Unix socket. Add
   `--add-cloudsql-instances PROJECT:REGION:kasparro-pg` to both `gcloud run deploy` and
   `gcloud run jobs deploy` in `deploy.yml`, give the runtime SA `roles/cloudsql.client`, and use:

   ```text
   postgresql://kasparro:PASSWORD@/kasparro?host=/cloudsql/PROJECT:REGION:kasparro-pg
   ```

Cloud SQL costs money even when idle, which is why Neon is the default here.

## 3. Bootstrap GCP (APIs, registry, service accounts, WIF, secrets, metrics)

From a clone of this repo, in Cloud Shell or a Linux/macOS shell:

```bash
export PROJECT_ID=your-project-id REGION=us-central1 GITHUB_REPO=manojkumargudge/kasparro-backend-manojkumar-gudge

# Optional: provide secret values now. They are piped to Secret Manager and never printed.
read -rs DATABASE_URL && export DATABASE_URL          # the Neon direct URL from step 2
export APP_API_KEY="$(openssl rand -hex 24)"           # client key for X-API-KEY; keep it somewhere safe
read -rs COINGECKO_API_KEY && export COINGECKO_API_KEY       # optional: CoinGecko demo key
read -rs COINPAPRIKA_API_KEY && export COINPAPRIKA_API_KEY   # optional: CoinPaprika key

./deploy/gcp/bootstrap.sh
```

The script is idempotent. It:

- enables the Run, Artifact Registry, Secret Manager, Scheduler, IAM, STS, Logging and
  Monitoring APIs;
- creates the `kasparro` Docker repository in Artifact Registry;
- creates three service accounts, each with the least access it needs:
  - `kasparro-runtime`: what the service and job run as. It can read the four secrets and
    write logs and metrics.
  - `kasparro-deployer`: what GitHub Actions uses. It has Run admin, Artifact Registry writer
    and Scheduler admin, can act as the runtime and scheduler accounts, can see secret
    metadata, and can read the app key for the smoke test.
  - `kasparro-scheduler`: has `run.invoker`, so Cloud Scheduler can start the job.
- creates the Workload Identity pool `github` and the OIDC provider `github-oidc`, limited to
  `assertion.repository == GITHUB_REPO`;
- creates the four secrets and adds versions for any values you exported;
- creates the four log-based metrics from `deploy/gcp/log-metrics/*.yaml`.

To add or rotate a secret later:

```bash
printf %s "$VALUE" | gcloud secrets versions add kasparro-coinpaprika-api-key --data-file=-
```

The two upstream keys are optional. The deploy binds them only when they have a version;
without them the CoinGecko and CoinPaprika keyless tiers are used.

## 4. Connect GitHub

The script prints six `gh variable set …` commands at the end. Run them, or add the same
values under *Settings → Secrets and variables → Actions → **Variables***. None of them are
secrets:

| Variable | Example |
|---|---|
| `GCP_PROJECT_ID` | `your-project-id` |
| `GCP_REGION` | `us-central1` |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/123456789/locations/global/workloadIdentityPools/github/providers/github-oidc` |
| `GCP_DEPLOY_SERVICE_ACCOUNT` | `kasparro-deployer@your-project-id.iam.gserviceaccount.com` |
| `GCP_RUNTIME_SERVICE_ACCOUNT` | `kasparro-runtime@your-project-id.iam.gserviceaccount.com` |
| `GCP_SCHEDULER_SERVICE_ACCOUNT` | `kasparro-scheduler@your-project-id.iam.gserviceaccount.com` |

## 5. Deploy

Push to `main`, or open *Actions → Deploy → Run workflow*. On the first deploy the workflow
starts the ETL job once before the smoke test, so `/health` has data. The job summary shows
the service URL.

**Make the GHCR image public for evaluators (one time).** On GitHub open *Your profile →
Packages → kasparro-backend → Package settings → Change visibility → Public*. After that:

```bash
docker pull ghcr.io/manojkumargudge/kasparro-backend:latest
```

## 6. Demo checklist (what to show in the console)

Set `URL=$(gcloud run services describe kasparro-api --region $REGION --format 'value(status.url)')`.

**Public API** (F.3)

```bash
curl -s "$URL/health" | jq
curl -s -H "X-API-KEY: $APP_API_KEY" "$URL/data?limit=5&sort=market_cap" | jq
curl -s -H "X-API-KEY: $APP_API_KEY" "$URL/runs?limit=5" | jq
curl -s "$URL/metrics" | head -40
```

**Cron runs** (F.4)

- Console: *Cloud Scheduler* shows `kasparro-etl-hourly` with the schedule `0 * * * *`, last
  run and result.
- Trigger a run immediately: `gcloud scheduler jobs run kasparro-etl-hourly --location $REGION`
- Console: *Cloud Run → Jobs → kasparro-etl → Executions* shows one execution per hour, each
  with status, duration and logs.
  CLI: `gcloud run jobs executions list --job kasparro-etl --region $REGION`
- The API shows the same runs: `GET /runs?limit=10` lists one row per source per execution.

**Logs** (F.5). In *Logging → Logs Explorer*, use these queries. Severity is mapped from the
JSON `severity` field, so the level filter works.

```text
resource.type="cloud_run_job" AND resource.labels.job_name="kasparro-etl"
jsonPayload.event="etl_run_finished"
jsonPayload.event="etl_run_finished" AND jsonPayload.status="failed"
severity>=WARNING AND jsonPayload.message:"Schema drift"
resource.type="cloud_run_revision" AND resource.labels.service_name="kasparro-api"
```

**Metrics** (F.5). In *Monitoring → Metrics Explorer*, choose *Logging → User-defined*:

- `logging/user/kasparro_etl_run_success`: runs per source (group by `source`)
- `logging/user/kasparro_etl_run_failure`: failed runs per source
- `logging/user/kasparro_etl_records_in` and `…_records_inserted`: records per run
  (distributions)

Built-in Cloud Run metrics (request count, latency, instance count) are on the service's
*Metrics* tab. To alert on failures, create an alerting policy on
`kasparro_etl_run_failure > 0` over 1 hour.

**Restart + resume** (F.7). Crash a run partway through, then resume it:

```bash
gcloud run jobs execute kasparro-etl --region $REGION --wait \
  --update-env-vars ETL_SOURCES=coinpaprika,ETL_BATCH_SIZE=10,FAIL_AFTER_N=25,FAIL_MODE=exit   # exits 70
gcloud run jobs execute kasparro-etl --region $REGION --wait --update-env-vars ETL_SOURCES=coinpaprika
curl -s -H "X-API-KEY: $APP_API_KEY" "$URL/runs?limit=4&source=coinpaprika" | jq '.runs[] | {status, error, records_in, records_unchanged, records_inserted}'
```

You should see the crashed run as `failed` with the error `abandoned: …`, then a
`success` run whose `records_unchanged` covers the batches committed before the crash.
`--update-env-vars` on `execute` applies only to that one execution.

**Full live smoke test** (F.7), from any machine:

```bash
python scripts/smoke_test.py --base-url "$URL" --api-key "$APP_API_KEY" \
  --crash-cmd  "gcloud run jobs execute kasparro-etl --region $REGION --wait --update-env-vars ETL_SOURCES=coinpaprika,ETL_BATCH_SIZE=10,FAIL_AFTER_N=25,FAIL_MODE=exit" \
  --resume-cmd "gcloud run jobs execute kasparro-etl --region $REGION --wait --update-env-vars ETL_SOURCES=coinpaprika"
```

It checks ingest, data, stats, metrics, resume and rate limiting (429 + `Retry-After`).
The rate-limit check uses up the key's budget for the current minute.

## 7. Costs and teardown

At hourly runs this stays within the free tiers of Cloud Run, Scheduler (3 free jobs),
Secret Manager, Logging and Neon. To remove everything:

```bash
gcloud scheduler jobs delete kasparro-etl-hourly --location $REGION
gcloud run jobs delete kasparro-etl --region $REGION
gcloud run services delete kasparro-api --region $REGION
gcloud artifacts repositories delete kasparro --location $REGION
```

## Known limitations

- `/metrics` is in Prometheus format, but Cloud Run does not scrape it on its own. The console
  metrics come from log-based metrics plus Cloud Run's built-in ones. To scrape `/metrics`,
  add a Managed Service for Prometheus sidecar.
- The CI smoke test skips the crash/resume and rate-limit checks so that deploys stay fast and
  don't use up the key's budget. Run them manually with the command above.
- This guide and its scripts were written without access to a GCP project. The workflow
  passes actionlint and the bootstrap script passes shellcheck, but neither has been run
  against a real project yet.

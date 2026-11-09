#!/usr/bin/env bash
# One-time (idempotent) GCP setup for kasparro-backend. Safe to re-run.
#
#   PROJECT_ID=my-project REGION=asia-south1 GITHUB_REPO=owner/repo ./deploy/gcp/bootstrap.sh
#
# Secret values: enter them beforehand with `read -s VAR; export VAR` (no shell history) for
# DATABASE_URL, APP_API_KEY, APP_DEMO_API_KEY, COINGECKO_API_KEY, COINPAPRIKA_API_KEY.
# They are piped to Secret Manager and never printed.
# Requires: gcloud (authenticated as a project owner). See docs/DEPLOY_GCP.md.
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
: "${GITHUB_REPO:?set GITHUB_REPO (owner/repo)}"
REGION="${REGION:-asia-south1}"
AR_REPO="${AR_REPO:-kasparro}"
POOL="${WIF_POOL:-github}"
PROVIDER="${WIF_PROVIDER:-github-oidc}"
HERE="$(cd "$(dirname "$0")" && pwd)"

RUNTIME_SA="kasparro-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
DEPLOY_SA="kasparro-deployer@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULER_SA="kasparro-scheduler@${PROJECT_ID}.iam.gserviceaccount.com"

log() { printf '\n==> %s\n' "$*"; }
exists() { "$@" >/dev/null 2>&1; }

gcloud config set project "$PROJECT_ID" >/dev/null
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"

log "Enabling APIs"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com \
  cloudscheduler.googleapis.com iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  logging.googleapis.com monitoring.googleapis.com

log "Artifact Registry repository ${AR_REPO} (${REGION})"
exists gcloud artifacts repositories describe "$AR_REPO" --location "$REGION" ||
  gcloud artifacts repositories create "$AR_REPO" --repository-format=docker --location "$REGION" \
    --description "kasparro-backend images"
# Stay inside the 0.5 GB Artifact Registry free allowance: keep the 2 newest images only.
gcloud artifacts repositories set-cleanup-policies "$AR_REPO" --location "$REGION" \
  --policy "$HERE/ar-cleanup-policy.json" --no-dry-run >/dev/null

log "Service accounts"
for sa in kasparro-runtime kasparro-deployer kasparro-scheduler; do
  exists gcloud iam service-accounts describe "${sa}@${PROJECT_ID}.iam.gserviceaccount.com" ||
    gcloud iam service-accounts create "$sa" --display-name "$sa"
done

bind_project() { gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$1" --role "$2" \
  --condition=None >/dev/null; }
bind_project "$RUNTIME_SA" roles/logging.logWriter
bind_project "$RUNTIME_SA" roles/monitoring.metricWriter
bind_project "$DEPLOY_SA" roles/run.admin
bind_project "$DEPLOY_SA" roles/artifactregistry.writer
bind_project "$DEPLOY_SA" roles/cloudscheduler.admin
bind_project "$DEPLOY_SA" roles/secretmanager.viewer
bind_project "$SCHEDULER_SA" roles/run.invoker
for sa in "$RUNTIME_SA" "$SCHEDULER_SA"; do
  gcloud iam service-accounts add-iam-policy-binding "$sa" --member "serviceAccount:$DEPLOY_SA" \
    --role roles/iam.serviceAccountUser >/dev/null
done

log "Workload Identity Federation for ${GITHUB_REPO} (no JSON keys)"
exists gcloud iam workload-identity-pools describe "$POOL" --location global ||
  gcloud iam workload-identity-pools create "$POOL" --location global --display-name "GitHub Actions"
exists gcloud iam workload-identity-pools providers describe "$PROVIDER" --location global --workload-identity-pool "$POOL" ||
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" --location global \
    --workload-identity-pool "$POOL" --display-name "GitHub OIDC" \
    --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition "assertion.repository == '${GITHUB_REPO}'"
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${GITHUB_REPO}" \
  >/dev/null

log "Secret Manager"
# secret name | env var holding an optional initial value | required?
SECRETS=(
  "kasparro-database-url|DATABASE_URL|required"
  "kasparro-app-api-key|APP_API_KEY|required"
  "kasparro-demo-api-key|APP_DEMO_API_KEY|required"
  "kasparro-coingecko-api-key|COINGECKO_API_KEY|required"
  "kasparro-coinpaprika-api-key|COINPAPRIKA_API_KEY|required"
)
for entry in "${SECRETS[@]}"; do
  IFS='|' read -r name var kind <<<"$entry"
  exists gcloud secrets describe "$name" || gcloud secrets create "$name" --replication-policy automatic
  if [[ -n "${!var:-}" ]]; then
    printf '%s' "${!var}" | gcloud secrets versions add "$name" --data-file=- >/dev/null
    echo "   stored a new version of $name (from \$$var)"
  elif ! gcloud secrets versions list "$name" --filter state=ENABLED --limit 1 --format 'value(name)' | grep -q .; then
    echo "   $name has no value yet ($kind): read -s VALUE; printf %s \"\$VALUE\" | gcloud secrets versions add $name --data-file=-"
  fi
  gcloud secrets add-iam-policy-binding "$name" --member "serviceAccount:$RUNTIME_SA" \
    --role roles/secretmanager.secretAccessor >/dev/null
done
# The deploy workflow reads the app key to run the post-deploy smoke test (masked in logs).
gcloud secrets add-iam-policy-binding kasparro-app-api-key --member "serviceAccount:$DEPLOY_SA" \
  --role roles/secretmanager.secretAccessor >/dev/null

log "Log-based metrics"
for file in "$HERE"/log-metrics/*.yaml; do
  metric="$(basename "$file" .yaml)"
  if exists gcloud logging metrics describe "$metric"; then
    gcloud logging metrics update "$metric" --config-from-file "$file" >/dev/null
  else
    gcloud logging metrics create "$metric" --config-from-file "$file" >/dev/null
  fi
  echo "   $metric"
done

cat <<EOF

Done. Set these GitHub repository *variables* (Settings → Secrets and variables → Actions → Variables),
or run the gh commands below. None of them are secrets.

  gh variable set GCP_PROJECT_ID --body "${PROJECT_ID}"
  gh variable set GCP_REGION --body "${REGION}"
  gh variable set GCP_WORKLOAD_IDENTITY_PROVIDER --body "projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
  gh variable set GCP_DEPLOY_SERVICE_ACCOUNT --body "${DEPLOY_SA}"
  gh variable set GCP_RUNTIME_SERVICE_ACCOUNT --body "${RUNTIME_SA}"
  gh variable set GCP_SCHEDULER_SERVICE_ACCOUNT --body "${SCHEDULER_SA}"

Then push to main (or run the "Deploy" workflow manually).
EOF

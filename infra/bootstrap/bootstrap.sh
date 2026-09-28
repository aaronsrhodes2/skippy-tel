#!/usr/bin/env bash
# One-time CI/CD bootstrap. Run in Cloud Shell, from the repo:
#
#   cd ~/skippy-tel && git pull && bash infra/bootstrap/bootstrap.sh
#
# After this, every `git push` deploys (portal + game-server infra) through
# GitHub Actions, and server settings/restarts happen in the portal's admin
# page. It's safe to run again: it only asks for what's still missing.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECT="${PROJECT:-skippy-tel}"
REGION="${REGION:-us-west1}"
gcloud config set project "$PROJECT" >/dev/null

say() { printf '\n\033[1;32m== %s\033[0m\n' "$*"; }

# ── 1. terraform (Cloud Shell doesn't keep it between sessions) ───────────────
if ! command -v terraform >/dev/null; then
  say "Installing terraform"
  wget -qO- https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor --yes -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
  echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" \
    | sudo tee /etc/apt/sources.list.d/hashicorp.list >/dev/null
  sudo apt-get update -qq && sudo apt-get install -y -qq terraform
fi

# ── 2. APIs first (enabling one takes a minute to propagate; terraform's
#       lookups fail if they run before that) ─────────────────────────────────
say "Enabling APIs (this can take a minute)"
gcloud services enable artifactregistry.googleapis.com cloudresourcemanager.googleapis.com \
  compute.googleapis.com iam.googleapis.com iamcredentials.googleapis.com run.googleapis.com \
  secretmanager.googleapis.com serviceusage.googleapis.com storage.googleapis.com sts.googleapis.com
for i in $(seq 1 12); do
  gcloud iam service-accounts describe "skippy-agent@$PROJECT.iam.gserviceaccount.com" >/dev/null 2>&1 && break
  echo "  waiting for the IAM API to come up..."; sleep 10
done

# ── 3. bootstrap resources ────────────────────────────────────────────────────
say "Creating CI/CD resources (state bucket, registry, deployer identity, secrets)"
cd "$ROOT/infra/bootstrap"
terraform init -input=false >/dev/null
terraform apply -input=false -auto-approve -var "project=$PROJECT" -var "region=$REGION"
NUMBER=$(terraform output -raw project_number)
PORTAL_URL=$(terraform output -raw portal_url)
BUCKET=$(terraform output -raw state_bucket)

# ── 4. secrets ────────────────────────────────────────────────────────────────
has_version() { gcloud secrets versions list "$1" --filter="state=ENABLED" --format="value(name)" --limit=1 2>/dev/null | grep -q .; }

if ! has_version portal-google-client-id; then
  cat <<EOF

  The portal needs a "Sign in with Google" OAuth client. In the console:
    1. APIs & Services -> OAuth consent screen (Google Auth Platform): User type External,
       app name "Skippy Portal", your email for support/contact. Then Audience ->
       PUBLISH APP. Publishing matters: in "Testing" mode Google itself blocks anyone you
       haven't added as a test user, so friends couldn't sign in. It only asks for
       name + email, so no Google review is needed — and the portal's own allowlist
       still decides who actually gets in.
    2. APIs & Services -> Credentials -> Create credentials -> OAuth client ID
       -> Web application. Authorized redirect URI:
           $PORTAL_URL/auth/callback
    3. Copy the client ID and secret, and paste them below.
EOF
fi
say "Secrets (visible input; each one is checked before it's saved)"
PROJECT="$PROJECT" bash "$ROOT/infra/bootstrap/secrets.sh"

# ── 5. move the game-server Terraform state into the bucket CI uses ───────────
say "Moving infra/gcp-games state to gs://$BUCKET"
cd "$ROOT/infra/gcp-games"
terraform init -input=false -migrate-state -force-copy -backend-config="bucket=$BUCKET" >/dev/null
echo "  done"

# ── 6. point the workflow at this project, and push = first deploy ────────────
say "Wiring GitHub Actions to this project"
cd "$ROOT"
WORKFLOW=.github/workflows/deploy.yml
if grep -q "WIF_PROVIDER: projects/PROJECT_NUMBER" "$WORKFLOW"; then
  sed -i "/WIF_PROVIDER: /s/PROJECT_NUMBER/$NUMBER/" "$WORKFLOW"
  git add "$WORKFLOW"
  git -c user.name="Skippy bootstrap" -c user.email="noreply@skippy-tel" \
    commit -q -m "Point deploy workflow at GCP project $PROJECT ($NUMBER)"
  if git push -q; then
    echo "  pushed — the first deploy is running in GitHub Actions."
  else
    echo "  push failed. Tell Claude: project number is $NUMBER — it can make this edit."
  fi
else
  echo "  already wired."
fi

say "Done"
echo "  Portal: $PORTAL_URL"
echo "  From now on: tell Claude what to change; a push deploys it."

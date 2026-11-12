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
# Remember the GitHub sign-in after the first time (~/.git-credentials, readable
# only by this Cloud Shell account), so pulls stop asking for it.
git config --global credential.helper >/dev/null || git config --global credential.helper store

say() { printf '\n\033[1;32m== %s\033[0m\n' "$*"; }

# ── 1. terraform (Cloud Shell doesn't keep it between sessions) ───────────────
# (Cloud Shell ships a `terraform` stub that only prints install instructions,
#  so check for the real binary, not just the name.)
real_terraform() { terraform version 2>/dev/null | grep -q '^Terraform v'; }
if ! real_terraform; then
  say "Installing terraform"
  wget -qO- https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor --yes -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
  echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" \
    | sudo tee /etc/apt/sources.list.d/hashicorp.list >/dev/null
  sudo apt-get update -qq && sudo apt-get install -y -qq terraform
fi
hash -r
if ! real_terraform && /usr/bin/terraform version 2>/dev/null | grep -q '^Terraform v'; then
  export PATH="/usr/bin:$PATH"; hash -r        # the stub sits earlier on PATH; use the real one
fi
real_terraform || { echo "terraform didn't install; nothing was changed. Paste this output to Claude."; exit 1; }

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
# The state lives in the project's state bucket (prefix "bootstrap"). From a local state (the first time after this change) init
# migrates it up; on a project that has no bucket yet it starts local and migrates once the apply below has made the bucket.
if gcloud storage buckets describe "gs://$PROJECT-tfstate" >/dev/null 2>&1; then
  terraform init -input=false -migrate-state -force-copy -backend-config="bucket=$PROJECT-tfstate" >/dev/null
else
  terraform init -input=false -backend=false >/dev/null
fi
# A bucket made by hand (gcloud storage buckets create ...) is adopted, not fought: without this, apply stops with a 409 "already exists".
adopt() {   # <terraform address> <bucket name>
  if gcloud storage buckets describe "gs://$2" >/dev/null 2>&1 && ! terraform state list 2>/dev/null | grep -qx "$1"; then
    echo "  adopting the existing bucket gs://$2"
    terraform import -input=false -var "project=$PROJECT" -var "region=$REGION" "$1" "$PROJECT/$2" >/dev/null
  fi
}
adopt google_storage_bucket.tabletop skippy-tel-tabletop
terraform apply -input=false -auto-approve -var "project=$PROJECT" -var "region=$REGION"
if [[ ! -f .terraform/terraform.tfstate ]] || ! grep -q '"type": "gcs"' .terraform/terraform.tfstate 2>/dev/null; then
  terraform init -input=false -migrate-state -force-copy -backend-config="bucket=$PROJECT-tfstate" >/dev/null   # first time: the bucket exists now
fi
NUMBER=$(terraform output -raw project_number)
PORTAL_URL=$(terraform output -raw portal_url)
BUCKET=$(terraform output -raw state_bucket)

# The portal's account used to hold roles/compute.admin; main.tf now gives it
# only the games-VM role (skippyPortalVm), so drop the broad one.
RUNTIME="serviceAccount:skippy-agent@$PROJECT.iam.gserviceaccount.com"
if gcloud projects get-iam-policy "$PROJECT" --flatten=bindings --format='value(bindings.role)' \
     --filter="bindings.role=roles/compute.admin AND bindings.members=$RUNTIME" | grep -q .; then
  gcloud projects remove-iam-policy-binding "$PROJECT" --member="$RUNTIME" \
    --role=roles/compute.admin --condition=None >/dev/null
  echo "  portal account narrowed: compute.admin removed (it keeps only the games-VM role)"
fi

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

# ── 6. deploys: skippy-core makes them now (core/cd, signed in through the skippy-core provider above; docs/no-github.md) ──
say "Deploys"
echo "  skippy-core deploys every commit its own CI passes (core/cd/cd.py); nothing to wire here."

say "Done"
echo "  Portal: $PORTAL_URL"
echo "  From now on: tell Claude what to change; a push to the house git deploys it (through skippy-core)."

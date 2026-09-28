# Secrets — where they live

**Platinum rule (Captain's convention, applies across all projects):**
secrets live in one vault file, `G:\My Drive\vault\.env`, synced via Google
Drive for Desktop. It is **not** committed to any repo and is not reachable
from cloud environments (Cloud Shell, this session, CI) — only from a
machine with that Drive folder mounted (the Windows PC, and the Mac if
Drive for Desktop is set up there too).

Known keys in the vault (add to this list as more get added there):

| Key | Used for |
|---|---|
| `GITHUB_TOKEN` | `git clone`/`push` over HTTPS from a machine without cached GitHub credentials (e.g. a fresh Cloud Shell session) |

## This repo's own secret files

Everything below is gitignored; only the `.example` files are committed.

| File | Holds | Where it's read |
|---|---|---|
| `games/.env` | Game passwords, Dragonwilds `OWNER_ID`, Discord webhook URLs | `scripts/games.sh env` uploads it to the GCP VM's instance metadata |
| `infra/gcp-games/terraform.tfvars` | GCP project id | `terraform`/`tofu apply`, run from Cloud Shell (state lives in Cloud Shell's persistent home, not in git) |
| `.env` (SkippyTel v1, `services/flask-api/`) | `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, etc. | v1 Flask container only |

## Using a vault-style secret from Cloud Shell

Cloud Shell cannot read `G:\...` — that's a local Windows drive letter, not
reachable from Google's cloud VMs. To use a value from the vault there:

```bash
# Fastest: copy the value out of the vault file on your PC, then:
export GITHUB_TOKEN=<paste value>
git clone https://$GITHUB_TOKEN@github.com/aaronsrhodes2/skippy-tel.git

# Or: Cloud Shell ⋮ menu → Upload → the vault .env file, then:
source ~/.env
```
`$GITHUB_TOKEN` in the URL keeps the literal token out of shell history.
Prefer a **short-lived, minimally-scoped** token (repo-read only, days not
years) for anything typed into a session outside your own machine.

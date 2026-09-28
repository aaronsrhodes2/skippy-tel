# Skippy Portal

One page, gated to you and whoever you allow: game server status and
start/stop, links to the other services, and a way into Skippy. Code in
[`portal/`](../../portal).

**Scope of this v1** (see `docs/ROADMAP.md` for what's deferred):
- **Auth:** Google sign-in + a hardcoded email allowlist. No accounts, no
  passwords — remove someone by deleting their email from the list and
  redeploying.
- **Games:** status, connect info, start (with a game picker + auto-sleep
  minutes) and stop for the `infra/gcp-games/` VM. Calls the Compute Engine
  API directly (`portal/gcp.py`) — same logic as `scripts/games.sh`, as a
  library instead of a script.
- **Services:** a static, curated list (`portal/catalog.py`'s `SERVICES`) —
  no live health check. The portal runs on Cloud Run, which has no route
  into the home Tailscale mesh most services live on; edit the list by hand
  for now.
- **Skippy:** a button that opens Claude (a Project if you've set one up,
  otherwise a new chat) — not a custom-built chat interface. That's a
  bigger, separate project if you want it later.

## One-time setup

All of this happens in **Cloud Shell**, same as the game server setup.

1. **OAuth client for "Sign in with Google"** (separate from any
   gcloud/Terraform credentials — this one only ever sees an email address):
   - GCP Console → **APIs & Services → OAuth consent screen** → configure
     if you haven't already (External, or Internal if this is a Workspace org)
   - **APIs & Services → Credentials → Create Credentials → OAuth client ID**
     → Application type **Web application**
   - Authorized redirect URI: `https://<the Cloud Run URL from step 3>/auth/callback`
     (you'll need to deploy once first to learn the URL, then add this and
     redeploy — Cloud Run URLs are stable across redeploys once created)
   - Copy the **Client ID** and **Client secret**

2. **A session secret:**
   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```

3. **Deploy:**
   ```bash
   cd ~/skippy-tel
   gcloud run deploy skippy-portal \
     --source portal \
     --region us-west1 \
     --service-account skippy-agent@skippy-tel.iam.gserviceaccount.com \
     --allow-unauthenticated \
     --set-env-vars FLASK_SECRET_KEY=<from step 2>,GOOGLE_CLIENT_ID=<from step 1>,GOOGLE_CLIENT_SECRET=<from step 1>,ALLOWED_EMAILS=aaron.s.rhodes@gmail.com,GCP_PROJECT=skippy-tel,GCP_ZONE=us-west1-b,GCP_INSTANCE=skippy-games
   ```
   `--allow-unauthenticated` sounds backwards, but it's correct here: it
   means Cloud Run's own IAM layer doesn't gate the request, because the
   app does that itself (Google sign-in + the email allowlist). Without it,
   Cloud Run would 403 every request before Flask ever saw it.
   It reuses the `skippy-agent` service account already granted
   `roles/compute.admin` for the games VM — no new key, no new account.

4. Take the printed **Service URL**, go back to step 1's OAuth client, add
   `<that URL>/auth/callback` as an authorized redirect URI, save.

5. Open the URL, sign in, confirm the dashboard loads and shows the VM's
   real status.

To add a friend: add their email to `ALLOWED_EMAILS` (comma-separated) and
redeploy with the same command (env vars aren't stored anywhere else, so
resending the full list each time is normal — a small script or Secret
Manager reference removes that annoyance later if it gets tedious).

## Redeploying after a code change
```bash
cd ~/skippy-tel && git pull
gcloud run deploy skippy-portal --source portal --region us-west1
```
(Cloud Run keeps the previous env vars/service account on a redeploy that
doesn't pass `--set-env-vars` — no need to repeat them.)

## Known rough edges (v1)
- Changing which games run while the VM is **already on** only updates its
  metadata — it won't reconfigure the running containers until the next
  stop/start. Matches a limitation already in `scripts/games.sh`.
- `portal/gcp.py`'s exact Compute Engine client attribute names (notably
  `nat_i_p`) couldn't be verified against a live API while writing this —
  they're correct for the pinned `google-cloud-compute` version, but if the
  dashboard errors with an `AttributeError`, that's the first place to look.

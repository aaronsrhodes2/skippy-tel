# GitHub out: our own git, our own CI, our own registry (plan, 2026-10-25)

The Captain's decision: GitHub is a weakness; it leaves the architecture. Our code lives in plain git, our tests and deploys run on our
own machines in plain Docker.

## The decisions (2026-10-25)

| Question | Answer |
|---|---|
| Where the repos live | **skippy-pc's D: drive**: bare repos in `D:\trunk\git\<name>.git`. skippy-core reaches them as `/mnt/pc/trunk/git` through the data trunk (docs/data-trunk.md) |
| The off-site copy | **A GCS bucket**: a nightly `git bundle` of every repo |
| Tests and deploys | **skippy-core runs them.** The portal stays on Cloud Run (its uptime comes from not living in the house), deployed by skippy-core with a Google service-account key |
| Third-party images and packages now fetched from GitHub | **Mirrored locally**: a registry and a package cache on skippy-core |

## What GitHub does today, and what replaces each

| Today (GitHub) | Tomorrow |
|---|---|
| 22 repos under `aaronsrhodes2` (origin of everything) | Bare repos in `D:\trunk\git`. Every checkout's `origin` becomes `D:/trunk/git/<name>.git` on skippy-pc and `/mnt/pc/trunk/git/<name>.git` on skippy-core |
| `test.yml`, `run-tests.yml`: tests on every push | **skippy-core's CI** (`core/ci`): it polls the bare repos every 30 s (skippy-core's deployer already works this way), checks out each new commit of main into a scratch folder, and runs `tools/affected_tests.py` (the full suites when it can't tell) in throwaway Docker containers. Results go to the portal's log intake (docs/status-design.md) and to the Status page |
| `deploy.yml`: tests, Terraform for `infra/gcp-games`, the portal to Cloud Run, the smoke test | After green tests, skippy-core: `docker build` the portal (and Mentat cloud) → push to Artifact Registry → `gcloud run deploy`; Terraform in a container; the same smoke test. Authenticated by a service-account key kept root-only on skippy-core |
| `core.yml`: the gate on skippy-core's own deploys | The local CI's result for that commit. The deployer reads `/mnt/pc/trunk/git/skippy-tel.git` instead of fetching from GitHub, and asks the local CI instead of GitHub's Actions API |
| `rollback.yml`, `tools/rollback_pick.py` | skippy-core keeps the image digest of every good deploy; a rollback redeploys one. The Recovery key asks skippy-core |
| `ops.yml`: VM status, logs, start/stop, discord-setup | The same commands run by skippy-core's `gcloud` (or by the portal itself, which already holds the VM's identity), as a Status page action |
| `domains.yml` (`tools/domains.py`) | Run once by skippy-core when DNS changes |
| Workload Identity Federation (Google trusts GitHub's tokens) and the `github-actions-token` secret | Retired. The deployer identity is used through its key on skippy-core instead |
| Portal: the Recovery and ship cards read GitHub's API (`recovery.py`, `ship.py`, `loop_timer.py`) | They read skippy-core's CI and deploy state from its report (`/home/core-report`, Status design Part 1) |
| `new_folder.py` makes a private GitHub repo | It makes a bare repo in `D:\trunk\git` |
| The private Skippy pushes to GitHub, and its workshop clones pull from it | Push to and pull from the bare repos. `projects.json` gets local remotes |
| The games VM pulls the Garry's Mod addon from `github.com/.../sigma-ground` | CI puts the addon's tarball in the bucket; the VM fetches it from there (it already reaches GCS) |
| Mentat's image `ADD`s sigma-ground's tarball from GitHub | CI passes sigma-ground's checkout as the build context; nothing is fetched |
| The portal image downloads Star Control's release from GitHub | CI builds it from `star-control-precursors` (or keeps the tarball in the bucket) and passes it in the build context |
| Images on ghcr.io (llama-swap, python-matter-server, Valheim) and anything pip fetches from GitHub | **The mirror.** A `registry:2` container on skippy-core (tailnet only) holds a copy of each pinned image, and compose files name the mirror. A pip cache serves packages the same way. The games VM can't reach the house, so its images are copied to Artifact Registry in the project instead |

## Progress

- **Phase 1, done 2026-10-25:**
  - 22 bare repos are in `D:\trunk\git`, each with a `post-receive` hook that forwards to GitHub;
  - every checkout's `origin` points there, and so does skippy-core's deployer checkout;
  - New folder makes a bare repo;
  - the nightly off-site bundle waits on the bucket.
- **Phase 2, done 2026-10-25:**
  - `core/ci/ci.py` on skippy-core (timer every minute, its own copy in `/opt/skippy-core/ci`: re-run `core/ci/install.sh --apply`
    after changing it) tests main's newest commit in plain Docker: run-tests.yml's suite and flask-api jobs, and the shell syntax;
  - results are in `/var/lib/skippy-core/ci/<repo>/results/<sha>.json`, each job's log beside them;
  - the first green run took 85 s for all of SkippyTel's tests (2,806);
  - the deployer gates on it (`CI_LOCAL` in core.env): green deploys, a failed test is refused, untested waits;
  - the simulator (core.yml's `sim` job) isn't run: it stood in for the box, and the box is now the test;
  - skippy-core's deploy timer is on (2026-10-25, every 2 minutes): push to main, skippy-core's CI tests it, and its deployer
    deploys it only when green. The first gated deploy (eeac5ff) passed every smoke check.

- **Phase 3, done 2026-10-25:**
  - skippy-core deploys the cloud: `core/cd/cd.py` (timer every minute, its own copy in `/opt/skippy-core/cd`: re-run
    `core/cd/install.sh --apply` after changing it) builds the portal and Mentat images, pushes them to Artifact Registry,
    deploys to Cloud Run, runs deploy.yml's smoke tests and puts the old revision back on any failure; Terraform for
    `infra/gcp-games` runs when it changed. What it deploys, and the pins, are in `core/cd/deploy.json`;
  - it signs in to Google with no key: its own signing key (`/etc/skippy-core/gcp-oidc`, root-only), trusted through the
    `skippy-core` provider in infra/bootstrap (Google holds only the public JWKS; the project forbids service-account keys);
  - the portal reads its deploys from skippy-core (`DEPLOYS_FROM=house`, `POST /home/deploys`, `portal/deploys.py`): the ship
    cards and Recovery's redeploy and rollback go through it;
  - GitHub's `deploy`, `core`, `test` and `run-tests` workflows are deleted. `rollback`, `ops` and `domains` stay as the phone's
    way back while GitHub is still a mirror. First automatic deploy: run 2, 6dd397f (CI 53 s, then live).
  - Still on GitHub's API, to move in phase 5: the `ops` and `domains` workflows, the bridge's `ship` card links, `loop_timer.py`.

## Phases (each ends working and checked; GitHub keeps receiving pushes as a mirror until the last)

1. **Git home:**
   - every repo cloned bare into `D:\trunk\git`;
   - every checkout, worktree, skippy-core's deployer checkout and the bridge's `projects.json` pointed at it;
   - `new_folder.py` changed;
   - the nightly bundle to the bucket.
2. **CI on skippy-core:**
   - `core/ci` runs the tests on every push and reports them;
   - skippy-core's own deploys gated by it.
3. **CD on skippy-core (done):**
   - the portal and Mentat-cloud deploys, Terraform, the smoke test and rollback;
   - the Recovery and ship cards and the ops commands moved off GitHub's API.
4. **No fetch from GitHub:**
   - the Mentat and Star Control build contexts;
   - the Garry's Mod addon from the bucket;
   - the registry and pip mirrors.
5. **Cut-over:**
   - GitHub stops receiving pushes; the workflows are deleted;
   - Workload Identity Federation and `github-actions-token` are retired;
   - CLAUDE.md, PLATINUM §17 (push to main) and the docs are rewritten.
   - The GitHub repos are archived. **Deleting them is the Captain's call, repo by repo**: some are public (sigma-ground, orion-trail,
     star-control-precursors, quarksum, ...) and others may use them.

## What only the Captain can do

- **A Cloud Shell run of `infra/bootstrap`** after phase 1's change:
  - it makes the backup bucket and a key for the deployer identity;
  - the key goes into skippy-core's root-only settings and is never shown.
- **Each GitHub repo's archiving or deletion** at the end.

## Risks, said plainly

- **Pushes depend on skippy-pc.** With skippy-pc off, nothing can be pushed and CI waits. The private Skippy and every checkout are on
  skippy-pc anyway, so nothing is lost but the timing.
- **Off-site lags by up to a day.** The nightly bundle is the off-site copy; a disaster loses at most the day's commits. An extra
  bundle after each deploy shortens that.
- **The VM can't use the house mirror.** The games VM and Cloud Run sit outside the house, so they use Artifact Registry copies.

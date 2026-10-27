---
name: ci-tests
description: Run SkippyTel's tests on Linux exactly the way skippy-core's CI runs them (python:3.12-slim in Docker, the same requirements and pytest flags), from this Windows PC. Use before pushing anything that touches core/ (its tests need Linux: the deployer imports fcntl), when a test passes on Windows but CI says red, and to check main is green before a deploy depends on it.
---

# CI tests, on Linux, from skippy-pc

skippy-core's CI (`core/ci/ci.py`, a timer every minute) is the only gate before a deploy: a red `main` holds back **every** skippy-core
deploy, not just the commit that broke it. Windows runs miss things it catches:

- **`core/` can't run on Windows at all:** `core/deployer/deployer.py` imports `fcntl` (`ModuleNotFoundError` on Windows).
- **This PC reaches things CI can't.** Tests that quietly lean on skippy-core, the bridge on :9337 or the vault pass here and fail there.
  Example: on 2026-10-26, 11 bridge tests passed here because Core answered from this PC, and got 400s in CI.
- **One pytest session holds many folders**, so two test files with the same name (`portal/tests/test_car.py` and `core/tests/test_car.py`)
  fail with `import file mismatch`, and only in CI. Give every test file a name unique across the repo.

## 1. Take the job from the source of truth

Read `PLANS["skippy-tel"]` in `core/ci/ci.py`. Each job is `{"name", "reqs": [requirement files], "cmd": ...}`, with `{cpus}` in `cmd`.
Today's jobs are `suite`, `flask-api`, `services` and `shell-syntax`, but read the file, because it changes.

## 2. Run it in the same image

From the repo root, in bash (Git Bash). `MSYS_NO_PATHCONV=1` and `$(pwd -W)` keep Docker's paths Windows-safe (Platinum §20):

```bash
cd /d/Aaron/development/SkippyTel
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W)":/src -w /src python:3.12-slim sh -c '
  apt-get -qq update >/dev/null 2>&1; apt-get -qq install -y git >/dev/null 2>&1
  pip install -q -r portal/requirements.txt -r tools/requirements-ci.txt >/dev/null 2>&1
  python -m pytest core/tests services/claude-bridge/tests -q -n 8 --dist loadfile -p no:cacheprovider 2>&1 | tail -15'
```

- Swap in the job's `reqs` and the folders you need. Run the job's whole `cmd` before a push that touches many areas.
- `git` is needed: the deployer and CI tests build real repos in temp folders.
- For a quick look at only the failures, pipe through `grep -E '^(FAILED|ERROR|E   )'`.
- It takes about a minute for core plus the bridge, and longer the first time while pip downloads.

## 3. Read the result honestly

- **A failure under `-n 8` that passes when its file runs alone** (`python -m pytest core/tests/test_ci.py -q`) is a timing flake from
  parallel load. Say that's what it is; don't call it green or red without saying so.
- **A failure from someone else's commit:** check `git log` for the test's area. Fix it only when asked, or when it blocks something you were
  asked to ship, and say whose commit it came from.
- **What CI itself said:**
  ```bash
  ssh skippy-core 'sudo python3 -c "import json;d=json.load(open(\"/var/lib/skippy-core/ci/skippy-tel/latest.json\"));print(d[\"sha\"][:7],d[\"state\"]);[print(j[\"name\"],j[\"state\"]) for j in d[\"jobs\"]]"'
  ```
  A failed job's `tail` holds the pytest summary. The deployer's view: `/var/lib/skippy-core/deployer/status.json` (`waiting-ci`,
  `known-bad`, `ok`). `python tools/loop_timer.py --sha <commit>` shows one commit's whole trip (CI, the deploy, serving) from these records.

## 4. Windows runs still have a place

For a quick check of portal, reporter or bridge code, run pytest on Windows directly (`cd portal && python -m pytest tests -q -n 8`), or
`python tools/affected_tests.py` for only what the change can break. Anything in `core/`, or anything about to be pushed, gets the Linux run above.

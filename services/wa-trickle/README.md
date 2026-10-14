# wa-trickle — Wolfram|Alpha and Mentat, on the same questions, slowly and free

Wolfram|Alpha's free API tier allows about 2,000 non-commercial calls a month. This service spends a small daily budget of them
(`WA_DAILY_BUDGET`, default 60) on a queue of questions: **one call per question, ever** (the answer is frozen when it arrives), and
puts the same question to Mentat (`POST mentat-home:8080/ask`) — again whenever Mentat's commit changes (`GET /version`), so
Mentat's progress is measured against a frozen Wolfram answer without another Wolfram call. Why: the Captain's goal is a local
Wolfram contender (sigma-ground `misc/CONTENDER_PLAN.md`); paid Wolfram use is out, so the comparison is a slow, free trickle.

```bash
bash services/wa-trickle/setup.sh                      # state/wa-trickle/wolfram.env: the AppID only, copied from the vault
docker compose up --build -d wa-trickle                # port 127.0.0.1:3010
curl -s localhost:3010/status                          # questions, budget today/month, days to finish, Mentat's commit
# in sigma-ground:  python tools/wa_trickle_load.py --suite math329     (enqueue)   |   python tools/wa_trickle_score.py   (score)
```

State is one SQLite file (`state/wa-trickle/trickle.sqlite`; gitignored). Deploying from a git worktree: set `WA_TRICKLE_STATE` to the
main checkout's `state/wa-trickle`. Wolfram answers are stored raw (pods) and scored later on the sigma-ground side, so the extractors
can improve without a new call. A 403 from Wolfram (quota or bad key) pauses the queue until the next month; a network error costs no
call and the question stays queued. Tests: `python -m pytest services/wa-trickle/tests`.

# Search and speech pop-ups: `search_memory` and `diarize`

Two pop-ups ([docs/popups.md](popups.md): one registry entry each, their own containers) for the Captain, both admin-only and both on
skippy-pc. Code, entries and containers: `services/search-popups/` (`popups.json`, `compose.yml`).

| pop-up | what it does | models | where it runs |
|---|---|---|---|
| `search_memory` | a question in, the passages of the Captain's own text that answer it out, each with its source | BAAI **bge-m3** (embedder) + **bge-reranker-v2-m3** (reranker), MIT, fp16 | skippy-pc's card, loaded per search, under the reporter's GPU lock (Ollama unloaded first) |
| `diarize` | an attached recording back as a speaker-labelled transcript | pyannote **segmentation-3.0** (MIT) + **WeSpeaker ResNet34** (VoxCeleb), via sherpa-onnx; then the **existing whisper** | skippy-pc's CPU; whisper is the container voice-gate already uses |

## search_memory

**What it reads** (all of it already on skippy-pc; nothing is copied off the machine):

- the private Skippy's saved conversations: `D:\Aaron\conversations\private-skippy` (a `.turns.json` wins over the `.md` of the same conversation);
- the Skippy Memory archive. Docker can't see the Drive folder (`G:` mounts empty in a container: checked), so the container reads the archive's
  own source, Claude Code's `~/.claude/projects` transcripts, through the archive's own parser (`memory/parsers/claude_code.py` +
  `memory/store.py` `render`). Same words: 3,807 of 3,812 passages identical (the other 5 were the session still being written). Claude Code
  clears old transcripts after a few weeks while the archive keeps them, so the index **keeps a memory passage when its transcript
  disappears**. Archive conversations from other AIs (an export dropped in its inbox; none yet) come through the bridge's `/memory` door,
  which cuts a long conversation to its last 60,000 characters;
- Mentat's research log: `SkippyTel/state/mentat-research/<YYYY-MM>.jsonl`, one passage per request (question or tool + arguments, and the answer).

**The index** (`index.py`): one SQLite file, `SkippyTel/state/search-popups/search.db`. A passage is one turn (the Captain's words and the
reply, tool calls named but their output left out), cut into windows of 1,500 characters with 200 of overlap, its document's title in
front. Each search starts with a refresh: a file whose mtime and size are unchanged isn't read; a changed file has only its new passages
embedded (a passage's key is a hash of its place and text). Then bge-m3 finds the 30 nearest passages, bge-reranker-v2-m3 reorders them,
and the best `k` (default 6) come back as text (or JSON with `format: json`): title, source, date, place in the conversation, relevance,
an excerpt. The question is never logged. Between searches the service holds no GPU memory.

### Benchmark (2026-10-24, skippy-pc, the real corpus: 4,510 passages, ~5 MB)

22 paraphrased questions about real conversations, worded to share few words with the passage that answers them ("fixing the names of my
film files" for "rename these movies"); a hit is the right conversation. `bench.py` reruns it.

| embedder + reranker | hit@1 | hit@5 | MRR@10 | index build | embedder VRAM | rerank 30 / question | reranker VRAM |
|---|---|---|---|---|---|---|---|
| Qwen3-Embedding-0.6B alone | 0.55 | 0.82 | 0.69 | 45 s | 2.2 GB | | |
| Qwen3-Embedding-0.6B + Qwen3-Reranker-0.6B | 0.82 | 0.91 | 0.85 | | | 1.12 s | 4.4 GB |
| Qwen3-Embedding-0.6B + bge-reranker-v2-m3 | 0.82 | 0.95 | 0.87 | | | 0.17 s | 1.2 GB |
| bge-m3 alone | 0.73 | 0.91 | 0.80 | 19 s | 1.2 GB | | |
| bge-m3 + Qwen3-Reranker-0.6B | 0.77 | 0.91 | 0.82 | | | 1.46 s | 4.3 GB |
| **bge-m3 + bge-reranker-v2-m3** (chosen) | **0.82** | **0.91** | **0.86** | **19 s** | **1.2 GB** | **0.17 s** | **1.2 GB** |

GPU numbers are fp16 on the 16 GB card with Ollama unloaded and the other residents (Whisper, the voices, the picture studio) in place.
The two bge models are as good as the best pair (one question in 22 apart) at half its index time and VRAM; the Qwen3 reranker was slower
and no better. The reranker matters most: it lifts hit@1 from 0.55-0.73 to 0.82.

**CPU vs GPU vs skippy-core.** On the CPU (8 threads, the Docker VM's share) the 0.6B Qwen3 embedder managed ~0.5 passages/s (the first
index: ~2.5 hours); bge on the CPU is in the table below. On the card both bge
models together need ~2.4 GB; the card has that only with Ollama out (Ollama's qwen3:8b alone doesn't fit: it runs 19 % on the CPU), which
is exactly what a pop-up with `vram_gb > 0` gets from the reporter. skippy-core would also do (llama.cpp serves embedding and reranking
models), but it would need its own llama-swap slots and the text would cross the tailnet for every refresh; the card is enough, so it stays
on skippy-pc. **Fits beside the resident voices + Ollama? No, not beside Ollama; yes once the reporter has unloaded it (~3 GB headroom left).**

| bge-m3 + bge-reranker-v2-m3, 8 CPU threads (host, fp32) | GPU (fp16) |
|---|---|
| embedding: 1.0 passage/s, so the first index would take ~75 min | 235 passages/s: 19 s |
| one question embedded: 31 ms | 2 ms |
| reranking 30 passages: **31.5 s** a question | **0.17 s** |

So the CPU is out: a search would take half a minute after a 75-minute first build.

The Docker VM has 8 GB for every container (it sat at 6.3 GB in use); bge-m3 ships only a float32 `pytorch_model.bin` that `torch.load` reads
whole (2.3 GB), so `seed_weights.py` converts both models to fp16 safetensors once (on the host) into the Docker volume
`skippytel_search-popups-weights`, and they load straight onto the card, tensor by tensor. (The volume also dodges Docker's ~8 MB/s
reads from D:.) The container's `mem_limit` is 3 GB.

## diarize

**Who spoke when** is sherpa-onnx's offline diarization on the CPU: pyannote's segmentation-3.0 finds the speech and the speaker changes, a
voice print per stretch, the prints are clustered (cosine threshold 0.5, the count found by the clustering). **pyannote's own pipeline was
not usable**: its weights on Hugging Face are gated (accepting terms with an account token), and the vault has no Hugging Face token
(`HF_ID` is an id, not a token). The same segmentation-3.0 model, MIT-licensed, is published as ONNX by k2-fsa (sherpa-onnx's authors) with
no gate: `D:\models\sherpa-onnx` (`SOURCE.json` has the URLs and sha256). **What each said**: each turn is cut out and posted to the whisper
container behind voice-gate (`skippytel-whisper`, faster-whisper medium on the card, already resident); no second whisper is loaded.
That whisper always answers in English (`task="translate"`). It gets **no prompt**: the first version passed the previous turn's last words,
and on the test clip whisper then invented a line for the next speaker ("Today we're going to talk about the outside of the lighthouse",
twice, over a narrator saying something else entirely); without it every line came out right. An empty prompt would make that whisper fall
back to its voice-command primer, so a single space is sent.

### Benchmark (a 47 s, three-speaker clip with known turns: a real narrator recording and two Windows voices)

| segmentation | voice prints | speakers found | confusion | missed speech | diarization time | RAM |
|---|---|---|---|---|---|---|
| **fp32** | **WeSpeaker ResNet34 (VoxCeleb)** | **3 of 3** | **0 %** | 15 % | **4.3 s** | **185 MB** |
| fp32 | 3D-Speaker ERes2Net (Chinese) | 6 | 28 % | 17 % | 5.9 s | 260 MB |
| int8 | WeSpeaker | 3 | 0 % | 19 % | 4.1 s | 181 MB |
| int8 | 3D-Speaker | 4 | 11 % | 19 % | 4.8 s | 269 MB |

"Missed speech" is mostly the silence at the start of the narrator's cuts, counted as speech in the reference. Telling it the speaker count
(3) made WeSpeaker merge two of the three, so the pop-up doesn't offer it. No VRAM of its own: 0 GB, so it needs no GPU lock and Ollama
stays loaded.

End to end: see "Verified" below.

## Verified (2026-10-24, on skippy-pc, through the home reporter's own `run_job`, the step before the portal's card)

- **diarize**, the 47 s three-speaker clip: 3 speakers, 6 turns, every speaker right and every line right, in **18.2 s** (11.8 s finding
  who spoke when on the CPU, 4 s of whisper for six turns).

- **search_memory**, "what did we decide about the steam link asking for a PIN?": the right conversation (the 2026-10-01 Steam Link
  session) comes back first. Through the reporter's `run_job` the search finished in the service but the reporter had stopped waiting:
  **484 s** against a 480 s limit, with another pop-up's GPU benchmark running on the card at the same time. Nearly all of it was
  loading the two models onto the card: inside the Docker VM, host-to-card copies ran at ~20 MB/s while the card was shared
  (1.1 GB of raw tensors in 55 s; reading the file itself takes 8 s). On the quiet card in the benchmark above (host Python), the same
  load is under a minute and a search is 2-3 s once loaded. **Still to verify: one search on a quiet card through the reporter.**
  If per-search loading stays slow in Docker, the choices are keeping the two models resident (2.4 GB of the card, as the voices
  hold 3 GB), or moving them to skippy-core.

## Run it

```bash
cd /d/Aaron/development/SkippyTel
python services/search-popups/seed_weights.py                     # once: D:/models -> fp16 -> the Docker volume
docker compose -f services/search-popups/compose.yml build
docker compose -f services/search-popups/compose.yml up -d
docker exec skippy-popup-search python -c "import urllib.request as u; print(u.urlopen(u.Request('http://127.0.0.1:47430/refresh', b'{}')).read())"
python -m pytest services/search-popups/tests -q                  # their own session (CI's lists are in run-tests.yml)
python services/search-popups/bench.py --device cuda               # the search benchmark (host Python; Ollama unloaded)
```

The first `/refresh` builds the whole index (~20 s of GPU after the models load); every search after that refreshes by itself. The bridge
door (other AIs' archive conversations) needs `SKIPPY_HOME_TOKEN` in the deploying shell's environment; without it that part is skipped.

## Privacy

Both are `audience: admin`. The text, the index, the recordings and the models stay on skippy-pc; the only thing that leaves is the answer
to the Captain's own question (or his transcript), the way every pop-up's result goes to the chat. Neither service writes the question or
the audio anywhere; whisper's turn files are its own tempfiles, deleted after each turn.

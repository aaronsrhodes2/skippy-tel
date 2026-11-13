"""purple's three limits, enforced (the Captain, 2026-11-12/13: "Leak, Spend, Lie ... everything else is fair game"). docs/purple-rules.md is the policy,
this is its PreToolUse hook: Claude Code runs it before EVERY tool call of a Workshop run (the lane guard only sees file edits and Bash).

  * LEAK   a vault value or a secret-shaped string on its way out of the machine: in a request (curl, WebFetch, a search query, a form), in a file that git would
           publish (a tracked, not-ignored file), in a commit or push (the diff is scanned), in a message to anyone. Everything pushed is public (PLATINUM §23).
           A local model reading a secret is NOT a leak: nothing here limits Read.
  * SPEND  paid CLIs and APIs (gcloud create/deploy, terraform apply, aws, stripe ...), a paid key named in a command, a card number, a sub-agent on a paid model
           (Core runs), billable resources added by a push (infra/**: new resource blocks).
  * LIE    what a hook can see before a call: a commit whose diff adds skipped / xfail / deleted tests or loosened assertions (a test that was truly wrong is
           allowed with a `TEST-FIX:` line in the commit message that says why). The rest of lying (a claim with no call behind it) is toolchain.py's and the stop gate's.

Every refusal is appended to state/purple-audit.jsonl (a line: when, rule, tool, why, a short summary with secrets masked), and a leak refusal says how to proceed
(remove it, note it in state/leaks.md). Exit 2 = refused (the reason on stderr, which Claude Code shows the model); 0 = allowed. Anything unknown is allowed and
not audited: the lane guard, the prompt and the three limits' meaning do the rest. No vault readable = only the secret-shaped patterns are checked.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VAULT = Path(os.environ.get("SKIPPY_VAULT", r"G:\My Drive\vault\.env"))
AUDIT = Path(os.environ.get("SKIPPY_AUDIT", str(ROOT / "state" / "purple-audit.jsonl")))

# ── secrets ──
_SECRET_KEY = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD|PASS|PIN|AUTH|CREDENTIAL|COOKIE|SSN|PASSPORT|BANK|ROUTING|ACCOUNT|OTA|PSK|CERT)", re.I)
SECRET_PATTERNS = [re.compile(p) for p in (
    r"sk-ant-[A-Za-z0-9_-]{20,}", r"sk-[A-Za-z0-9]{32,}", r"sk_[0-9a-f]{32,}", r"AIza[0-9A-Za-z_-]{35}", r"gh[pousr]_[A-Za-z0-9]{36,}",
    r"github_pat_[A-Za-z0-9_]{50,}", r"xox[baprs]-[A-Za-z0-9-]{10,}", r"AKIA[0-9A-Z]{16}", r"ya29\.[0-9A-Za-z_-]{20,}", r"tskey-[A-Za-z0-9-]{20,}",
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----", r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    r"[MN][A-Za-z\d]{23,25}\.[\w-]{6}\.[\w-]{27,}")]


def load_secrets(path: Path = VAULT) -> list[str]:
    """The vault's secret values (not its settings): a key named like a secret, a value long and mixed enough to be one. Held in memory only."""
    out: list[str] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip("\"'")
            if len(v) >= 10 and _SECRET_KEY.search(k) and not re.fullmatch(r"(https?://\S+|[\w.-]+\.(?:local|com|net|org|live))", v) and (len(v) >= 16 or (re.search(r"\d", v) and re.search(r"[A-Za-z]", v))):
                out.append(v)
    except OSError:
        pass
    return sorted(set(out), key=len, reverse=True)


def has_secret(text: str, secrets) -> str | None:
    """What kind of secret the text holds ("a vault value", "a token-shaped string"), or None."""
    if not text:
        return None
    for s in secrets:
        if s in text:
            return "a vault value"
    for p in SECRET_PATTERNS:
        if p.search(text):
            return "a key or token"
    return None


def mask(text: str, secrets) -> str:
    for s in secrets:
        text = text.replace(s, "[secret]")
    for p in SECRET_PATTERNS:
        text = p.sub("[secret]", text)
    return text


def luhn_card(text: str) -> bool:
    for m in re.finditer(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)", text):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19:
            total, alt = 0, False
            for ch in reversed(digits):
                d = int(ch)
                if alt:
                    d = d * 2 - 9 if d * 2 > 9 else d * 2
                total, alt = total + d, not alt
            if total % 10 == 0 and len(set(digits)) > 1:
                return True
    return False


# ── where things go ──
_LOCAL_HOST = re.compile(r"^(?:localhost|127\.|0\.0\.0\.0|\[?::1|192\.168\.|10\.|172\.(?:1[6-9]|2\d|3[01])\.|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.|skippy-[\w-]+|[\w-]+\.local|[\w.-]+\.ts\.net|host\.docker\.internal)", re.I)
_SEND = re.compile(r"\b(?:curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod|nc|ncat|scp|rsync|sftp|ftp)\b", re.I)
_PASTE = re.compile(r"pastebin|paste\.|hastebin|gist\.github|transfer\.sh|file\.io|0x0\.st|ix\.io|termbin|dpaste|ghostbin|webhook\.site|requestbin|ngrok", re.I)
_URL = re.compile(r"https?://([^/\s\"'<>]+)", re.I)
_GH_POST = re.compile(r"\bgh\s+(?:issue|pr|gist|release|api|repo\s+create|workflow)\b[^;&|\n]*", re.I)
_MESSAGING = re.compile(r"discord(?:app)?\.com/api|hooks\.slack\.com|api\.telegram\.org|api\.twilio\.com|smtp|sendmail|mailgun|sendgrid|api\.pushover", re.I)
MESSAGING_TOOL = re.compile(r"discord|slack|gmail|email|send_?message|sms|twilio|telegram|whatsapp", re.I)

PAID_HOSTS = re.compile(r"api\.anthropic\.com|api\.openai\.com|generativelanguage\.googleapis\.com|api\.elevenlabs\.io|api\.stripe\.com|"
                        r"fleet-api\.prd\.[\w.]+\.vn\.cloud\.tesla\.com|billing\.googleapis\.com|cloudbilling|api\.replicate\.com|api\.together\.xyz|"
                        r"api\.mistral\.ai|api\.cohere\.|api\.deepgram\.com|api\.assemblyai\.com|api\.openrouter\.ai|openrouter\.ai/api|"
                        r"api\.perplexity\.ai|api\.groq\.com|api\.fireworks\.ai|api\.x\.ai|api\.huggingface\.co/.*inference", re.I)
PAID_KEYS = re.compile(r"\b(?:ANTHROPIC_API_KEY|OPENAI_API_KEY|GEMINI_API_KEY|GOOGLE_API_KEY|ELEVENLABS_API_KEY|ELEVEN_API_KEY|STRIPE_[A-Z_]*KEY|REPLICATE_API_TOKEN|"
                       r"OPENROUTER_API_KEY|MISTRAL_API_KEY|AWS_SECRET_ACCESS_KEY|AZURE_[A-Z_]*KEY|TESLA_[A-Z_]*(?:TOKEN|SECRET))\b")
PAID_CLI = re.compile(r"(?:^|[\s;&|(])(?:aws|az|doctl|heroku|flyctl|fly|vercel|netlify|stripe|linode-cli|vultr-cli|ibmcloud|oci)\s+\S", re.I)
_GCLOUD_PAID = re.compile(r"\bgcloud\b[^;&|\n]*\b(?:create|deploy|insert|import|add-iam|enable|beta\s+billing|billing)\b", re.I)
_TERRAFORM = re.compile(r"\bterraform\s+(?:apply|destroy|import)\b|\btofu\s+(?:apply|destroy)\b", re.I)
PAID_MODEL = re.compile(r"claude|opus|sonnet|haiku|gpt|gemini|o[134]-|grok|mistral-(?:large|medium)|command-r", re.I)

# ── lying, as far as a diff shows it ──
_TEST_PATH = re.compile(r"(?:^|/)(?:tests?|__tests__|spec)/|(?:^|/)test_[^/]*\.py$|[^/]*_test\.(?:py|go|js|ts)$|\.(?:test|spec)\.[jt]sx?$")
_WEAKEN = [
    (re.compile(r"^\+.*(?:@pytest\.mark\.(?:skip|xfail)|pytest\.skip\(|pytest\.xfail\(|@unittest\.(?:skip|expectedFailure)|\b(?:it|test|describe)\.skip\b|\bxit\(|\bxdescribe\(|\bt\.Skip\()"), "adds a skipped or expected-to-fail test"),
    (re.compile(r"^\+\s*assert\s+(?:True|1)\s*(?:#.*)?$"), "adds an assertion that cannot fail"),
    (re.compile(r"^\+\s*(?:except(?:\s+\w+)?\s*:\s*pass|pass\s*#\s*TODO)"), "swallows an error in a test"),
    (re.compile(r"^-\s*(?:assert\b|self\.assert|expect\()"), "removes an assertion"),
]


def weakened_tests(diff: str) -> str | None:
    """Why this diff weakens the tests, or None. Reads only the lines of test files; a removed assertion counts only when not replaced in the same hunk."""
    path, removed, added, reasons = "", 0, 0, []
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            m = re.search(r" b/(\S+)$", line)
            path = m.group(1) if m else ""
        elif line.startswith("deleted file mode") and _TEST_PATH.search(path):
            reasons.append(f"deletes the test file {path}")
        elif _TEST_PATH.search(path) and line[:1] in "+-" and not line.startswith(("+++", "---")):
            for rx, why in _WEAKEN:
                if rx.search(line):
                    if why == "removes an assertion":
                        removed += 1
                    else:
                        reasons.append(f"{why} in {path}")
            if re.match(r"^\+\s*(?:assert\b|self\.assert|expect\()", line):
                added += 1
    if removed > added:
        reasons.append(f"removes {removed - added} more assertion(s) than it adds")
    return "; ".join(dict.fromkeys(reasons)) or None


def _git(args: list[str], cwd: str, timeout: int = 20) -> str:
    try:
        r = subprocess.run(["git"] + args, cwd=cwd or None, capture_output=True, text=True, timeout=timeout, errors="ignore")
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _staged(cwd: str) -> str:
    return _git(["diff", "--cached", "-U0", "--no-color"], cwd)


def _unpushed(cwd: str) -> str:
    for ref in ("@{upstream}", "origin/main", "origin/master"):
        out = _git(["diff", "-U0", "--no-color", f"{ref}..HEAD"], cwd, 40)
        if out:
            return out
    return _git(["show", "-U0", "--no-color", "HEAD"], cwd)


def added_lines(diff: str) -> str:
    """The added lines of a diff, minus lines marked `secret-fixture` (a made-up key in a test: the guard's own tests must be pushable)."""
    return "\n".join(line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++") and "secret-fixture" not in line)


def new_billable(diff: str) -> str | None:
    """A push that adds billable infrastructure: a new resource block in infra/**/*.tf (a changed one is the pipeline's business)."""
    path, hits = "", []
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            m = re.search(r" b/(\S+)$", line)
            path = m.group(1) if m else ""
        elif path.startswith("infra/") and path.endswith((".tf", ".tf.json")):
            m = re.match(r'^\+\s*resource\s+"(google_[a-z_]+|aws_[a-z_]+|azurerm_[a-z_]+)"\s+"([^"]+)"', line)
            if m and not m.group(1).endswith(("_iam_member", "_iam_binding", "_project_service", "_firewall", "_dns_record_set")):
                hits.append(f"{m.group(1)}.{m.group(2)}")
    return ", ".join(hits[:4]) or None


class Guard:
    def __init__(self, secrets=None, cwd: str = "", core_run: bool = False):
        self.secrets = secrets if secrets is not None else load_secrets()
        self.cwd = cwd
        self.core_run = core_run

    # each rule returns (rule, reason) or None
    def check(self, tool: str, args: dict) -> tuple[str, str] | None:
        if tool == "Bash":
            return self._bash(str(args.get("command") or ""))
        if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            return self._file(args)
        if tool in ("WebFetch", "WebSearch") or tool.endswith(("web_search", "browse")):
            return self._outbound(json.dumps(args, ensure_ascii=False), "a request to an outside site")
        if tool in ("Task", "Agent"):
            model = str(args.get("model") or "")
            if self.core_run and model and PAID_MODEL.search(model):
                return "spend", f"a sub-agent on the paid model {model!r}: this run thinks with Skippy Core, and sub-agents stay on it."
            return self._outbound(json.dumps(args, ensure_ascii=False), "a sub-agent's prompt")
        if tool.startswith("mcp__") and MESSAGING_TOOL.search(tool):
            return "leak", f"{tool} sends a message to someone; only the Captain's own DM may be written to, and purple has no tool for that but the house's own."
        if luhn_card(json.dumps(args, ensure_ascii=False)) and tool not in ("Read", "Grep", "Glob"):
            return "spend", "a payment card number in a tool call: purple never enters payment details."
        return None

    def _outbound(self, blob: str, what: str):
        kind = has_secret(blob, self.secrets)
        if kind:
            return "leak", f"{what} would carry {kind}; nothing sensitive goes to an outside service."
        if luhn_card(blob):
            return "spend", f"{what} holds a payment card number; purple never enters payment details."
        for host in _URL.findall(blob):
            if PAID_HOSTS.search(host + "/"):
                return "spend", f"{host} is a paid API; purple uses its own tools (and builds one if it needs one)."
        return None

    def _file(self, args: dict):
        content = "".join(str(args.get(k) or "") for k in ("content", "new_string", "new_source")) + "".join(str(e.get("new_string") or "") for e in (args.get("edits") or []) if isinstance(e, dict))
        path = str(args.get("file_path") or args.get("notebook_path") or "")
        return None        # writing a secret into a local file is no leak, and neither is reading one (the Captain, 2026-11-13: "an ingested secret is not a leak"): only sending it out is, and the push check catches that

    def _ignored(self, path: str) -> bool:
        base = os.path.dirname(path) or self.cwd
        try:
            r = subprocess.run(["git", "check-ignore", "-q", path], cwd=base if os.path.isdir(base) else self.cwd or None, capture_output=True, timeout=10)
            return r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _bash(self, cmd: str):
        low = cmd.lower()
        # SPEND
        if PAID_KEYS.search(cmd):
            return "spend", "the command names a paid service's API key; purple never uses paid APIs."
        if PAID_CLI.search(cmd):
            return "spend", "a cloud/payment CLI that bills; purple uses its own tools."
        if _GCLOUD_PAID.search(cmd) or _TERRAFORM.search(cmd):
            return "spend", "it creates or changes billable cloud resources; infrastructure changes go through the push that the deployer applies, and only existing resources."
        for host in _URL.findall(cmd):
            if PAID_HOSTS.search(host + "/"):
                return "spend", f"{host} is a paid API."
        # LEAK
        sends = bool(_SEND.search(cmd)) or bool(_GH_POST.search(cmd)) or bool(_PASTE.search(cmd))
        hosts = _URL.findall(cmd)
        outside = [h for h in hosts if not _LOCAL_HOST.match(h)]
        if _MESSAGING.search(cmd):
            return "leak", "it sends a message to a service other people read; only the Captain's own DM through the house's tools is allowed."
        if _SEND.search(cmd) and _PASTE.search(cmd):
            return "leak", "a paste or file-drop site is public by design; nothing of the Captain's goes there (host your own on skippy-pc if something must be shared)."
        if sends and (outside or _PASTE.search(cmd) or _GH_POST.search(cmd)):
            kind = has_secret(cmd, self.secrets)
            if kind:
                return "leak", f"the command sends {kind} to an outside host."
            if luhn_card(cmd):
                return "spend", "a payment card number in a request."
        if re.search(r"\bgit\b[^;&|\n]*\scommit\b", low):
            diff = _staged(self.cwd)          # (a secret in a local commit is no leak: only the push, below, takes it out of the house)
            why = weakened_tests(diff)
            if why and not re.search(r"test-fix:\s*\S", cmd, re.I):
                return "lie", (f"the staged diff weakens the tests: {why}. If a test was truly wrong, say why in the commit message on a line starting `TEST-FIX:`; "
                               "otherwise fix the code, not the test.")
        if re.search(r"\bgit\b[^;&|\n]*\spush\b", low):
            diff = _unpushed(self.cwd)
            kind = has_secret(added_lines(diff), self.secrets)
            if kind:
                return "leak", f"the commits being pushed hold {kind}; a push is public. Rewrite them clean first, and note it in state/leaks.md if one already went out."
            bill = new_billable(diff)
            if bill:
                return "spend", f"the push adds billable cloud resources ({bill}); only existing infrastructure may be used."
        return None


def audit(rule: str, tool: str, why: str, summary: str, secrets) -> None:
    try:
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "rule": rule, "tool": tool, "why": why, "call": mask(summary, secrets)[:300]}) + "\n")
    except OSError:
        pass


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    tool = str(event.get("tool_name") or "")
    args = event.get("tool_input") or {}
    cwd = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or ""
    guard = Guard(cwd=cwd, core_run=os.environ.get("SKIPPY_CORE_EYES") == "1")
    hit = guard.check(tool, args if isinstance(args, dict) else {})
    if not hit:
        return 0
    rule, why = hit
    audit(rule, tool, why, f"{tool} {json.dumps(args, ensure_ascii=False)}", guard.secrets)
    follow = {"leak": " If something already got out: remove it, add a line to state/leaks.md, and say so in the report.",
              "spend": " Use your own tools; if you need one that does not exist, build it.",
              "lie": ""}.get(rule, "")
    print(f"Refused by the Captain's {rule.upper()} rule: {why}{follow} (docs/purple-rules.md). If this really is the only way to the goal, finish everything else and say so.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

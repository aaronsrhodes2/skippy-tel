"""The fence that keeps containers away from the VM's metadata server (games-env holds every game's passwords and
Discord webhooks) must still let them do DNS: on GCE the metadata address is also the VM's resolver, and a container on
Docker's default bridge (docker build, an `apk add` step) takes its resolver from the host.

Found 2026-09-30 when EverQuest's first install died building the PEQ-editor image: "fetching ...alpinelinux.org: DNS:
transient error". Game containers on compose networks use Docker's own DNS, so the games never noticed."""
import re
from pathlib import Path

STARTUP = (Path(__file__).resolve().parents[2] / "infra" / "gcp-games" / "startup.sh").read_text(encoding="utf-8")
META = "169.254.169.254"


def rules():
    """Every iptables line in startup.sh that touches the metadata address, in file order."""
    return [ln.strip() for ln in STARTUP.splitlines() if "iptables" in ln and META in ln]


def test_dns_is_let_through_to_the_resolver_over_udp_and_tcp():
    text = "\n".join(rules())
    assert re.search(r'iptables -I DOCKER-USER 1 -d 169\.254\.169\.254 -p "\$proto" --dport 53 -j ACCEPT', text)
    assert "for proto in udp tcp" in STARTUP                                   # both transports: a big answer falls back to TCP


def test_the_drop_stays_and_nothing_else_is_let_through():
    lines = rules()
    assert any("-j DROP" in ln and "--dport" not in ln for ln in lines)         # everything else to that address is still dropped
    accepts = [ln for ln in lines if "-j ACCEPT" in ln]
    assert accepts and all("--dport 53" in ln for ln in accepts)               # the only exception is port 53, never port 80
    assert not any("--dport 80" in ln for ln in lines)


def test_the_accept_is_inserted_above_the_drop():
    """iptables matches top to bottom: the DNS rule is inserted at position 1 AFTER the DROP exists, so it sits above it."""
    drop = STARTUP.index("iptables -I DOCKER-USER -d 169.254.169.254 -j DROP")
    accept = STARTUP.index('iptables -I DOCKER-USER 1 -d 169.254.169.254 -p "$proto" --dport 53')
    assert drop < accept
    assert "-I DOCKER-USER 1" in STARTUP and "-A DOCKER-USER" not in STARTUP     # an -A would land below the DROP and do nothing


def test_the_everquest_diag_block_never_reads_stdin():
    """diag.sh arrives on bash's stdin (curl | bash): any docker exec without </dev/null swallows the rest of the script,
    so the snapshot would end early and lose its end marker."""
    diag = (Path(__file__).resolve().parents[2] / "infra" / "gcp-games" / "diag.sh").read_text(encoding="utf-8")
    block = diag[diag.index("EQ=akk-stack-eqemu-server-1"):diag.index("# ── end everquest ──")]
    execs = [ln for ln in block.splitlines() if "docker exec" in ln]
    assert execs and all("</dev/null" in ln for ln in execs)
    assert "docker logs" not in block and "> /" not in block.replace("> /dev/null", "")   # read-only: no writes
    assert diag.index("# ── end everquest ──") < diag.index('echo "=== end ==="')

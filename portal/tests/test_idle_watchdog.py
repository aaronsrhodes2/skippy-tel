"""infra/gcp-games/idle_watchdog.py: why the games VM never went to sleep, and the watchdog that replaces the old one.

Pinned to what was observed (2026-09-30), not to what was assumed:

  * The old script, run unmodified on a real Debian 12 (iptables 1.8.9, nf_tables and legacy alike), stops at its
    first iptables line: `iptables -I -t mangle PREROUTING ...` is "Invalid rule number `mangle'", exit status 2,
    and `set -e` ends the script before it counts anything, logs anything or writes a state file. Every 5 minutes,
    for the life of the VM. (test_the_old_command_line_is_the_bug, test_the_fake_iptables_refuses_...)
  * portal/tests/fixtures/watchdog/iptables_L_mangle_prerouting.txt: the real listing after 40 s of a two-way
    30 packets/s stream: 1200 packets counted, 1134 of them on the ASSURED flow (the first ~2 s, before the kernel
    marks a flow ASSURED, are not).
  * nf_conntrack_docker_vm.txt: real /proc/net/nf_conntrack lines from Docker Desktop's Linux VM (kernel 6.6):
    200-odd one-shot probes (UNREPLIED), the same stream (ASSURED), one probe that got an answer (no flag), and
    the noise around them (DNS, TCP, a container's outbound traffic, Factorio's LAN broadcast).
  * valheim_steam_session_real.txt: the games VM's own log of the Captain's 38-second Steam connection (the Steam
    id is made up). valheim_crossplay_real.txt: a real Valheim 1.0.16 crossplay server's session lines (lab run on
    this PC; the public IP is replaced). (.txt: the repo's .gitignore drops *.log.)
    The crossplay server was observed binding UDP 2457 and one ephemeral port, NOT 2456.
  * Not reproducible here: internet scanners (a home NAT lets nothing unsolicited in: an idle private Valheim and
    Factorio counted 0 packets to their ports in 7+ minutes). What the scanner cases below use is the probe shape
    the kernel really recorded, at rates chosen to exceed the old 500-packets threshold.
  * Not captured from a live client: Factorio's [JOIN]/[LEAVE] lines (the wording is in the game's own locale file,
    "joined the game." / "left the game."), and Valheim's `Got connection`/`Closing socket` lines from a crossplay
    server.
"""
import ast
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infra" / "gcp-games"
FIX = Path(__file__).parent / "fixtures" / "watchdog"

spec = importlib.util.spec_from_file_location("idle_watchdog", INFRA / "idle_watchdog.py")
iw = importlib.util.module_from_spec(spec)
sys.modules["idle_watchdog"] = iw          # dataclasses need the module registered
spec.loader.exec_module(iw)


def fixture(name):
    return (FIX / name).read_text(encoding="utf-8")


# ── the bug: hypothesis B, reproduced ────────────────────────────────────────

OLD_SCRIPT_LINE = 'iptables -I -t mangle PREROUTING -p udp -m multiport --dports "$PORTS" -m comment --comment skippy-games'


def chain_of(argv):
    """The word iptables will take as the chain, the way its option parser does: whatever follows -C/-I/-A/-D."""
    for i, word in enumerate(argv[:-1]):
        if word in ("-C", "-I", "-A", "-D"):
            return argv[i + 1]
    return None


def test_the_old_command_line_is_the_bug():
    # iptables 1.8.9 said, for exactly this order: "Invalid rule number `mangle'" (exit 2). "-t" is read as the chain.
    old = OLD_SCRIPT_LINE.replace('"$PORTS"', "2456:2457").split()
    assert chain_of(old) == "-t"
    assert chain_of(old).startswith("-")            # never a valid chain name


@pytest.mark.parametrize("action", ["-C", "-I"])
@pytest.mark.parametrize("comment", [iw.RULE_RAW, iw.RULE_ASSURED])
def test_the_new_command_line_names_the_table_first(action, comment):
    argv = iw.iptables_argv(action, comment)
    assert argv[:5] == ["iptables", "-w", "5", "-t", "mangle"]
    assert chain_of(argv) == "PREROUTING"
    assert "--comment" in argv and argv[argv.index("--comment") + 1] == comment


def test_the_assured_rule_counts_only_assured_flows():
    assert "--ctstatus" in iw.iptables_argv("-I", iw.RULE_ASSURED)
    assert "--ctstatus" not in iw.iptables_argv("-I", iw.RULE_RAW)


def test_the_rules_only_count_packets_addressed_to_this_host():
    # A container's own outbound traffic to a remote port that happens to be on the list is not a player.
    for comment in (iw.RULE_RAW, iw.RULE_ASSURED):
        argv = iw.iptables_argv("-I", comment)
        assert argv[argv.index("addrtype") - 1] == "-m" and argv[argv.index("--dst-type") + 1] == "LOCAL"


def test_the_multiport_list_fits_what_iptables_accepts():
    spec_ = iw.port_spec()
    units = sum(2 if ":" in part else 1 for part in spec_.split(","))
    assert units <= 15                               # xt_multiport's limit: a range counts as two
    assert spec_ == "2456:2457,5998:5999,7000:7030,7777,7778,9000,27015,34197"


def test_every_watched_port_is_open_in_the_firewall():
    tf = (INFRA / "main.tf").read_text(encoding="utf-8")
    udp = re.search(r'allow \{\s*protocol = "udp"\s*ports = \[(.*?)\]', tf, re.S).group(1)
    open_ports = set()
    for a, _, b in re.findall(r'"(\d+)(-(\d+))?"', udp):
        open_ports |= set(range(int(a), int(b or a) + 1))
    for game, ranges in iw.GAME_PORTS.items():
        for lo, hi in ranges:
            assert set(range(lo, hi + 1)) <= open_ports, f"{game}: {lo}-{hi} is counted but not open in the firewall"


# ── a fake VM: iptables that behaves like the real one, docker, journald ─────

class FakeSystem:
    """Stands in for run(). Its iptables refuses what the real one refused."""

    def __init__(self, running=(), logs=None):
        self.rules = set()
        self.raw = self.assured = 0
        self.running, self.logs = list(running), dict(logs or {})
        self.calls, self.merge_seen = [], {}
        self.iptables_broken = False
        self.poweroff = False

    def _listing(self):
        rows = ["Chain PREROUTING (policy ACCEPT 0 packets, 0 bytes)",
                "    pkts      bytes target     prot opt in     out     source               destination"]
        for comment, n in ((iw.RULE_ASSURED, self.assured), (iw.RULE_RAW, self.raw)):
            if comment in self.rules:
                rows.append(f"{n:>8} {n * 40:>8}            17   --  *      *       0.0.0.0/0            0.0.0.0/0"
                            f"            multiport dports {iw.port_spec()} /* {comment} */")
        return "\n".join(rows) + "\n"

    def __call__(self, argv, timeout=60, merge=False):
        self.calls.append(list(argv))
        prog = argv[0]
        if prog == "iptables":
            if self.iptables_broken:
                return 3, "iptables: Permission denied (you must be root)"
            chain = chain_of(argv)
            if chain is not None and chain.startswith("-"):
                # `-I -t mangle PREROUTING`: "-t" is taken as the chain, the next word ("mangle") as a rule number
                return 2, f"iptables v1.8.9 (nf_tables): Invalid rule number `{argv[argv.index(chain) + 1]}'"
            if "-L" in argv:
                return 0, self._listing()
            comment = argv[argv.index("--comment") + 1]
            if "-C" in argv:
                return (0, "") if comment in self.rules else (1, "iptables: Bad rule (does a matching rule exist in that chain?).")
            if "-I" in argv:
                self.rules.add(comment)
                return 0, ""
        if prog == "docker" and argv[1] == "ps":
            return 0, "\n".join(self.running) + "\n"
        if prog == "docker" and argv[1] == "logs":
            self.merge_seen[argv[2]] = merge
            return (0, self.logs[argv[2]]) if argv[2] in self.logs else (1, "no such container")
        if prog == "systemctl" and argv[1:] == ["poweroff"]:
            self.poweroff = True
        return 0, ""

    def commands(self, prog):
        return [c for c in self.calls if c[0] == prog]


def test_the_fake_iptables_refuses_the_old_order_like_the_real_one():
    system = FakeSystem()
    old = ["iptables", "-I", "-t", "mangle", "PREROUTING", "-p", "udp", "--comment", "skippy-games"]
    rc, out = system(old)
    assert rc == 2 and "Invalid rule number `mangle'" in out
    assert system.rules == set()


# ── ensure_rules / counters ──────────────────────────────────────────────────

def test_ensure_rules_adds_what_is_missing_once():
    system = FakeSystem()
    added, problems = iw.ensure_rules(system)
    assert (sorted(added), problems) == (sorted([iw.RULE_RAW, iw.RULE_ASSURED]), [])
    assert system.rules == {iw.RULE_RAW, iw.RULE_ASSURED}
    inserts = len([c for c in system.calls if "-I" in c])
    assert iw.ensure_rules(system) == ([], []) and len([c for c in system.calls if "-I" in c]) == inserts


def test_ensure_rules_reports_a_failure_instead_of_dying():
    system = FakeSystem()
    system.iptables_broken = True
    added, problems = iw.ensure_rules(system)
    assert added == [] and len(problems) == 2
    assert "skippy-games-assured" in problems[1] and "exit 3" in problems[1]
    assert iw.ensure_rules(lambda *a, **k: (127, "iptables: not found"))[1]           # no iptables at all: still no crash


def test_counters_are_read_from_the_real_listing():
    assert iw.parse_counters(fixture("iptables_L_mangle_prerouting.txt")) == {"skippy-games": 1200,
                                                                            "skippy-games-assured": 1134}
    # the final rules (with the ADDRTYPE match), listed by the same iptables after 15 s of the same stream
    assert iw.parse_counters(fixture("iptables_L_mangle_prerouting_addrtype.txt")) == {"skippy-games": 450,
                                                                                     "skippy-games-assured": 384}
    assert iw.parse_counters("Chain PREROUTING (policy ACCEPT 0 packets, 0 bytes)\n") == {}
    dup = fixture("iptables_L_mangle_prerouting.txt") * 2
    assert iw.parse_counters(dup)["skippy-games"] == 1200                              # a duplicated rule: not summed


def test_a_counter_that_went_backwards_was_reset():
    assert iw.delta(500, 300) == 200
    assert iw.delta(20, 5000) == 20
    assert iw.delta(None, 5) is None and iw.delta(5, None) is None


# ── the flow table (real captures) ───────────────────────────────────────────

def test_one_shot_probes_stream_and_answered_probe_are_told_apart():
    text = fixture("nf_conntrack_docker_vm.txt")
    flows = iw.parse_conntrack(text, include_local=True)         # the capture's sources are Docker's own addresses
    assert (flows.total, flows.unreplied, flows.assured, flows.replied) == (14, 12, 1, 1)
    assert flows.by_game["valheim"] == {"assured": 0, "replied": 0, "unreplied": 12}
    assert flows.by_game["dragonwilds"] == {"assured": 1, "replied": 1, "unreplied": 0}   # the stand-in on 7778
    assert (flows.sources, flows.assured_sources) == (1, 1)


def test_flows_started_from_inside_are_not_players():
    text = fixture("nf_conntrack_docker_vm.txt")
    assert iw.parse_conntrack(text).total == 0                    # every source in the capture is a container/host
    inside = ("ipv4     2 udp      17 120 src=172.18.0.4 dst=203.0.113.7 sport=41000 dport=7777 "
              "src=203.0.113.7 dst=172.18.0.4 sport=7777 dport=41000 [ASSURED] mark=0 zone=0 use=2")
    outside = inside.replace("172.18.0.4", "198.51.100.20", 1).replace("dst=203.0.113.7 sport=41000", "dst=10.138.0.2 sport=41000", 1)
    assert iw.parse_conntrack(inside).total == 0                  # a container talking OUT to something's port 7777
    assert iw.parse_conntrack(outside).assured == 1               # a player talking IN


def test_many_scanners_are_many_addresses_and_no_players():
    lines = fixture("nf_conntrack_docker_vm.txt").splitlines()[:12]          # the 12 real unanswered probes
    spread = [ln.replace("src=172.17.0.1", f"src=198.51.100.{i + 1}", 1) for i, ln in enumerate(lines)]
    flows = iw.parse_conntrack("\n".join(spread))
    assert (flows.total, flows.unreplied, flows.assured, flows.sources) == (12, 12, 0, 12)


def test_tcp_garbage_and_ipv6_lines():
    assert iw.parse_flow("ipv4     2 tcp      6 119 TIME_WAIT src=1.2.3.4 dst=5.6.7.8 sport=1 dport=2456") is None
    assert iw.parse_flow("not a conntrack line") is None
    assert iw.parse_flow("") is None
    v6 = ("ipv6     10 udp      17 118 src=2001:db8::5 dst=2001:db8::1 sport=50000 dport=34197 "
          "src=2001:db8::1 dst=2001:db8::5 sport=34197 dport=50000 [ASSURED] mark=0 zone=0 use=2")
    assert iw.parse_flow(v6) == ("2001:db8::5", 34197, "assured")
    assert iw.is_local("fe80::1") and iw.is_local("10.138.0.2") and not iw.is_local("198.51.100.4")


def test_a_missing_flow_table_is_unreadable_not_empty(tmp_path):
    assert iw.read_flows(str(tmp_path / "nope")) is None
    (tmp_path / "ct").write_text("")
    assert iw.read_flows(str(tmp_path / "ct")).total == 0


# ── what the games say (real lines, and the honest gaps) ─────────────────────

def test_valheim_steam_connection_pairs_the_real_session():
    log = fixture("valheim_steam_session_real.txt")
    assert iw.valheim_players(log) == (0, False)                                    # in at 22:43:48, out at 22:44:27
    before_leaving = "".join(log.splitlines(keepends=True)[:3])
    assert iw.valheim_players(before_leaving) == (1, False)
    second = before_leaving + "Sep 29 22:50:01 supervisord: valheim-server 09/29/2026 22:50:01: Got connection SteamID 76561198000000002\n"
    assert iw.valheim_players(second) == (2, False)


def test_valheim_crossplay_says_its_count_in_the_session_line():
    log = fixture("valheim_crossplay_real.txt")
    assert iw.valheim_players(log) == (0, True)                                     # "... is active with 0 player(s)"
    busy = log + ('Sep 30 00:20:00 supervisord: valheim-server 09/30/2026 00:20:00: Session "LabWatchdog" with join '
                  'code 460582 and IP 203.0.113.9:2456 is active with 3 player(s)\n')
    assert iw.valheim_players(busy) == (3, True)
    empty_again = busy + ('Sep 30 00:30:00 supervisord: valheim-server 09/30/2026 00:30:00: Session "LabWatchdog" with '
                          'join code 460582 and IP 203.0.113.9:2456 is active with 0 player(s)\n')
    assert iw.valheim_players(empty_again) == (0, True)                             # the latest line is the count


def test_valheim_crossplay_that_has_not_said_yet_is_blind():
    log = ('Sep 30 00:13:19 supervisord: valheim-server 09/30/2026 00:13:19: New session server "X" that has join '
           'code , now 0 player(s)\n')
    assert iw.valheim_players(log) == (0, True)                                     # the "now N player(s)" form counts
    only_registering = 'Sep 30 00:13:20 supervisord: valheim-server 09/30/2026 00:13:20: Session "X" registered with join code 460582\n'
    assert iw.valheim_players(only_registering) == (None, True)
    assert iw.valheim_players("Valheim version: l-1.0.16\n") == (0, False)         # a normal server never mentions a join code


def test_factorio_join_and_leave():
    log = ("2026-09-30 06:50:00 [JOIN] Aaron joined the game\n"
           "2026-09-30 06:52:10 [JOIN] Some Friend joined the game\n"
           "2026-09-30 06:59:30 [LEAVE] Aaron left the game\n"
           "2026-09-30 07:00:00 [CHAT] Some Friend: hello\n")
    assert iw.factorio_players(log) == 1
    assert iw.factorio_players(log + "2026-09-30 07:01:00 [LEAVE] Some Friend left the game\n") == 0
    assert iw.factorio_players("Info ServerMultiplayerManager.cpp:808: nothing here\n") == 0


def test_game_logs_are_read_in_order_and_only_for_running_games():
    system = FakeSystem(running=["valheim", "dragonwilds"],
                        logs={"valheim": fixture("valheim_steam_session_real.txt")})
    assert iw.read_game_logs(system) == ({"valheim": 0}, [])
    assert system.merge_seen == {"valheim": True}       # stdout+stderr interleaved as written: pairing needs the order
    assert not any(c[:2] == ["docker", "logs"] and c[2] == "dragonwilds" for c in system.calls)


def test_a_crossplay_server_that_has_not_counted_is_reported_blind():
    log = 'Sep 30 00:13:20 supervisord: valheim-server 09/30/2026 00:13:20: Session "X" registered with join code 460582\n'
    assert iw.read_game_logs(FakeSystem(running=["valheim"], logs={"valheim": log})) == ({}, ["valheim"])


def test_no_docker_means_no_log_evidence_not_a_crash():
    assert iw.read_game_logs(lambda *a, **k: (127, "docker: not found")) == ({}, [])


# ── the decision ─────────────────────────────────────────────────────────────

def flows_of(assured=0, unreplied=0):
    return iw.Flows(total=assured + unreplied, assured=assured, unreplied=unreplied,
                    sources=unreplied + (1 if assured else 0), assured_sources=1 if assured else 0)


def sample(**kw):
    kw.setdefault("uptime_s", 3600.0)
    kw.setdefault("window_s", 300.0)
    return iw.Sample(**kw)


def test_scanner_noise_over_the_old_threshold_is_idle_now():
    # 5000 packets in 5 minutes (17/s) from 300 addresses that never got an answer: the old counter (500 = 1.7/s)
    # called every such interval busy. Nothing in it is a player.
    noisy = sample(raw_delta=5000, assured_delta=0, flows=flows_of(unreplied=300))
    assert noisy.raw_delta >= 500
    verdict = iw.assess(noisy)
    assert verdict.active is False and "5000 packets of probe noise ignored" in verdict.reason


def test_the_real_probe_burst_is_idle():
    # 200 one-shot probes + one query, as recorded: raw +201, assured +0, and the real unanswered flows in the table.
    probes = "\n".join(fixture("nf_conntrack_docker_vm.txt").splitlines()[:12])
    burst = sample(raw_delta=201, assured_delta=0, flows=iw.parse_conntrack(probes, include_local=True))
    assert burst.flows.unreplied == 12 and burst.flows.assured == 0
    assert iw.assess(burst).active is False


def test_a_player_stream_is_active_by_the_assured_counter():
    real = sample(raw_delta=1200, assured_delta=1134, flows=flows_of(assured=1))
    assert iw.assess(real).active is True
    assert iw.assess(sample(raw_delta=1200, assured_delta=1134)).active is True          # flow table unreadable: fine


def test_a_trickle_counts_only_while_its_flow_is_still_open():
    assert iw.assess(sample(raw_delta=40, assured_delta=40, flows=flows_of(assured=1))).active is True
    assert iw.assess(sample(raw_delta=40, assured_delta=40, flows=flows_of())).active is False
    assert iw.assess(sample(raw_delta=40, assured_delta=40)).active is False             # below "strong", no table
    assert iw.assess(sample(raw_delta=5, assured_delta=5, flows=flows_of(assured=1))).active is False


def test_a_log_count_beats_silent_counters():
    verdict = iw.assess(sample(raw_delta=0, assured_delta=0, flows=flows_of(), players={"valheim": 2, "factorio": 0}))
    assert verdict.active is True and "valheim=2" in verdict.reason


def test_crossplay_players_are_invisible_to_the_counters_but_not_to_the_log():
    # A crossplay server binds 2457 and an ephemeral port, never 2456: the counters read zero all night.
    assert iw.assess(sample(raw_delta=3, assured_delta=0, flows=flows_of(), players={"valheim": 1})).active is True


def test_unreadable_evidence_never_reads_as_nobody():
    nothing = iw.assess(sample())
    assert nothing.active is None and "no evidence could be read" in nothing.reason
    blind = iw.assess(sample(raw_delta=0, assured_delta=0, flows=flows_of(), blind=["valheim"]))
    assert blind.active is None and "valheim" in blind.reason and "crossplay" in blind.reason


def test_without_counters_or_flows_the_blunt_count_is_used_at_a_higher_bar():
    assert iw.assess(sample(raw_delta=2999)).active is False
    assert iw.assess(sample(raw_delta=3000)).active is True
    assert iw.FALLBACK_PACKETS >= 500 * 5                                              # 6x the old bar (per 5 minutes)
    assert iw.assess(sample(raw_delta=1200, window_s=120)).active is True              # 3000 per 5 min, scaled to 2 min
    assert iw.assess(sample(raw_delta=1199, window_s=120)).active is False


def test_flow_table_alone_decides_when_the_counters_are_gone():
    assert iw.assess(sample(flows=flows_of(assured=2))).active is True
    assert iw.assess(sample(flows=flows_of(unreplied=40))).active is False


# ── the clock ────────────────────────────────────────────────────────────────

CFG = iw.Config(idle_limit_min=30, max_hours=8)


def run_ticks(samples, cfg=CFG, state=None):
    state, t, out = state or iw.State(), 1_000_000.0, []
    for s in samples:
        t += 300
        state, decision = iw.step(state, s, cfg, t)
        out.append(decision)
    return state, out


def test_thirty_idle_minutes_put_the_vm_to_sleep_on_the_sixth_check():
    quiet = sample(raw_delta=2, assured_delta=0, flows=flows_of())
    _, decisions = run_ticks([quiet] * 7)
    assert [d.action for d in decisions] == ["none"] * 5 + ["sleep", "sleep"]
    assert [d.idle_min for d in decisions][:6] == [5, 10, 15, 20, 25, 30]
    assert decisions[5].why == "idle" and decisions[5].reason == "nobody played for 30 min"


def test_a_player_resets_the_clock():
    quiet = sample(raw_delta=0, assured_delta=0, flows=flows_of())
    busy = sample(raw_delta=900, assured_delta=880, flows=flows_of(assured=1))
    state, decisions = run_ticks([quiet] * 5 + [busy] + [quiet] * 5)
    assert all(d.action == "none" for d in decisions) and state.idle_min == 25


def test_not_being_able_to_tell_holds_the_clock():
    quiet = sample(raw_delta=0, assured_delta=0, flows=flows_of())
    dark = sample()                                       # nothing readable
    state, decisions = run_ticks([quiet] * 3 + [dark] * 20 + [quiet])
    assert all(d.action == "none" for d in decisions)     # 20 unreadable checks did not sleep it...
    assert state.idle_min == 20 and state.unknown_min == 0   # ...nor reset it: 15 + the last quiet check
    state, _ = run_ticks([dark] * 4)
    assert state.idle_min == 0 and state.unknown_min == 20


def test_the_backstop_ends_a_session_whatever_the_traffic_says():
    busy = dict(raw_delta=9000, assured_delta=8900, flows=flows_of(assured=3), players={"valheim": 4})
    just_before = iw.step(iw.State(announced=True), sample(uptime_s=8 * 3600 - 1, **busy), CFG, 1.0)[1]
    assert just_before.action == "none"
    _, decision = iw.step(iw.State(announced=True), sample(uptime_s=8 * 3600 + 5, **busy), CFG, 1.0)
    assert (decision.action, decision.why) == ("sleep", "backstop") and "8 h limit reached" in decision.reason


def test_the_backstop_is_announced_once_fifteen_minutes_ahead():
    busy = dict(raw_delta=900, assured_delta=880, flows=flows_of(assured=1))
    state = iw.State()
    early = iw.step(state, sample(uptime_s=8 * 3600 - 16 * 60, **busy), CFG, 1.0)
    assert early[1].action == "none"
    state, on_time = iw.step(state, sample(uptime_s=8 * 3600 - 14 * 60, **busy), CFG, 2.0)
    assert on_time.action == "announce" and state.announced
    _, again = iw.step(state, sample(uptime_s=8 * 3600 - 9 * 60, **busy), CFG, 3.0)
    assert again.action == "none"


def test_a_session_that_is_also_idle_just_sleeps_without_announcing():
    state = iw.State(idle_min=25)
    _, decision = iw.step(state, sample(uptime_s=8 * 3600 - 10 * 60, raw_delta=0, assured_delta=0, flows=flows_of()), CFG, 1.0)
    assert (decision.action, decision.why) == ("sleep", "idle")


@pytest.mark.parametrize("cfg", [iw.Config(idle_limit_min=30, max_hours=0), iw.Config(idle_limit_min=0, max_hours=8)])
def test_the_backstop_is_off_when_max_hours_is_0_or_the_session_never_sleeps(cfg):
    busy = sample(uptime_s=40 * 3600, raw_delta=9000, assured_delta=8900, flows=flows_of(assured=3))
    decision = iw.step(iw.State(), busy, cfg, 1.0)[1]
    assert decision.action == "none"
    quiet = sample(uptime_s=40 * 3600, raw_delta=0, assured_delta=0, flows=flows_of())
    if cfg.idle_limit_min == 0:                            # never sleep means never
        assert iw.step(iw.State(idle_min=999), quiet, cfg, 1.0)[1].action == "none"


def test_a_check_after_a_stall_counts_at_most_ten_minutes():
    state, decision = iw.step(iw.State(), sample(window_s=4 * 3600, raw_delta=0, assured_delta=0, flows=flows_of()), CFG, 9.0)
    assert state.idle_min == 10


@pytest.mark.parametrize("raw,expected", [("30", 30), ("", 30), ("abc", 30), ("0", 0), (" 45 ", 45), ("-5", 30)])
def test_idle_minutes_setting(raw, expected):
    assert iw.parse_idle(raw) == expected


@pytest.mark.parametrize("raw,expected", [("", 8.0), ("8", 8.0), ("0", 0.0), ("12.5", 12.5), ("junk", 8.0),
                                          ("-1", 8.0), ("nan", 8.0), ("inf", 8.0)])
def test_max_hours_setting(raw, expected):
    assert iw.parse_max_hours(raw) == expected


# ── a whole tick, against a fake VM ──────────────────────────────────────────

@pytest.fixture
def vm(tmp_path, monkeypatch):
    monkeypatch.setattr(iw, "STATE", tmp_path / "state")
    monkeypatch.setattr(iw, "metadata", lambda key, default="": {"idle-minutes": "30", "max-hours": "8"}.get(key, default))
    monkeypatch.setattr(iw, "read_flows", lambda path="/proc/net/nf_conntrack": flows_of())
    system = FakeSystem(running=["valheim"], logs={"valheim": "Valheim version: l-1.0.16\n"})
    up = {"s": 0.0, "now": 5_000_000.0}
    monkeypatch.setattr(iw, "uptime_s", lambda: up["s"])

    def tick(minutes=5, dry_run=False):
        up["s"] += minutes * 60
        up["now"] += minutes * 60
        return iw.tick(system, dry_run=dry_run, now=up["now"])
    system.tick, system.up = tick, up
    return system


def test_an_empty_server_powers_off_after_thirty_minutes_and_saves_the_worlds_first(vm):
    for _ in range(5):
        vm.tick()
    assert not vm.poweroff
    vm.tick()
    assert vm.poweroff
    stops = [c for c in vm.calls if c[:2] == ["docker", "compose"]]
    assert len(stops) == 1 and stops[0][-3:] == ["stop", "--timeout", "120"]
    assert [c for c in stops[0] if c == "--profile"] == ["--profile"] * 5
    order = [c[0] for c in vm.calls if c[0] in ("docker", iw.EVERQUEST, iw.DISCORD, "systemctl") and c[:2] != ["docker", "ps"]
             and c[:2] != ["docker", "logs"]]
    assert order[-4:] == ["docker", iw.EVERQUEST, iw.DISCORD, "systemctl"]                 # save, save, say so, power off
    assert [c for c in vm.calls if c[0] == iw.DISCORD][0][1:] == ["offline", "nobody played for 30 min"]


def test_the_journal_line_says_what_was_seen(vm):
    vm.tick()
    lines = [c for c in vm.calls if c[0] == "logger"]
    assert lines and lines[-1][1:3] == ["-t", "skippy-idle"]
    assert "idle 5/30 min" in lines[-1][-1] and "no player evidence" in lines[-1][-1]


def test_a_connected_player_keeps_the_vm_up_for_hours(vm):
    for _ in range(60):                                    # five hours of ticks
        vm.raw += 1500
        vm.assured += 1400
        vm.tick()
    assert not vm.poweroff and not vm.commands(iw.DISCORD)


def test_scanner_noise_alone_still_lets_it_sleep(vm):
    for _ in range(6):
        vm.raw += 4000                                     # 13/s of unanswered probes: 8x the old threshold
        vm.tick()
    assert vm.poweroff


def test_the_backstop_announces_then_stops_a_busy_server(vm, monkeypatch):
    monkeypatch.setattr(iw, "metadata", lambda key, default="": {"idle-minutes": "30", "max-hours": "1"}.get(key, default))
    vm.raw = vm.assured = 0
    said = []
    for _ in range(12):
        vm.raw += 2000
        vm.assured += 1900
        vm.tick()
        said.append(len(vm.commands(iw.DISCORD)))
        if vm.poweroff:
            break
    notices = [c for c in vm.commands(iw.DISCORD) if c[1] == "notice"]
    assert len(notices) == 1 and "15 minutes" in notices[0][2] and "limited to 1 hour." in notices[0][2]
    assert vm.poweroff and vm.up["s"] == 3600                # stopped at exactly the limit, players or not
    assert vm.commands(iw.DISCORD)[-1][1] == "offline" and "1 h limit reached" in vm.commands(iw.DISCORD)[-1][2]


def test_a_dry_run_changes_nothing(vm):
    for _ in range(6):
        vm.tick(dry_run=True)
    assert not vm.poweroff and not [c for c in vm.calls if c[:2] == ["docker", "compose"] or c[0] == iw.DISCORD]


def test_a_session_set_to_never_sleep_is_left_alone(vm, monkeypatch):
    monkeypatch.setattr(iw, "metadata", lambda key, default="": {"idle-minutes": "0"}.get(key, default))
    for _ in range(10):
        vm.tick()
    assert not vm.poweroff and not vm.commands("iptables")


def test_unreachable_metadata_falls_back_to_thirty_minutes_and_eight_hours(vm, monkeypatch):
    monkeypatch.setattr(iw, "metadata", lambda key, default="": default)
    for _ in range(6):
        vm.tick()
    assert vm.poweroff


def test_broken_iptables_does_not_sleep_a_vm_that_might_have_players(vm, monkeypatch):
    vm.iptables_broken = True
    monkeypatch.setattr(iw, "read_flows", lambda path="/proc/net/nf_conntrack": None)      # and no flow table either
    for _ in range(20):                                    # nothing readable for 100 minutes
        vm.tick()
    assert not vm.poweroff
    assert iw.load_state().unknown_min == 100 and iw.load_state().verdict == "unknown"
    warnings = [c[-1] for c in vm.commands("logger") if "WARNING" in c[-1]]
    assert any("iptables" in w for w in warnings) and any("only the 8 h limit will end" in w for w in warnings)
    vm.up["s"] = 8 * 3600 - 300
    vm.tick()
    assert vm.poweroff                                     # and the backstop still ends it


def test_a_crash_in_the_evidence_code_still_lets_the_backstop_end_the_session(vm, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError("a bug nobody has met yet")
    monkeypatch.setattr(iw, "gather", boom)
    for _ in range(12):                                    # an hour of crashing checks: nothing sleeps it...
        assert vm.tick() == 0                              # ...and none of them raises
    assert not vm.poweroff
    assert any("ERROR the check crashed" in c[-1] for c in vm.commands("logger"))
    assert "a bug nobody has met yet" in capsys.readouterr().err                       # the traceback reaches the unit's journal
    vm.up["s"] = 8 * 3600 - 300
    vm.tick()
    assert vm.poweroff                                     # ...but the session limit still does


def test_a_broken_iptables_with_a_readable_flow_table_still_finds_players(vm, monkeypatch):
    vm.iptables_broken = True
    monkeypatch.setattr(iw, "read_flows", lambda path="/proc/net/nf_conntrack": flows_of(assured=1))
    for _ in range(12):
        vm.tick()
    assert not vm.poweroff


def test_crossplay_players_keep_it_up_and_a_silent_crossplay_server_holds_the_clock(vm):
    vm.logs["valheim"] = fixture("valheim_crossplay_real.txt") + (
        'Sep 30 00:20:00 supervisord: valheim-server 09/30/2026 00:20:00: Session "LabWatchdog" with join code 460582 '
        'and IP 203.0.113.9:2456 is active with 1 player(s)\n')
    for _ in range(12):
        vm.tick()
    assert not vm.poweroff                                 # the counters saw nothing; the log said 1 player
    vm.logs["valheim"] = 'Sep 30 00:13:20 supervisord: valheim-server 09/30/2026 00:13:20: Session "X" registered with join code 1\n'
    for _ in range(12):
        vm.tick()
    assert not vm.poweroff and iw.load_state().verdict == "unknown"


def test_the_first_reading_counts_the_whole_counter(vm):
    vm.rules = {iw.RULE_RAW, iw.RULE_ASSURED}              # armed at boot, by startup.sh
    vm.raw, vm.assured = 4000, 3900
    vm.tick()
    assert iw.load_state().verdict == "active"             # the first window is counted from the counter's start


def test_a_rule_added_this_second_has_watched_nothing():
    sample_, totals, problems = iw.gather(iw.State(), 1.0, FakeSystem(running=[]), flows_path="/nonexistent/nf_conntrack")
    assert sample_.raw_delta is None and sample_.assured_delta is None and sample_.flows is None
    assert problems == []


def test_legacy_state_files_are_still_written(vm):
    vm.raw = 42
    vm.tick()
    assert (iw.STATE / "idle").read_text().strip() == "5"
    assert (iw.STATE / "packets").read_text().strip() == "42"


def test_a_corrupt_state_file_starts_fresh(vm):
    (iw.STATE).mkdir(parents=True, exist_ok=True)
    (iw.STATE / "watchdog.json").write_text("{not json")
    vm.tick()
    assert iw.load_state().idle_min == 5


# ── the report skippy-diag prints ────────────────────────────────────────────

def test_the_report_is_what_the_portal_parses(vm, monkeypatch):
    import diagnostics
    import idle_watch
    lines = []
    vm.logs["valheim"] = fixture("valheim_steam_session_real.txt").splitlines(keepends=True)[0]     # one player, just in
    iw.report(vm, out=lines.append, now=100.0)
    assert any(l.startswith("counters: raw=0 assured=0") and "start with the next report" in l for l in lines)
    vm.raw, vm.assured = 3000, 2900
    lines.clear()
    iw.report(vm, out=lines.append, now=220.0)
    text = "\n".join(lines)
    assert "last 120s: raw +3000, assured +2900" in text
    assert diagnostics.players_online(text) == {"valheim": 1}
    wd = idle_watch.parse(text)
    assert wd["verdict"] == "active" and wd["limit"] == "30" and wd["max_hours"] == "8"


def test_the_report_tells_what_the_last_check_decided(vm):
    vm.tick()
    vm.tick()
    lines = []
    iw.report(vm, out=lines.append, now=vm.up["now"] + 30)
    last = [ln for ln in lines if ln.startswith("last check")][0]
    assert last.startswith("last check 30s ago: idle, idle clock 10 min (no player evidence")
    vm.iptables_broken = True
    vm.tick()
    vm.tick()
    lines.clear()
    iw.report(vm, out=lines.append, now=vm.up["now"] + 5)
    assert "cannot tell for" not in "\n".join(lines)          # flows still readable: idle, not unknown


def test_the_report_says_when_there_is_no_rule_and_no_table(vm, monkeypatch):
    vm.iptables_broken = True
    monkeypatch.setattr(iw, "read_flows", lambda path="/proc/net/nf_conntrack": None)
    lines = []
    iw.report(vm, out=lines.append, now=1.0)
    text = "\n".join(lines)
    assert "PROBLEM iptables could not add" in text and "counters: raw=NO RULE assured=NO RULE" in text
    assert "unreadable" in text and 'verdict=unknown' in text


# ── the file itself ──────────────────────────────────────────────────────────

def test_it_is_python_3_11_stdlib_only():
    source = (INFRA / "idle_watchdog.py").read_text(encoding="utf-8")
    tree = ast.parse(source, feature_version=(3, 11))                   # Debian 12's python3
    imported = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert imported <= set(sys.stdlib_module_names)
    assert source.startswith("#!/usr/bin/env python3")

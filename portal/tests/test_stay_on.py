"""Stay-on: the games VM never sleeps and has no hour limit until it is switched off again (`ops stay-on [on|off]`).
2026-11-13: the VM switched itself off after 30 idle minutes in the middle of the Captain's Factorio work, because every
`ops start` wrote idle-minutes 30 and nothing reachable from skippy-core could say otherwise."""
import re
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import gcp
import ops
import remote
from catalog import clamp_idle
from test_app import FakeVm

ROOT = Path(__file__).resolve().parents[2]


class Client:
    """compute_v1.InstancesClient, as far as stay_on goes: it records every call, so a stop or a start would show."""

    def __init__(self, meta, status="RUNNING"):
        self.meta, self.status, self.calls = dict(meta), status, []

    def get(self, **_):
        items = [SimpleNamespace(key=k, value=v) for k, v in self.meta.items()]
        return SimpleNamespace(status=self.status, machine_type="zones/z/machineTypes/e2-standard-4", network_interfaces=[],
                               metadata=SimpleNamespace(items=items, fingerprint="fp"), last_start_timestamp=None)

    def set_metadata(self, metadata_resource, **_):
        self.calls.append("set_metadata")
        self.meta = {i.key: i.value for i in metadata_resource.items}
        return SimpleNamespace(result=lambda: None)

    def __getattr__(self, name):          # stop, start, set_machine_type, reset...: none may be called
        def call(**_):
            self.calls.append(name)
            return SimpleNamespace(result=lambda: None)
        return call


def real_vm(monkeypatch, meta, status="RUNNING"):
    """A real GamesVm over the recording client; google's compute_v1 (not installed everywhere) is two plain records."""
    import types
    fake = types.ModuleType("google.cloud.compute_v1")
    fake.Items = lambda key, value: SimpleNamespace(key=key, value=value)
    fake.Metadata = lambda items, fingerprint: SimpleNamespace(items=items, fingerprint=fingerprint)
    for name in ("google", "google.cloud"):
        if name not in sys.modules:
            try:
                __import__(name)
            except ImportError:
                monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "google.cloud.compute_v1", fake)
    monkeypatch.setattr(sys.modules["google.cloud"], "compute_v1", fake, raising=False)
    vm = gcp.GamesVm("p", "z", "i")
    vm._client_obj = Client(meta, status)
    return vm


def test_stay_on_writes_idle_0_and_no_hour_ceiling_and_never_stops_or_starts_a_running_vm(monkeypatch):
    vm = real_vm(monkeypatch, {"games": "factorio", "idle-minutes": "30", "max-hours": "8", "games-env": "A=1"})
    s = vm.stay_on(True)
    assert (s.status, s.idle_minutes, s.max_hours, s.games) == ("RUNNING", "0", "0", ["factorio"])
    assert vm._client.meta["stay-on"] == "1" and vm._client.meta["games-env"] == "A=1"     # everything else is kept
    assert vm._client.calls == ["set_metadata"]                                             # one metadata write, nothing else


def test_stay_on_off_goes_back_to_30_idle_minutes_and_the_8_hour_ceiling(monkeypatch):
    vm = real_vm(monkeypatch, {"games": "factorio", "idle-minutes": "0", "max-hours": "0", "stay-on": "1"})
    s = vm.stay_on(False)
    assert (s.status, s.idle_minutes, s.max_hours) == ("RUNNING", "30", "8")
    assert vm._client.meta["stay-on"] == "" and vm._client.calls == ["set_metadata"]
    tf = (ROOT / "infra/gcp-games/variables.tf").read_text(encoding="utf-8")                # "off" is Terraform's own default
    assert re.search(r'variable "max_hours".*?default\s*=\s*8\b', tf, re.S)


def test_an_admin_may_ask_for_idle_0_and_a_player_may_not():
    assert clamp_idle("0", True) == 0 and clamp_idle("0", False) == 15


def test_a_later_start_keeps_stay_on_whoever_presses_play_and_whatever_idle_they_ask_for():
    vm = FakeVm()
    assert ops.set_stay_on(vm, True)[0] and ops.stay_on(vm)
    vm.state.status = "TERMINATED"
    ok, message = ops.start_games(vm, ["valheim"], "45", is_admin=False)
    assert ok and vm.calls[-1] == ("start", ["valheim"], 0) and "stays on" in message and "sleeps after" not in message
    vm.state.status = "TERMINATED"
    ops.start_games(vm, ["valheim"], None, is_admin=True)                                   # `ops start`, the chat tool
    assert vm.calls[-1] == ("start", ["valheim"], 0)
    ops.set_stay_on(vm, False)
    vm.state.status = "TERMINATED"
    ok, message = ops.start_games(vm, ["valheim"], None, is_admin=True)
    assert vm.calls[-1] == ("start", ["valheim"], 30) and "sleeps after 30 idle minutes" in message


def test_a_start_does_not_fail_over_a_flag_it_cannot_read():
    vm = FakeVm()
    vm.get_metadata = lambda key: 1 / 0
    assert ops.start_games(vm, ["valheim"], None, is_admin=True)[0] and vm.calls[-1] == ("start", ["valheim"], 30)


def run(monkeypatch, vm, *argv):
    monkeypatch.setattr(remote, "GamesVm", lambda *a: vm)
    return remote.main(list(argv))


def test_the_ops_command_switches_it_on_and_off_without_stopping_the_vm_and_status_says_so(monkeypatch, capsys):
    vm = FakeVm("RUNNING")
    assert run(monkeypatch, vm, "stay-on") == 0 and "Stay-on is ON" in capsys.readouterr().out
    assert [c for c in vm.calls if c[0] in ("stay_on", "start", "stop", "restart")] == [("stay_on", True)]
    assert vm.state.status == "RUNNING"
    monkeypatch.setattr(remote.diagnostics, "current_join_codes", lambda *a: {})
    monkeypatch.setattr(remote.diagnostics, "current_ready", lambda *a: {})
    status = remote.status(vm)
    assert (status["idle_minutes"], status["max_hours"], status["stay_on"], status["status"]) == ("0", "0", True, "RUNNING")
    assert run(monkeypatch, vm, "stay-on", "off") == 0 and "Stay-on is OFF" in capsys.readouterr().out
    assert vm.calls[-1] == ("stay_on", False) and remote.status(vm)["stay_on"] is False
    assert run(monkeypatch, vm, "stay-on", "on") == 0 and vm.calls[-1] == ("stay_on", True)


def test_a_misspelt_argument_changes_nothing(monkeypatch, capsys):
    vm = FakeVm("RUNNING")
    assert run(monkeypatch, vm, "stay-on", "of") == 2 and "`on` (the default) or `off`" in capsys.readouterr().out
    assert not [c for c in vm.calls if c[0] == "stay_on"]


def test_terraform_leaves_the_stay_on_flag_alone():
    """A metadata key Terraform doesn't know is deleted at the next infra apply unless it is in ignore_changes."""
    tf = (ROOT / "infra/gcp-games/main.tf").read_text(encoding="utf-8")
    assert 'metadata["stay-on"]' in re.search(r"ignore_changes\s*=\s*\[(.*?)\n\s*\]", tf, re.S).group(1)

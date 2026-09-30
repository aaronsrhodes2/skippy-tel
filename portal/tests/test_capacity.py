"""When Google has no room for the VM's machine type (ZONE_RESOURCE_POOL_EXHAUSTED), start() moves down a list of
equivalent types instead of failing, and says so plainly if none has room. Uses a fake Compute client."""
import sys
import types

import pytest

import gcp
import ops
import spend
from catalog import GAMES, machine_for

EXHAUSTED = ("503 SERVICE UNAVAILABLE ZONE_RESOURCE_POOL_EXHAUSTED: The zone 'projects/skippy-tel/zones/us-west1-b' "
             "does not have enough resources available to fulfill the request.")


class Op:
    def result(self):
        return None


class FakeCompute:
    """Just enough of google.cloud.compute_v1 for GamesVm.start()."""
    class InstancesSetMachineTypeRequest:
        def __init__(self, machine_type):
            self.machine_type = machine_type

    class Items:
        def __init__(self, key, value):
            self.key, self.value = key, value

    class Metadata:
        def __init__(self, items, fingerprint):
            self.items, self.fingerprint = items, fingerprint


class FakeInstances:
    def __init__(self, machine_type="e2-standard-2", status="TERMINATED", full=()):
        self.machine_type, self.status, self.full = machine_type, status, set(full)
        self.meta = {"games": "valheim", "idle-minutes": "30"}
        self.log = []                                    # ("set_machine_type", type) / ("start", type) in order
        self.fail_with = None                            # an exception to raise on start, whatever the type

    def get(self, project, zone, instance):
        return types.SimpleNamespace(
            status=self.status, machine_type=f"zones/z/machineTypes/{self.machine_type}", network_interfaces=[],
            metadata=types.SimpleNamespace(items=[types.SimpleNamespace(key=k, value=v) for k, v in self.meta.items()],
                                           fingerprint="f"),
            last_start_timestamp=None)

    def set_metadata(self, project, zone, instance, metadata_resource):
        self.meta = {i.key: i.value for i in metadata_resource.items}
        return Op()

    def set_machine_type(self, project, zone, instance, instances_set_machine_type_request_resource):
        self.machine_type = instances_set_machine_type_request_resource.machine_type.rsplit("/", 1)[-1]
        self.log.append(("set_machine_type", self.machine_type))
        return Op()

    def start(self, project, zone, instance):
        self.log.append(("start", self.machine_type))
        if self.fail_with:
            raise self.fail_with
        if self.machine_type in self.full:
            raise RuntimeError(EXHAUSTED)
        self.status = "RUNNING"
        return Op()


@pytest.fixture
def vm(monkeypatch):
    cloud = types.ModuleType("google.cloud")
    cloud.compute_v1 = FakeCompute
    google = types.ModuleType("google")
    google.cloud = cloud
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud)
    box = gcp.GamesVm("p", "us-west1-b", "i")
    box._client_obj = FakeInstances()
    return box


def test_the_cheapest_type_is_tried_first_and_nothing_else_when_it_works(vm):
    assert vm.start(["factorio"]).status == "RUNNING"
    assert vm._client_obj.log == [("start", "e2-standard-2")]                   # already that size: no resize either


def test_no_room_moves_to_the_next_type_and_it_still_boots(vm):
    vm._client_obj.full = {"e2-standard-2", "n2d-standard-2"}
    status = vm.start(["factorio"])
    assert status.status == "RUNNING" and status.machine_type == "n2-standard-2"
    assert vm._client_obj.log == [("start", "e2-standard-2"), ("set_machine_type", "n2d-standard-2"), ("start", "n2d-standard-2"),
                                  ("set_machine_type", "n2-standard-2"), ("start", "n2-standard-2")]
    assert vm._client_obj.meta["games"] == "factorio"                            # the choice is saved either way


def test_the_next_start_goes_back_to_the_cheapest(vm):
    vm._client_obj.full = {"e2-standard-2"}
    vm.start(["valheim"])
    assert vm._client_obj.machine_type == "n2d-standard-2"
    vm._client_obj.status, vm._client_obj.full, vm._client_obj.log = "TERMINATED", set(), []
    vm.start(["valheim"])
    assert vm._client_obj.log == [("set_machine_type", "e2-standard-2"), ("start", "e2-standard-2")]


def test_when_nothing_has_room_it_says_so_and_names_what_it_tried(vm):
    vm._client_obj.full = set(gcp.machine_candidates(["factorio"]))
    with pytest.raises(gcp.CapacityError) as caught:
        vm.start(["factorio"])
    assert caught.value.tried == ["e2-standard-2", "n2d-standard-2", "n2-standard-2", "e2-highmem-2", "n1-standard-2"]
    assert vm._client_obj.status == "TERMINATED" and vm._client_obj.meta["games"] == "factorio"
    ok, message = ops.start_games(vm, ["factorio"], None, is_admin=True)
    assert not ok and "no spare capacity" in message and "us-west1-b" in message and "press Play again" in message
    assert "503" not in message and "ZONE_RESOURCE" not in message                # the person gets words, not the API error


def test_a_real_error_is_not_mistaken_for_no_room(vm):
    vm._client_obj.fail_with = PermissionError("403 Required 'compute.instances.start' permission")
    with pytest.raises(PermissionError):
        vm.start(["factorio"])
    assert vm._client_obj.log == [("start", "e2-standard-2")]                    # it did not go on trying other sizes


def test_a_running_server_only_saves_the_choice(vm):
    vm._client_obj.status = "RUNNING"
    vm.start(["dragonwilds"])
    assert vm._client_obj.log == [] and vm._client_obj.meta["games"] == "dragonwilds"


def test_restart_reports_no_room_too(vm):
    vm._client_obj.status, vm._client_obj.full = "RUNNING", set(gcp.machine_candidates(["valheim"]))
    vm.stop = lambda: setattr(vm._client_obj, "status", "TERMINATED")
    ok, message = ops.restart_server(vm)
    assert not ok and "no spare capacity" in message


def test_what_counts_as_no_room():
    assert gcp.is_capacity_error(RuntimeError(EXHAUSTED))
    assert gcp.is_capacity_error(RuntimeError("The zone 'z' does not have enough resources available"))
    assert not gcp.is_capacity_error(RuntimeError("403 quota exceeded"))
    assert not gcp.is_capacity_error(RuntimeError("Instance is already running"))


RAM_GB = {"e2-standard-2": 8, "n2d-standard-2": 8, "n2-standard-2": 8, "e2-highmem-2": 16, "n1-standard-2": 7.5,
          "e2-standard-4": 16, "n2d-standard-4": 16, "n2-standard-4": 16, "n1-standard-4": 15, "e2-highmem-4": 32,
          "n2d-highmem-4": 32, "n2-highmem-4": 32, "e2-highmem-8": 64, "n2d-highmem-8": 64, "n2-highmem-8": 64}
NEEDED = {"e2-standard-2": 6, "e2-standard-4": 14, "e2-highmem-4": 30, "e2-highmem-8": 30}   # machine_for()'s RAM tiers, OS included


def test_every_alternate_fits_what_the_primary_was_sized_for_and_has_a_price():
    for primary, alternates in gcp.ALTERNATES.items():
        for alt in alternates:
            assert RAM_GB[alt] >= NEEDED[primary], f"{alt} is too small to stand in for {primary}"
            assert not alt.startswith("t2"), "Tau T2D can't live-migrate; the VM's on_host_maintenance is MIGRATE"
        for machine in (primary, *alternates):
            assert machine in spend.HOURLY, f"{machine} has no price for the spend estimate"
    every_size = {machine_for([g]) for g in GAMES} | {machine_for(list(GAMES))}
    assert every_size <= set(gcp.ALTERNATES)                                     # every size we can pick has fallbacks

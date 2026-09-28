"""Thin wrapper around google-cloud-compute for the one VM this portal
controls. Mirrors the logic in scripts/games.sh, but as a library instead
of a script that shells out to `gcloud`.

Caveat, stated plainly: this module could not be exercised against the
real Compute Engine API while writing it (no network access to GCP from
that sandbox). The exact attribute names below (`nat_i_p` in particular —
a known quirk of the generated client, not a typo) are correct as of the
google-cloud-compute versions current in 2026, but if `gcp.status()`
raises an AttributeError after deploy, that's the first place to look —
paste the traceback and it's a quick fix.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from google.cloud import compute_v1

from catalog import machine_for


@dataclass
class VmStatus:
    status: str                  # RUNNING | TERMINATED | STOPPING | PROVISIONING | ...
    machine_type: str             # e.g. "e2-standard-4"
    external_ip: str | None
    games: list[str] = field(default_factory=list)
    idle_minutes: str = "30"


def _metadata_dict(instance) -> dict[str, str]:
    return {item.key: item.value for item in (instance.metadata.items or [])}


def _external_ip(instance) -> str | None:
    for iface in instance.network_interfaces or []:
        for cfg in iface.access_configs or []:
            ip = getattr(cfg, "nat_i_p", None) or getattr(cfg, "nat_ip", None)
            if ip:
                return ip
    return None


class GamesVm:
    def __init__(self, project: str, zone: str, instance: str):
        self.project, self.zone, self.instance = project, zone, instance
        self._client = compute_v1.InstancesClient()

    def status(self) -> VmStatus:
        inst = self._client.get(project=self.project, zone=self.zone, instance=self.instance)
        meta = _metadata_dict(inst)
        return VmStatus(
            status=inst.status,
            machine_type=inst.machine_type.rsplit("/", 1)[-1],
            external_ip=_external_ip(inst),
            games=meta.get("games", "").split(),
            idle_minutes=meta.get("idle-minutes", "30"),
        )

    def _set_metadata(self, updates: dict[str, str]) -> None:
        inst = self._client.get(project=self.project, zone=self.zone, instance=self.instance)
        merged = _metadata_dict(inst)
        merged.update(updates)
        items = [compute_v1.Items(key=k, value=v) for k, v in merged.items()]
        op = self._client.set_metadata(
            project=self.project, zone=self.zone, instance=self.instance,
            metadata_resource=compute_v1.Metadata(items=items, fingerprint=inst.metadata.fingerprint),
        )
        op.result()

    def start(self, games: list[str], idle_minutes: int = 30) -> VmStatus:
        """Size the VM for `games`, set which games start, and boot it.

        If the VM is already running, only the metadata is updated — a
        live game-list change needs a stop/start to take effect (no SSH
        access from here to re-trigger the startup script in place, unlike
        scripts/games.sh's `up` on an already-running VM).
        """
        current = self.status()
        wanted_type = machine_for(games)
        if current.status == "TERMINATED" and current.machine_type != wanted_type:
            op = self._client.set_machine_type(
                project=self.project, zone=self.zone, instance=self.instance,
                instances_set_machine_type_request_resource=compute_v1.InstancesSetMachineTypeRequest(
                    machine_type=f"zones/{self.zone}/machineTypes/{wanted_type}",
                ),
            )
            op.result()

        self._set_metadata({"games": " ".join(games), "idle-minutes": str(idle_minutes)})

        if current.status != "RUNNING":
            op = self._client.start(project=self.project, zone=self.zone, instance=self.instance)
            op.result()
        return self.status()

    def stop(self) -> VmStatus:
        """Requests a graceful stop. The VM's shutdown-script (see
        infra/gcp-games/shutdown.sh) saves worlds and posts to Discord
        before the OS actually powers off — same as the idle watchdog's
        own stop path, just triggered by a person instead of a timer."""
        op = self._client.stop(project=self.project, zone=self.zone, instance=self.instance)
        op.result()
        return self.status()

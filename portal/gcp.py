"""Thin wrapper around google-cloud-compute for the one VM this portal
controls. Same logic as scripts/games.sh, as a library instead of a script.

The VM's settings (passwords, world names, Discord webhooks) live in the
instance metadata key `games-env`, in .env format; the VM's startup script
writes it to /srv/games/.env on every boot. Editing settings here + a
restart is how any setting change reaches a running server.

Runs as the Cloud Run service account (skippy-agent), which holds
roles/compute.admin — no key file anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import envfile
from catalog import machine_for


@dataclass
class VmStatus:
    status: str                  # RUNNING | TERMINATED | STOPPING | PROVISIONING | ...
    machine_type: str             # e.g. "e2-standard-4"
    external_ip: str | None
    games: list[str] = field(default_factory=list)
    idle_minutes: str = "30"
    env: dict[str, str] = field(default_factory=dict)   # parsed games-env
    started_at: datetime | None = None                  # last boot, for the "starting" hint

    def minutes_up(self, now: datetime | None = None) -> int | None:
        if self.status != "RUNNING" or not self.started_at:
            return None
        now = now or datetime.now(timezone.utc)
        return max(0, int((now - self.started_at).total_seconds() // 60))


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _metadata_dict(instance) -> dict[str, str]:
    return {item.key: item.value for item in (instance.metadata.items or [])}


def _external_ip(instance) -> str | None:
    for iface in instance.network_interfaces or []:
        for cfg in iface.access_configs or []:
            # `nat_i_p` is how the generated client spells natIP; `nat_ip` is a fallback.
            ip = getattr(cfg, "nat_i_p", None) or getattr(cfg, "nat_ip", None)
            if ip:
                return ip
    return None


class GamesVm:
    def __init__(self, project: str, zone: str, instance: str):
        self.project, self.zone, self.instance = project, zone, instance
        self._client_obj = None

    @property
    def _client(self):
        # Created on first use, so importing/constructing never needs credentials.
        if self._client_obj is None:
            from google.cloud import compute_v1
            self._client_obj = compute_v1.InstancesClient()
        return self._client_obj

    def _get(self):
        return self._client.get(project=self.project, zone=self.zone, instance=self.instance)

    def status(self) -> VmStatus:
        inst = self._get()
        meta = _metadata_dict(inst)
        return VmStatus(
            status=inst.status,
            machine_type=inst.machine_type.rsplit("/", 1)[-1],
            external_ip=_external_ip(inst),
            games=meta.get("games", "").split(),
            idle_minutes=meta.get("idle-minutes", "30"),
            env=envfile.parse(meta.get("games-env", "")),
            started_at=_parse_ts(getattr(inst, "last_start_timestamp", None)),
        )

    def _set_metadata(self, updates: dict[str, str]) -> None:
        from google.cloud import compute_v1
        inst = self._get()
        merged = _metadata_dict(inst)
        merged.update(updates)
        items = [compute_v1.Items(key=k, value=v) for k, v in merged.items()]
        self._client.set_metadata(
            project=self.project, zone=self.zone, instance=self.instance,
            metadata_resource=compute_v1.Metadata(items=items, fingerprint=inst.metadata.fingerprint),
        ).result()

    def update_env(self, updates: dict[str, str]) -> None:
        """Merge keys into the VM's settings. Takes effect on the next boot."""
        current = _metadata_dict(self._get()).get("games-env", "")
        self._set_metadata({"games-env": envfile.merge(current, updates)})

    def start(self, games: list[str], idle_minutes: int = 30) -> VmStatus:
        """Size the VM for `games`, choose which games start, and boot it.
        On an already-running VM only the choice is saved (applies on restart)."""
        from google.cloud import compute_v1
        current = self.status()
        wanted_type = machine_for(games)
        if current.status == "TERMINATED" and current.machine_type != wanted_type:
            self._client.set_machine_type(
                project=self.project, zone=self.zone, instance=self.instance,
                instances_set_machine_type_request_resource=compute_v1.InstancesSetMachineTypeRequest(
                    machine_type=f"zones/{self.zone}/machineTypes/{wanted_type}",
                ),
            ).result()
        self._set_metadata({"games": " ".join(games), "idle-minutes": str(idle_minutes)})
        if current.status != "RUNNING":
            self._client.start(project=self.project, zone=self.zone, instance=self.instance).result()
        return self.status()

    def stop(self) -> VmStatus:
        """Graceful stop: the VM's shutdown-script (infra/gcp-games/shutdown.sh)
        saves worlds and posts to Discord before the OS powers off."""
        self._client.stop(project=self.project, zone=self.zone, instance=self.instance).result()
        return self.status()

    def restart(self, games: list[str] | None = None, idle_minutes: int | None = None) -> VmStatus:
        """Stop (if running) then start. Every boot re-pulls the server images
        and re-reads settings, so this is also "apply settings" and "update"."""
        current = self.status()
        games = games if games is not None else current.games
        idle = idle_minutes if idle_minutes is not None else int(current.idle_minutes or 30)
        if current.status == "RUNNING":
            self.stop()
        return self.start(games, idle_minutes=idle)

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
    max_hours: str = "8"                                # metadata max-hours: the watchdog's hard ceiling (0 = none)

    def minutes_up(self, now: datetime | None = None) -> int | None:
        if self.status != "RUNNING" or not self.started_at:
            return None
        now = now or datetime.now(timezone.utc)
        return max(0, int((now - self.started_at).total_seconds() // 60))


# When Google has no room for a machine type in the zone (ZONE_RESOURCE_POOL_EXHAUSTED: it happens to the E2 family
# for minutes to hours, most often right after a VM was stopped), start() tries these in order. All of them fit
# the same games (RAM at least what machine_for() sized for) and support live migration, which the VM's
# on_host_maintenance = MIGRATE needs (Tau T2D doesn't, so it isn't here). The cheapest is always tried first.
ALTERNATES = {
    "e2-standard-2": ("n2d-standard-2", "n2-standard-2", "e2-highmem-2", "n1-standard-2"),
    "e2-standard-4": ("n2d-standard-4", "n2-standard-4", "n1-standard-4", "e2-highmem-4"),
    "e2-highmem-4": ("n2d-highmem-4", "n2-highmem-4"),
    "e2-highmem-8": ("n2d-highmem-8", "n2-highmem-8"),
}


def machine_candidates(games: list[str]) -> list[str]:
    """The machine types to try for `games`, cheapest first."""
    wanted = machine_for(games)
    return [wanted, *ALTERNATES.get(wanted, ())]


def is_capacity_error(exc: BaseException) -> bool:
    """Google has no room right now (as opposed to a permissions or quota problem, which retrying can't fix)."""
    text = str(exc)
    return "RESOURCE_POOL_EXHAUSTED" in text or "does not have enough resources" in text


class CapacityError(RuntimeError):
    """No machine type we're willing to use has room in the zone at the moment."""

    def __init__(self, zone: str, tried: list[str]):
        self.zone, self.tried = zone, tried
        super().__init__(f"Google has no spare capacity in {zone} right now (tried {', '.join(tried)})")


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
            max_hours=meta.get("max-hours", "8"),
        )

    def serial_output(self, port: int) -> str:
        """The VM's serial port buffer: port 1 is the console (boot and
        startup-script output), port 2 carries skippy-diag snapshots."""
        from google.cloud import compute_v1
        return self._client.get_serial_port_output(
            request=compute_v1.GetSerialPortOutputInstanceRequest(
                project=self.project, zone=self.zone, instance=self.instance, port=port),
        ).contents or ""

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

    def get_metadata(self, key: str) -> str:
        return _metadata_dict(self._get()).get(key, "")

    def set_metadata(self, key: str, value: str) -> None:
        self._set_metadata({key: value})

    def update_env(self, updates: dict[str, str]) -> None:
        """Merge keys into the VM's settings. Takes effect on the next boot."""
        current = _metadata_dict(self._get()).get("games-env", "")
        self._set_metadata({"games-env": envfile.merge(current, updates)})

    def start(self, games: list[str], idle_minutes: int = 30) -> VmStatus:
        """Size the VM for `games`, choose which games start, and boot it.
        On an already-running VM only the choice is saved (applies on restart)."""
        from google.cloud import compute_v1
        current = self.status()
        self._set_metadata({"games": " ".join(games), "idle-minutes": str(idle_minutes)})
        if current.status == "RUNNING":
            return self.status()
        have, tried = current.machine_type, []
        for wanted_type in machine_candidates(games):
            try:
                if current.status == "TERMINATED" and have != wanted_type:
                    self._client.set_machine_type(
                        project=self.project, zone=self.zone, instance=self.instance,
                        instances_set_machine_type_request_resource=compute_v1.InstancesSetMachineTypeRequest(
                            machine_type=f"zones/{self.zone}/machineTypes/{wanted_type}",
                        ),
                    ).result()
                    have = wanted_type
                self._client.start(project=self.project, zone=self.zone, instance=self.instance).result()
                return self.status()
            except Exception as exc:   # noqa: BLE001 — only "no room" moves on to the next type; anything else is real
                if not is_capacity_error(exc):
                    raise
                tried.append(wanted_type)
        raise CapacityError(self.zone, tried)

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

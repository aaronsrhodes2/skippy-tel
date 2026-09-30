"""Monthly spend estimate for the personal dashboard, until real billing data
(the BigQuery billing export + an Anthropic Admin key) is wired in.

The model is the one the Captain signed off on (2026-09-29): one server at a
time, weekdays 0-4 h (avg 2), weekends 0-12 h (avg 6), plus the pop-up
overhead (boot + the idle tail before auto-sleep) on each day played.
Prices are Google's us-west1 on-demand list prices; update them here.
"""
from __future__ import annotations

HOURLY = {"e2-standard-2": 0.067, "e2-standard-4": 0.134, "e2-highmem-4": 0.181, "e2-highmem-8": 0.362,
          # the capacity fallbacks (gcp.ALTERNATES), used only when Google has no room for the E2 size
          "n2d-standard-2": 0.085, "n2-standard-2": 0.097, "e2-highmem-2": 0.090, "n1-standard-2": 0.095,
          "n2d-standard-4": 0.169, "n2-standard-4": 0.194, "n1-standard-4": 0.190,
          "n2d-highmem-4": 0.228, "n2-highmem-4": 0.262, "n2d-highmem-8": 0.456, "n2-highmem-8": 0.524}
WEEKS_PER_MONTH = 4.35
HOURS_IN_MONTH = 730

PLAY_HOURS = {"average": (5 * 2 + 2 * 6) * WEEKS_PER_MONTH, "max": (5 * 4 + 2 * 12) * WEEKS_PER_MONTH}
PLAY_DAYS = {"average": 5 * WEEKS_PER_MONTH, "max": 7 * WEEKS_PER_MONTH}
OVERHEAD_H_PER_DAY = 0.1 + 0.5                      # ~6 min boot + 30 min idle tail

IP_IDLE_PER_H, IP_USED_PER_H = 0.010, 0.005         # static external IP: unused costs more
DISK_PER_GB_MONTH = 0.10                            # pd-balanced
DISKS_GB = 60 + 30                                  # world disk + boot disk
SNAPSHOTS, MISC = 0.50, 0.60                        # snapshots; registry + secrets + state bucket
EGRESS = {"average": 1.50, "max": 5.00}
CHAT = {"average": 0.50, "max": 3.00}               # Haiku ~$0.01/message, 50-300 messages


def estimate(machine_type: str = "e2-standard-4") -> dict:
    """Line items and totals for an average and a heavy month."""
    rate = HOURLY.get(machine_type, HOURLY["e2-standard-4"])
    out = {"machine_type": machine_type, "hourly": rate, "months": {}}
    for case in ("average", "max"):
        billed = PLAY_HOURS[case] + PLAY_DAYS[case] * OVERHEAD_H_PER_DAY
        items = [
            ("VM compute (only while awake)", billed * rate),
            ("Static IP", (HOURS_IN_MONTH - billed) * IP_IDLE_PER_H + billed * IP_USED_PER_H),
            (f"Disks ({DISKS_GB} GB)", DISKS_GB * DISK_PER_GB_MONTH),
            ("Daily snapshots", SNAPSHOTS),
            ("Network out (game traffic)", EGRESS[case]),
            ("Registry, secrets, Terraform state", MISC),
            ("Cloud Run (portal + Mentat)", 0.0),
            ("Portal chat (Claude Haiku)", CHAT[case]),
        ]
        out["months"][case] = {"hours": round(billed), "items": [(k, round(v, 2)) for k, v in items],
                               "total": round(sum(v for _, v in items), 2)}
    always_on = HOURS_IN_MONTH * rate + HOURS_IN_MONTH * IP_USED_PER_H + DISKS_GB * DISK_PER_GB_MONTH
    out["always_on_total"] = round(always_on + SNAPSHOTS + MISC + EGRESS["max"], 2)
    return out


def session_cost(machine_type: str, minutes_up: int | None) -> float | None:
    if minutes_up is None:
        return None
    return round(HOURLY.get(machine_type, HOURLY["e2-standard-4"]) * minutes_up / 60, 2)

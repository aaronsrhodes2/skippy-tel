"""The games VM's hourly prices (Google's us-west1 on-demand list prices; update them here) and what a session costs so far.

(The monthly estimate and the Spend pane were removed 2026-10-27, the Captain: the only way to spend money on AI is orange Skippy and Claude Desktop.)
"""
from __future__ import annotations

HOURLY = {"e2-standard-2": 0.067, "e2-standard-4": 0.134, "e2-highmem-4": 0.181, "e2-highmem-8": 0.362,
          # the capacity fallbacks (gcp.ALTERNATES), used only when Google has no room for the E2 size
          "n2d-standard-2": 0.085, "n2-standard-2": 0.097, "e2-highmem-2": 0.090, "n1-standard-2": 0.095,
          "n2d-standard-4": 0.169, "n2-standard-4": 0.194, "n1-standard-4": 0.190,
          "n2d-highmem-4": 0.228, "n2-highmem-4": 0.262, "n2d-highmem-8": 0.456, "n2-highmem-8": 0.524}

def session_cost(machine_type: str, minutes_up: int | None) -> float | None:
    if minutes_up is None:
        return None
    return round(HOURLY.get(machine_type, HOURLY["e2-standard-4"]) * minutes_up / 60, 2)

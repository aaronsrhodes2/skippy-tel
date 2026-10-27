"""Rows that are retired or not set up yet say so (`planned`), so the portal shows them grey, never as an outage."""
from app.routes import services


def test_retired_and_unbuilt_services_are_marked_planned():
    rows = {s["id"]: s for s in services._build_manifest()}
    for sid in ("image", "ups", "starmap"):
        assert rows[sid]["planned"] is True
    assert "planned" not in rows["intent"] and "planned" not in rows["bilby"]
    assert "pending" not in rows["image"]["status_detail"]

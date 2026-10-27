"""docs/popup-inventory.md's pop-up table is generated from the registries (tools/popup_inventory.py): it fails when a pop-up is added, changed or
removed without the table being refreshed, so the inventory the private Skippy (and the Captain) read is never stale."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import popup_inventory as pi  # noqa: E402


def test_the_inventory_table_matches_the_registries():
    assert pi.main(["--check"]) == 0, "run: python tools/popup_inventory.py  and paste the table between the markers in docs/popup-inventory.md"


def test_every_registered_pop_up_has_a_row_and_the_table_is_the_registries_own():
    names = [e["name"] for e in pi.entries()]
    table = pi.table()
    assert len(names) >= 13 and all(f"| `{n}` |" in table for n in names)


def test_a_new_entry_changes_the_table(tmp_path):
    (tmp_path / "services" / "popups").mkdir(parents=True)
    (tmp_path / "services" / "popups" / "registry.json").write_text('{"popups": [{"name": "t", "box": "pc", "tier": "quick", "description": "A test. More words.", "est_seconds": 200, "vram_gb": 2}]}')
    row = pi.table(tmp_path).splitlines()[-1]
    assert row.startswith("| `t` | A test. |") and "3 min" in row and "2 GB" in row

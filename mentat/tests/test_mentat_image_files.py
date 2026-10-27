"""The Mentat image copies its modules by name (mentat/Dockerfile): every local module app.py imports must be on that list.

2026-10-27: app.py gained `import cascade_stage`, the Dockerfile did not, the deploy's smoke test did not notice, and the live Mentat crash-looped
("No module named 'cascade_stage'") until the next push. This test fails before the push instead.
"""
import re
from pathlib import Path

MENTAT = Path(__file__).resolve().parents[1]


def _local_imports(py: Path) -> set[str]:
    names = set()
    for m in re.finditer(r"^(?:import|from)\s+([A-Za-z_]\w*)", py.read_text(encoding="utf-8"), re.M):
        if (MENTAT / f"{m.group(1)}.py").exists():
            names.add(m.group(1))
    return names


def test_every_local_module_app_imports_is_copied_into_the_image():
    docker = (MENTAT / "Dockerfile").read_text(encoding="utf-8")
    copied = set(re.findall(r"mentat/([A-Za-z_]\w*)\.py", docker))
    needed = _local_imports(MENTAT / "app.py") | {"app"}
    for name in sorted(needed):
        needed |= _local_imports(MENTAT / f"{name}.py")            # what those modules import in turn
    missing = sorted(needed - copied)
    assert not missing, f"mentat/Dockerfile does not COPY: {missing}"

"""The private Skippy's projects: which repos the Captain can develop from his phone, and where the agent works on each.

The registry is projects.json beside this file (committed, so adding a project is a reviewed change, never something a
chat message or a request field can do). Every entry is checked here, and a bad one is refused at load, not at the first
run: a typo'd path must not become "the agent worked in the Captain's own checkout".

Why each project has its OWN clone (`clone`), never the Captain's checkout (`primary`): he and other editors work in the
checkout, and an agent with every permission check skipped must not stash, reset or commit over their work in progress.
The engine passes `primary` with --add-dir so the agent can read it (and, for SkippyTel, deploy a skippy-pc service
from it, which its prompt spells out). Stdlib only.
"""
from __future__ import annotations

import json
import ntpath
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

FILE = Path(__file__).with_name("projects.json")
DEFAULT = "skippytel"                      # the project of a run or conversation that names none (everything before projects)
MAX_PROJECTS = 40
_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_REPO = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}")
_BRANCH = re.compile(r"[A-Za-z0-9._/-]{1,100}")


class ProjectError(ValueError):
    """The registry (or a project id someone sent) isn't acceptable."""


@dataclass(frozen=True)
class Project:
    id: str                 # what the portal sends and the runs carry: [a-z0-9-]
    name: str               # what the chat shows
    repo: str               # GitHub owner/name
    branch: str             # the branch the agent pushes to (sigma-ground's is master)
    primary: str            # the Captain's checkout: read-only to the agent except for SkippyTel's service deploys
    clone: str              # the agent's own working copy
    deploy: str = ""        # "skippytel" = the SkippyTel deploy rules in the prompt; "" = the repo's own CLAUDE.md
    remote: str = ""        # where the clone comes from; "" = https://github.com/<repo>.git

    @property
    def url(self) -> str:
        return self.remote or f"https://github.com/{self.repo}.git"

    def public(self) -> dict:
        """What the bridge tells the portal (via /health): no paths, nothing about this PC."""
        return {"id": self.id, "name": self.name, "repo": self.repo, "branch": self.branch}


def _norm(path: str) -> str:
    """A path compared the Windows way (case and slash direction don't matter) on any OS: the registry holds D:/ paths
    and CI, which checks it, runs on Linux."""
    return posixpath.normpath(path.replace("\\", "/")).lower().rstrip("/")


def _absolute(path: str) -> bool:
    return ntpath.isabs(path) or posixpath.isabs(path)


def _inside(child: str, parent: str) -> bool:
    child, parent = _norm(child), _norm(parent)
    return child == parent or child.startswith(parent + "/")


def check(project: Project) -> Project:
    """One project, or ProjectError saying what's wrong with it."""
    if not _ID.fullmatch(project.id):
        raise ProjectError(f"bad project id {project.id!r}")
    if not project.name.strip() or len(project.name) > 60 or "\n" in project.name:
        raise ProjectError(f"{project.id}: bad name")
    if not _REPO.fullmatch(project.repo):
        raise ProjectError(f"{project.id}: repo must be owner/name")
    if not _BRANCH.fullmatch(project.branch) or ".." in project.branch:
        raise ProjectError(f"{project.id}: bad branch")
    for label, path in (("primary", project.primary), ("clone", project.clone)):
        if not path or "\n" in path or "\r" in path or not _absolute(path):
            raise ProjectError(f"{project.id}: {label} must be an absolute path on one line")
    # The point of the whole registry: the clone is never the Captain's checkout, nor inside it, nor around it.
    if _inside(project.clone, project.primary) or _inside(project.primary, project.clone):
        raise ProjectError(f"{project.id}: the clone must be separate from the Captain's checkout")
    if project.deploy not in ("", "skippytel"):
        raise ProjectError(f"{project.id}: unknown deploy rules {project.deploy!r}")
    return project


def parse(data) -> dict[str, Project]:
    """The registry from projects.json's content, checked. Raises ProjectError."""
    items = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items or len(items) > MAX_PROJECTS:
        raise ProjectError("projects.json needs a non-empty `projects` list")
    out: dict[str, Project] = {}
    clones: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ProjectError("every project is an object")
        try:
            project = check(Project(**{k: str(item.get(k) or "") for k in
                                       ("id", "name", "repo", "branch", "primary", "clone", "deploy", "remote")}))
        except TypeError as exc:                 # (can't happen with the comprehension above, but never a traceback)
            raise ProjectError(str(exc)) from None
        if project.id in out:
            raise ProjectError(f"{project.id} is listed twice")
        if _norm(project.clone) in clones:
            raise ProjectError(f"{project.id}: two projects share one clone")
        clones.add(_norm(project.clone))
        out[project.id] = project
    for p in out.values():                       # nobody's clone may be anybody's checkout either
        for q in out.values():
            if p is not q and (_inside(p.clone, q.primary) or _inside(q.primary, p.clone)):
                raise ProjectError(f"{p.id}'s clone overlaps {q.id}'s checkout")
    if DEFAULT not in out:
        raise ProjectError(f"the registry must have {DEFAULT!r}: it is every older conversation's project")
    return out


def load(path: Path = FILE) -> dict[str, Project]:
    return parse(json.loads(Path(path).read_text(encoding="utf-8")))

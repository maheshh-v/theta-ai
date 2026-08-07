"""
Briefs — the thing Theta actually produces.

A Brief is a researched answer to one question: a short summary, key findings,
prose sections, and a numbered source list. Every factual sentence carries a
`[n]` marker pointing at a source, which is what makes a brief checkable rather
than merely plausible.

Storage is one JSON file per brief under `data/briefs/`. That keeps the library
transparent (readable, greppable, easy to back up or delete) and needs no
database for a single-user app.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from secrets import token_urlsafe

from config import settings


@dataclass
class Source:
    n: int                 # citation number, 1-based
    title: str
    url: str
    site: str = ""
    quote: str = ""        # the passage that supports the citing claim
    used: bool = True      # False = gathered but not cited in the final brief

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Section:
    heading: str
    body: str              # markdown, with [n] citation markers

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Brief:
    id: str
    question: str
    title: str
    summary: str = ""
    key_findings: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    gaps: str = ""                                     # what could not be verified
    subquestions: list[str] = field(default_factory=list)
    created: str = ""
    model: str = ""
    search_provider: str = ""
    stats: dict = field(default_factory=dict)          # sources_read, sources_cited, seconds

    # -- lifecycle ---------------------------------------------------------- #
    @staticmethod
    def new_id() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + token_urlsafe(3)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["sections"] = [s.to_dict() for s in self.sections]
        data["sources"] = [s.to_dict() for s in self.sources]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Brief":
        data = dict(data)
        data["sections"] = [Section(**s) for s in data.get("sections", [])]
        data["sources"] = [Source(**s) for s in data.get("sources", [])]
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def summary_dict(self) -> dict:
        """Compact shape for list views and tool results — no section bodies."""
        return {
            "id": self.id,
            "title": self.title,
            "question": self.question,
            "summary": self.summary,
            "created": self.created,
            "sources_cited": len([s for s in self.sources if s.used]),
            "sources_read": self.stats.get("sources_read", len(self.sources)),
        }


class BriefStore:
    """A tiny file-backed collection of briefs, newest first."""

    def __init__(self, directory=None) -> None:
        self._dir = directory
        self._lock = threading.Lock()

    @property
    def dir(self):
        # Resolved lazily so tests can repoint settings.data_dir per test.
        return self._dir or settings.briefs_dir

    def _path(self, brief_id: str):
        # Ids are generated internally, but they also arrive from HTTP and from
        # the model, so refuse anything that could escape the directory.
        if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", brief_id or ""):
            raise ValueError(f"Invalid brief id: {brief_id!r}")
        return self.dir / f"{brief_id}.json"

    def save(self, brief: Brief) -> Brief:
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            path = self._path(brief.id)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(brief.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(path)
        return brief

    def get(self, brief_id: str) -> Brief | None:
        try:
            path = self._path(brief_id)
        except ValueError:
            return None
        if not path.exists():
            return None
        try:
            return Brief.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    def list(self, query: str = "", limit: int = 50) -> list[Brief]:
        directory = self.dir
        if not directory.exists():
            return []
        briefs: list[Brief] = []
        for path in sorted(directory.glob("*.json"), reverse=True):
            brief = self.get(path.stem)
            if brief is not None:
                briefs.append(brief)
        q = (query or "").strip().lower()
        if q:
            briefs = [b for b in briefs if q in _haystack(b)]
        return briefs[:limit]

    def delete(self, brief_id: str) -> bool:
        try:
            path = self._path(brief_id)
        except ValueError:
            return False
        if path.exists():
            path.unlink()
            return True
        return False


def _haystack(brief: Brief) -> str:
    return " ".join(
        [brief.title, brief.question, brief.summary, *brief.key_findings]
    ).lower()


store = BriefStore()

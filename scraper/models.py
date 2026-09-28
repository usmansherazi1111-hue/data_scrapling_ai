from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any
from datetime import datetime, timezone

@dataclass
class Evidence:
    value: Any
    source_url: str
    source_type: str
    confidence: float

@dataclass
class FieldResult:
    value: Any = "Not Found"
    confidence: float = 0.0
    source_url: str = ""
    source_type: str = ""
    evidence: list[Evidence] = field(default_factory=list)

    def jsonable(self):
        d = asdict(self)
        return d

@dataclass
class PageRecord:
    url: str
    status: int | None
    title: str
    mode: str
    elapsed_ms: int
    discovered_score: float = 0.0
    category: str = "other"
    error: str = ""
    links: int = 0
    xhr_count: int = 0
    text_chars: int = 0
    markdown_chars: int = 0
    challenge: str = ""

@dataclass
class CrawlResult:
    job_id: str
    started_at: str
    finished_at: str | None
    status: str
    seed_url: str
    pages: list[PageRecord] = field(default_factory=list)
    fields: dict[str, FieldResult] = field(default_factory=dict)
    raw_pages: dict[str, dict[str, Any]] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    enrichment: dict[str, Any] | None = None

    def jsonable(self):
        return {
            "job_id": self.job_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "seed_url": self.seed_url,
            "pages": [asdict(x) for x in self.pages],
            "fields": {k: v.jsonable() for k, v in self.fields.items()},
            "raw_pages": self.raw_pages,
            "stats": self.stats,
            "config": self.config,
            "enrichment": self.enrichment,
        }

def now_iso():
    return datetime.now(timezone.utc).isoformat()

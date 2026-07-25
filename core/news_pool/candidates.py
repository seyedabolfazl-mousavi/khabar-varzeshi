"""Pool candidate dataclass for one RSS entry in the short-term news pool."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from core.models import RssSource


@dataclass
class PoolCandidate:
    source: RssSource
    entry: Any
    canonical_url: str
    title: str
    description: str
    pub_date: datetime
    priority: int

    @property
    def source_name(self) -> str:
        return self.source.name

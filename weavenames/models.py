"""Shared Pydantic models for the weavenames pipeline.

Every stage of the pipeline produces or consumes one of these. Keeping them
here means there's exactly one place to look when you want to know the shape
of data flowing through the system.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Status values returned by individual availability probes.
# - free: registry returned 404 / not found
# - taken: registry returned a usable record (someone else owns it)
# - soft_claimed: taken, but interpretation flagged as squatter / abandoned
# - reserved: registry-level reservation (e.g. github 'admin')
# - available_soon: domain in pending-delete / redemption (RFC 8056)
# - premium: registrar surfaces premium / aftermarket pricing
# - error: probe failed (network, rate limit, parse error)
# - skipped: probe intentionally not run
AvailabilityStatus = Literal[
    "free",
    "taken",
    "soft_claimed",
    "reserved",
    "available_soon",
    "premium",
    "error",
    "skipped",
]


class AvailabilityResult(BaseModel):
    """Result of a single availability probe against one registry."""

    registry: str  # "pypi", "npm", "github_user", "github_repo", "domain:.com", ...
    name: str
    status: AvailabilityStatus
    flags: list[str] = Field(default_factory=list)
    detail: str | None = None
    raw: dict | None = None  # raw response, kept small / sanitized
    available_at: datetime | None = None  # set when status == available_soon


class TrademarkHit(BaseModel):
    """A single USPTO TESS / TSDR hit for a candidate."""

    serial_number: str | None = None
    mark: str
    status: str | None = None
    classes: list[str] = Field(default_factory=list)
    owner: str | None = None


class CandidateScores(BaseModel):
    """Composite score components for ranking."""

    availability: float = 0.0  # [0, 1]
    pairwise_fit: float = 0.0  # [0, 1] derived from tournament
    pronounceability: float = 0.0  # [0, 1]
    length_penalty: float = 0.0  # [0, 1] (positive = penalty)
    composite: float = 0.0


class Candidate(BaseModel):
    """A single name candidate as it moves through the pipeline."""

    name: str
    rejected: bool = False
    reject_reason: str | None = None
    availability: dict[str, AvailabilityResult] = Field(default_factory=dict)
    trademark_hits: list[TrademarkHit] = Field(default_factory=list)
    pairwise_wins: int = 0
    pairwise_matches: int = 0
    rationale: str | None = None
    scores: CandidateScores = Field(default_factory=CandidateScores)

    @property
    def lowered(self) -> str:
        return self.name.lower()


class TasteProfile(BaseModel):
    """User taste profile persisted to ~/.weavenames/profile.json."""

    class Preferences(BaseModel):
        syllables: list[int] = Field(default_factory=lambda: [1, 2, 3])
        max_length: int = 14
        avoid_suffixes: list[str] = Field(
            default_factory=lambda: ["-ify", "-ly", "-ster"]
        )
        favor_roots: list[str] = Field(
            default_factory=lambda: ["greek", "latin", "anglo-saxon"]
        )

    class HistoryEntry(BaseModel):
        name: str
        reason: str | None = None

    class History(BaseModel):
        accepted: list[str] = Field(default_factory=list)
        rejected: list["TasteProfile.HistoryEntry"] = Field(default_factory=list)

    preferences: Preferences = Field(default_factory=Preferences)
    history: History = Field(default_factory=History)


TasteProfile.History.model_rebuild()

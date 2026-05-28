from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel, Column, JSON


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Board(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    created_at: datetime = Field(default_factory=_utcnow)


class Node(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    board_id: int = Field(foreign_key="board.id", index=True)
    short_id: str = Field(index=True)
    type: str
    x: float = 0.0
    y: float = 0.0
    w: float = 240.0
    h: float = 160.0
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = "idle"
    created_at: datetime = Field(default_factory=_utcnow)


class Edge(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    board_id: int = Field(foreign_key="board.id", index=True)
    source_id: int = Field(foreign_key="node.id")
    target_id: int = Field(foreign_key="node.id")
    kind: str = "ref"
    # Per-edge variant pin: when the source node holds multiple variants
    # (`data.mediaIds`), this index selects WHICH variant feeds the
    # downstream as a reference. None = "fall back to the source's
    # active mediaId" (the natural single-variant case).
    #
    # Why per-edge instead of expanding all variants on the wire: each
    # variant of the same upstream produces a SEPARATE Flow API call
    # (Flow doesn't bind output[i] to input[i] when both are
    # multi-variant). Pinning lets the user say "use variant 2 for
    # downstream A, variant 3 for downstream B" with two clicks; the
    # edge UI surfaces the pinned index so the binding stays visible.
    source_variant_idx: Optional[int] = None


class Request(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    node_id: Optional[int] = Field(default=None, foreign_key="node.id", index=True)
    account_id: Optional[int] = Field(default=None, foreign_key="flowaccount.id", index=True)
    type: str
    params: dict = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = "queued"
    result: dict = Field(default_factory=dict, sa_column=Column(JSON))
    error: Optional[str] = None
    dispatch_attempts: int = 0
    next_retry_at: Optional[datetime] = None
    last_dispatch_error: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)
    finished_at: Optional[datetime] = None


class Asset(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    # node_id is optional — assets can arrive from TRPC before any node
    # binding (e.g. the user browses an old Flow project).
    node_id: Optional[int] = Field(default=None, foreign_key="node.id", index=True)
    kind: str  # image | video | thumbnail
    # Media id (the hex uuid from Google Flow). Unique so ingest can upsert.
    uuid_media_id: Optional[str] = Field(default=None, index=True, unique=True)
    # Latest captured signed GCS URL (expires — refreshed when user reopens
    # Flow tab).
    url: Optional[str] = None
    local_path: Optional[str] = None
    mime: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)


class MediaProjectMapping(SQLModel, table=True):
    """Cross-project media re-upload cache.

    Flow scopes mediaIds to the project they were uploaded in — a
    ref_media_id from project A is unknown to project B even though we
    have the bytes cached locally. When a dispatch needs to reference
    media from another project (e.g. a cross-board Reference reused on
    a different board), we re-upload the bytes under the target project
    and record the (original, project) → project-local mapping here so
    subsequent dispatches skip the upload round-trip.

    Each row says: "bytes of `original_media_id` are also available
    under `project_id` as `project_local_media_id`". Unique on
    (original_media_id, project_id) — composite index in __table_args__.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    original_media_id: str = Field(index=True)
    project_id: str = Field(index=True)
    project_local_media_id: str
    created_at: datetime = Field(default_factory=_utcnow)
    __table_args__ = (
        UniqueConstraint(
            "original_media_id", "project_id",
            name="uq_media_project_mapping",
        ),
    )


class Reference(SQLModel, table=True):
    """User-curated saved media for cross-board reuse.

    Distinct from Asset (auto-managed cache index). Each Reference
    points at one media_id and snapshots enough metadata to spawn a
    brand-new visual_asset node in any board without re-vision or
    re-upload.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    media_id: str = Field(index=True, unique=True)
    url: Optional[str] = None
    label: str = ""
    kind: str  # "image" | "character" | "visual_asset" | "storyboard_shot"
    ai_brief: Optional[str] = None
    aspect_ratio: Optional[str] = None
    tags: list = Field(default_factory=list, sa_column=Column(JSON))
    pinned: bool = False
    position: int = 0
    source_board_id: Optional[int] = Field(default=None, foreign_key="board.id", index=True)
    source_node_short_id: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)


class ChatMessage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    board_id: int = Field(foreign_key="board.id", index=True)
    role: str  # user | assistant | system
    content: str
    mentions: list = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow)


class Plan(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    board_id: int = Field(foreign_key="board.id", index=True)
    spec: dict = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = "draft"  # draft | approved | running | done | failed
    created_at: datetime = Field(default_factory=_utcnow)


class Scenario(SQLModel, table=True):
    """High-level script plan for a multi-scene Auto Flow run.

    A Scenario belongs to a board and may optionally be represented by a
    canvas node later. Task 1 only establishes the durable shape; planner,
    generation, voice, lipsync, and merge execution will build on this table.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    board_id: int = Field(foreign_key="board.id", index=True)
    node_id: Optional[int] = Field(default=None, foreign_key="node.id", index=True)
    theme: str
    extra_description: str = ""
    content_style: str = ""
    scene_count: int = 1
    video_audio_mode: str = "silent"
    voice_id: Optional[str] = None
    final_video_media_id: Optional[str] = None
    status: str = "draft"  # draft | planned | running | partial | done | failed
    # Source refs selected by the user at planner time. Expected keys:
    # background_media_ids, character_media_ids, visual_asset_media_ids.
    refs: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

class ScenarioScene(SQLModel, table=True):
    """One planned scene inside a Scenario.

    Each row carries all text required by later stages: background prompt,
    scene composition prompt, motion prompt, and voice script/direction.
    Media ids are filled progressively as generation steps complete.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    scenario_id: int = Field(foreign_key="scenario.id", index=True)
    idx: int = Field(index=True)
    title: str = ""
    background_description: str = ""
    background_image_prompt: str = ""
    composition_prompt: str = ""
    motion_prompt: str = ""
    voice_script: str = ""
    voice_direction: str = ""
    duration_seconds: float = 8.0
    status: str = "planned"  # planned | background_done | image_done | video_done | voice_done | error
    # Optional per-scene ref choices. Same shape as Scenario.refs, but scoped
    # to this scene if the planner decides a scene uses a subset.
    refs: dict = Field(default_factory=dict, sa_column=Column(JSON))
    background_media_id: Optional[str] = None
    image_media_id: Optional[str] = None
    video_media_id: Optional[str] = None
    voice_media_id: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

class PlanRevision(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    plan_id: int = Field(foreign_key="plan.id", index=True)
    rev_no: int
    spec: dict = Field(default_factory=dict, sa_column=Column(JSON))
    edits: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow)


class PipelineRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    plan_id: int = Field(foreign_key="plan.id", index=True)
    status: str = "pending"  # pending | running | done | failed
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None


class BoardFlowProject(SQLModel, table=True):
    """1:1 link between a local board and a Google Flow project_id.

    Kept as a separate table so we don't have to migrate the Board schema.
    Paygate tier is loaded realtime from the extension via /api/auth/me,
    not persisted here — the binding is purely about project identity.
    """
    board_id: int = Field(primary_key=True, foreign_key="board.id")
    flow_project_id: str
    created_at: datetime = Field(default_factory=_utcnow)


class FlowAccount(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    label: str
    provider: str = "flow"
    email: Optional[str] = None
    status: str = "active"  # active | paused | disabled | unhealthy
    priority_weight: int = 100
    credential: Optional[str] = None
    chrome_user_data_dir: Optional[str] = None
    paygate_tier: Optional[str] = None
    credits: Optional[str] = None
    cooldown_until: Optional[datetime] = None
    last_error: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class FlowAccountHealthEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="flowaccount.id", index=True)
    status: str
    message: str = ""
    created_at: datetime = Field(default_factory=_utcnow)


class FlowDispatchEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    request_id: int = Field(foreign_key="request.id", index=True)
    account_id: Optional[int] = Field(default=None, foreign_key="flowaccount.id", index=True)
    attempt_no: int = 1
    outcome: str = "picked"  # picked | failed | done
    decision_reason: str = ""
    error_code: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)

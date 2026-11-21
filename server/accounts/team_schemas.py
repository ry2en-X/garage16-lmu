"""Request and response bodies of the team endpoints."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class TeamCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class TeamJoinRequest(BaseModel):
    invite_code: str = Field(min_length=1, max_length=32)


class TeamInvitationCreateRequest(BaseModel):
    """POST /teams/{id}/invitations. Addressed by driver_id, not a name
    or email — see models.TeamInvitation's docstring for why. The web UI
    resolves a display name to a driver_id first via GET
    /drivers/lookup, same as any other caller would."""

    invited_driver_id: int


class TeamInvitationOut(BaseModel):
    id: int
    team_id: int
    team_name: str
    invited_driver_id: int
    invited_display_name: str
    created_by_driver_id: int
    created_by_display_name: str
    status: str  # pending | accepted | declined | expired | revoked
    created_at: str
    expires_at: str
    accepted_at: Optional[str] = None
    declined_at: Optional[str] = None
    revoked_at: Optional[str] = None


class TeamOut(BaseModel):
    id: int
    name: str
    invite_code: str
    member_count: int
    role: Optional[str] = None  # the CALLING driver's role on this team — None for /join's response shape reuse


class TeamUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=1000)
    discord_announcements_enabled: Optional[bool] = None


class TeamRoleChangeRequest(BaseModel):
    role: str = Field(pattern="^(admin|member)$")  # "owner" is never set via this endpoint — see transfer-ownership


class TeamTransferOwnershipRequest(BaseModel):
    new_owner_driver_id: int


class TeamDetail(BaseModel):
    """Team-Übersicht — every number computed live from Lap/RecordEvent,
    nothing artificially stored (per the explicit requirement: don't add
    counter columns, derive from existing data)."""

    id: int
    name: str
    description: Optional[str] = None
    invite_code: str
    discord_announcements_enabled: bool
    my_role: str
    member_count: int
    active_driver_count: int  # uploaded at least one lap in the last 14 days
    total_laps: int
    pbs_this_week: int  # RecordEvent record_type="PR" in the last 7 days
    team_best_count: int  # distinct (track, car_model) combos with a valid team lap


class TeamStatsOut(BaseModel):
    """GET /teams/{id}/stats?window=... — same shape as
    TeamDetail's fixed-window fields, but for a caller-selected window
    instead of the hardcoded 14/7-day ones there (which stay as-is for
    backward compatibility, see that endpoint)."""

    window: str
    laps: int
    pbs: int
    active_drivers: int
    records: int  # TEAM_BEST events for this team created within the window


class TeamMemberOut(BaseModel):
    driver_id: int
    display_name: str
    role: str
    is_active: bool  # uploaded at least one lap in the last 14 days
    lap_count: int
    pb_count: int  # total RecordEvent record_type="PR" ever, for this driver
    last_activity: Optional[str] = None  # ISO timestamp of their most recent lap, if any


class TeamRecordOut(BaseModel):
    """One row per (track, car_model) the team has a valid lap on — the
    team's current best there. Derived live from Lap, same principle as
    the rest of the leaderboard system (no CurrentRecord table).

    your_lap_time/gap are None when the
    caller has no valid lap of their own on this exact (track,
    car_model) — not 0.0, which would misleadingly read as "you tied the
    record". you_hold_it is true exactly when the caller IS the
    record-holder (driver_name matches them), in which case gap is 0.0."""

    track_name: str
    car_class: Optional[str] = None
    car_model: str
    lap_time: float
    driver_name: str
    your_lap_time: Optional[float] = None
    gap: Optional[float] = None
    you_hold_it: bool = False


class TeamActivityItem(BaseModel):
    """One entry in the team's activity console — reuses RecordEvent,
    no separate activity-log table."""

    record_type: str  # "PR" | "WR" | "TEAM_BEST"
    driver_name: str
    track_name: str
    car_name: str
    lap_time: float
    previous_best: Optional[float] = None
    created_at: str
    # The lap behind the entry (link to the analysis, one row per lap) and the car model
    lap_id: Optional[int] = None
    car_model: Optional[str] = None


class TeamLeaderboardEntry(BaseModel):
    lap_id: Optional[int] = None  # See LeaderboardEntry.lap_id
    driver_id: int
    driver_name: str
    lap_time: float
    sector_times: List[float]
    is_valid: bool
    uploaded_at: str
    delta_to_leader: float  # 0.0 for the leader themself

"""Usage analytics REST endpoints.

Usage routes:
  GET /api/usage/tokens?project_id=&period=30d   → [{date, input_tokens, output_tokens, model}]
  GET /api/usage/resources?project_id=           → [{timestamp, cpu_pct, ram_mb}]
  GET /api/usage/github?project_id=              → [{date, api_calls}]
  GET /api/usage/summary                         → aggregate token counts across all projects
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PERIODS: dict[str, int] = {
    "1d": 1,
    "7d": 7,
    "30d": 30,
    "90d": 90,
}


def _period_start(period: str) -> str:
    """Return ISO-8601 timestamp for start of the requested period."""
    days = _PERIODS.get(period, 7)
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class TokenDataPoint(BaseModel):
    date: str
    input_tokens: int
    output_tokens: int
    model: str


class ResourceDataPoint(BaseModel):
    timestamp: str
    cpu_pct: float
    ram_mb: float


class GitHubApiDataPoint(BaseModel):
    date: str
    api_calls: int


class UsageSummaryResponse(BaseModel):
    today_tokens: int
    month_tokens: int
    oauth_mode: bool = False
    message: str | None = None


def _is_oauth_mode() -> bool:
    """Return True when not using a real API key (OAuth or no key configured)."""
    from atc.agents.auth import get_auth_mode

    return get_auth_mode() != "api_key"


# ---------------------------------------------------------------------------
# GET /api/usage/summary
# ---------------------------------------------------------------------------


@router.get("/summary", response_model=UsageSummaryResponse)
async def get_usage_summary(request: Request) -> UsageSummaryResponse:
    """Aggregate token totals across all projects for today and this month."""
    db = request.app.state.db
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    month_start = datetime.now(UTC).strftime("%Y-%m-01")

    tok = "COALESCE(total_tokens, COALESCE(input_tokens,0)+COALESCE(output_tokens,0)+COALESCE(reasoning_output_tokens,0))"
    tok_cond = f"CASE WHEN recorded_at >= ? THEN {tok} ELSE 0 END"
    cursor = await db.execute(
        f"""SELECT
             COALESCE(SUM({tok_cond}), 0) as today_tokens,
             COALESCE(SUM({tok_cond}), 0) as month_tokens
           FROM usage_events
           WHERE event_type = 'ai_tokens'""",
        (today, month_start),
    )
    row = await cursor.fetchone()
    if row is None:
        return UsageSummaryResponse(today_tokens=0, month_tokens=0)
    return UsageSummaryResponse(
        today_tokens=int(row[0]),
        month_tokens=int(row[1]),
    )


# ---------------------------------------------------------------------------
# GET /api/usage/tokens
# ---------------------------------------------------------------------------


@router.get("/tokens", response_model=list[TokenDataPoint])
async def get_token_usage(
    request: Request,
    project_id: str | None = None,
    period: str = "30d",
) -> list[TokenDataPoint]:
    """Daily token totals grouped by model for the given period."""
    db = request.app.state.db
    since = _period_start(period)

    if project_id:
        cursor = await db.execute(
            """SELECT substr(recorded_at, 1, 10) as date,
                      COALESCE(model, 'unknown') as model,
                      COALESCE(SUM(input_tokens), 0) as input_tokens,
                      COALESCE(SUM(output_tokens), 0) as output_tokens
               FROM usage_events
               WHERE event_type = 'ai_tokens'
                 AND project_id = ?
                 AND recorded_at >= ?
               GROUP BY date, model
               ORDER BY date, model""",
            (project_id, since),
        )
    else:
        cursor = await db.execute(
            """SELECT substr(recorded_at, 1, 10) as date,
                      COALESCE(model, 'unknown') as model,
                      COALESCE(SUM(input_tokens), 0) as input_tokens,
                      COALESCE(SUM(output_tokens), 0) as output_tokens
               FROM usage_events
               WHERE event_type = 'ai_tokens'
                 AND recorded_at >= ?
               GROUP BY date, model
               ORDER BY date, model""",
            (since,),
        )
    rows = await cursor.fetchall()
    return [
        TokenDataPoint(
            date=str(r[0]),
            model=str(r[1]),
            input_tokens=int(r[2]),
            output_tokens=int(r[3]),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET /api/usage/resources
# ---------------------------------------------------------------------------


@router.get("/resources", response_model=list[ResourceDataPoint])
async def get_resource_usage(
    request: Request,
    project_id: str | None = None,
) -> list[ResourceDataPoint]:
    """Recent CPU/RAM snapshots, optionally filtered by project."""
    db = request.app.state.db
    since = _period_start("1d")

    if project_id:
        cursor = await db.execute(
            """SELECT recorded_at,
                      COALESCE(AVG(CASE WHEN event_type='cpu' THEN cpu_pct END), 0) as cpu_pct,
                      COALESCE(AVG(CASE WHEN event_type='ram' THEN ram_mb END), 0) as ram_mb
               FROM usage_events
               WHERE project_id = ?
                 AND event_type IN ('cpu', 'ram')
                 AND recorded_at >= ?
               GROUP BY substr(recorded_at, 1, 16)
               ORDER BY recorded_at DESC
               LIMIT 200""",
            (project_id, since),
        )
    else:
        cursor = await db.execute(
            """SELECT recorded_at,
                      COALESCE(AVG(CASE WHEN event_type='cpu' THEN cpu_pct END), 0) as cpu_pct,
                      COALESCE(AVG(CASE WHEN event_type='ram' THEN ram_mb END), 0) as ram_mb
               FROM usage_events
               WHERE event_type IN ('cpu', 'ram')
                 AND recorded_at >= ?
               GROUP BY substr(recorded_at, 1, 16)
               ORDER BY recorded_at DESC
               LIMIT 200""",
            (since,),
        )
    rows = await cursor.fetchall()
    return [
        ResourceDataPoint(
            timestamp=str(r[0]),
            cpu_pct=float(r[1]),
            ram_mb=float(r[2]),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET /api/usage/github
# ---------------------------------------------------------------------------


@router.get("/github", response_model=list[GitHubApiDataPoint])
async def get_github_api_usage(
    request: Request,
    project_id: str | None = None,
) -> list[GitHubApiDataPoint]:
    """Daily GitHub API call counts."""
    db = request.app.state.db
    since = _period_start("30d")

    if project_id:
        cursor = await db.execute(
            """SELECT substr(recorded_at, 1, 10) as date,
                      COALESCE(SUM(api_calls), 0) as api_calls
               FROM usage_events
               WHERE event_type = 'github_api'
                 AND project_id = ?
                 AND recorded_at >= ?
               GROUP BY date
               ORDER BY date""",
            (project_id, since),
        )
    else:
        cursor = await db.execute(
            """SELECT substr(recorded_at, 1, 10) as date,
                      COALESCE(SUM(api_calls), 0) as api_calls
               FROM usage_events
               WHERE event_type = 'github_api'
                 AND recorded_at >= ?
               GROUP BY date
               ORDER BY date""",
            (since,),
        )
    rows = await cursor.fetchall()
    return [GitHubApiDataPoint(date=str(r[0]), api_calls=int(r[1])) for r in rows]

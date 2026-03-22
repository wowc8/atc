# 009 — Rolling 30-Day Budget Window

**Date**: 2026-03-22
**Status**: accepted

## Decision

The monthly cost limit in `project_budgets` is evaluated over a rolling 30-day window (`recorded_at >= datetime('now', '-30 days')`) rather than a calendar-month boundary (`strftime('%Y-%m', recorded_at) = strftime('%Y-%m', 'now')`).

## Context

The calendar-month approach creates a predictable end-of-month pattern where teams exhaust their remaining budget in the final days before reset, leading to artificial spend spikes and session pauses at awkward times. A rolling window smooths this out by always measuring the last 30 days of activity regardless of when in the month the measurement occurs.

The alternative (calendar month) was the initial implementation. It was simple but caused practical problems: a project that spent heavily on day 28 could trigger budget_exceeded on day 30, then reset to zero on day 1 with full budget available again, making the limit feel arbitrary rather than a genuine rate control.

## Consequences

- Budget status reflects real recent usage rather than calendar position
- No month-end spend spike pattern driven by imminent resets
- `BudgetEnforcer._compute_status()` passes no date parameter for the cost query; the rolling window is expressed in SQL via `datetime('now', '-30 days')`
- Existing `daily_token_limit` check is unchanged (still uses today's date)

"""Shared, allowlisted column sorting for server-side-paginated listing pages.

Used by the Supplier, Customer, and Item list routes so column-header
sorting behaves consistently and is always applied before pagination (so a
sort covers the whole filtered dataset, not just the current page).
"""
from flask import request
from sqlalchemy import case


def resolve_sort(allowed, default_key, default_dir="asc"):
    """Read ``?sort=`` and ``?dir=`` from the query string, validated against
    an allowlist.

    `allowed` is a dict mapping a safe column key (used in the URL and in the
    template) to the SQLAlchemy column/expression to order by — never a raw
    string built from user input. Anything outside `allowed`, or an invalid
    `dir`, falls back to the given defaults.

    Returns (sort_key, direction), where sort_key is always a key of
    `allowed` and direction is always "asc" or "desc".
    """
    sort_key = request.args.get("sort", default_key)
    if sort_key not in allowed:
        sort_key = default_key
    direction = request.args.get("dir", default_dir)
    if direction not in ("asc", "desc"):
        direction = default_dir
    return sort_key, direction


def apply_sort(query, allowed, sort_key, direction, tiebreaker):
    """Apply ORDER BY for `sort_key`/`direction` (from `resolve_sort`) to
    `query`, using `allowed` to resolve the actual column.

    NULLs are always pushed to the end regardless of direction (via a CASE
    expression rather than dialect-specific NULLS FIRST/LAST, so this works
    identically on SQLite and PostgreSQL). `tiebreaker` (e.g. a primary key
    column) is appended last so row order — and therefore pagination — stays
    stable when the sorted column has duplicate values.
    """
    column = allowed[sort_key]
    null_rank = case((column.is_(None), 1), else_=0)
    ordered = column.desc() if direction == "desc" else column.asc()
    return query.order_by(null_rank, ordered, tiebreaker)


__all__ = ["resolve_sort", "apply_sort"]

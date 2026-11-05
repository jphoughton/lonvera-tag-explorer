"""The Feed definition: one entry per target table, with how to pull it, where it lands, how it is compared with the sheet-fed table and when
it counts as stale. Source modules (upstack, warehance, meta_feeds) each export a list FEEDS."""
from __future__ import annotations

import datetime as dt
import importlib
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from psycopg import sql

from .common import Ctx, Span, day_floor_where, trailing_span


@dataclass(frozen=True)
class Metric:
    """A compared column: a value passes when |a - b| <= abs_tol or |a - b| / max(|a|, |b|) <= rel_tol."""
    col: str
    abs_tol: float = 0.01
    rel_tol: float = 0.0


def default_span(ctx: Ctx, days: int) -> Span:
    return trailing_span(ctx.today, days)


@dataclass(frozen=True)
class Feed:
    key: str                                   # stable name used in proj_feed_config / proj_feed_attempts
    label: str                                 # shown in freshness chips (finished-document wording)
    source: str                                # 'upstack' | 'warehance' | 'meta' | 'expandfi'
    main: str                                  # the sheet-fed table the app reads
    shadow: str                                # proj_feedx_* twin written during the dual-run
    cols: tuple[str, ...]                      # insert columns (loaded_at is defaulted)
    key_cols: tuple[str, ...]                  # primary key columns, in cols
    day_col: str                               # the date column the window and the freshness use
    pull: Callable[[Ctx, Span], list[tuple]] = field(repr=False, default=lambda ctx, span: [])
    days: int = 14                             # default trailing window per run
    stale_after: int = 2                       # freshness: days after which the feed counts as stale while the feed itself is the source (shadow, direct)
    sheet_stale_after: int = 2                 # freshness: the same limit while the sheet-fed path is the source (the module's own threshold; a test pins them)
    fixed_window: bool = False                 # True: history cannot be re-pulled, so a caller's wider `days` never widens the window (bids)
    metrics: tuple[Metric, ...] = ()
    span: Callable[[Ctx, int], Span] = default_span
    delete_where: Callable[[Span], tuple[sql.Composable, tuple]] | None = None
    sheet_tab: str = ""                        # sheet raw tab the main table is fed from today
    sheet_parser: tuple[str, str] | None = None  # (module, function): grid -> rows in `cols` order
    settle_days: int = 3                       # days at the end of the window compared at 3x tolerance and never failed
    skip_compare: Callable[[tuple, dt.date], bool] | None = None   # row, today -> True when the row must not be compared (e.g. partial period)
    scope_where: str = ""                      # constant WHERE limiting a shared table to this feed (e.g. "location = 'revamp'"), used by freshness and verify
    fresh_where: str = ""                      # extra WHERE used by the freshness check only (e.g. "grain = 'day'")
    native: bool = False                       # no sheet twin: the feed always runs (default mode shadow) and `main` is the table it writes
    conflict_nothing: bool = False             # insert with ON CONFLICT DO NOTHING (never overwrite a row the delete did not remove)
    notes: str = ""

    def module_days(self, days: int | None) -> int:
        """Window for a pull started by a projection module's refresh: the module's trailing `days` (14 by default), never narrower than the feed's own
        window (Amazon needs 21 so matured days are refreshed) and never wider for a feed whose history cannot be rebuilt."""
        if self.fixed_window or not days:
            return self.days
        return max(days, self.days)

    @property
    def default_mode(self) -> str:
        return "shadow" if self.native else "sheet"

    def where(self, span: Span) -> tuple[sql.Composable, tuple]:
        if self.delete_where:
            return self.delete_where(span)
        return day_floor_where(self.day_col), (span.since,)

    def key_of(self, row: Sequence[Any]) -> tuple:
        return tuple(row[self.cols.index(c)] for c in self.key_cols)

    def day_of(self, row: Sequence[Any]) -> dt.date:
        return row[self.cols.index(self.day_col)]

    def parse_sheet(self, grid: list[list]) -> list[tuple]:
        if not self.sheet_parser:
            raise RuntimeError(f"{self.key}: no sheet parser")
        mod, fn = self.sheet_parser
        rows = getattr(importlib.import_module(mod), fn)(grid)
        return [tuple(r) for r in rows if r and r[0] is not None]

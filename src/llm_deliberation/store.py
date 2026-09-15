from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path("data") / "deliberation.db"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class StageRecord:
    id: int
    run_id: str
    name: str
    provider: str | None
    model: str | None
    status: str
    attempt: int
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    error: str | None
    started_at: str | None
    completed_at: str | None
    text: str | None = None
    # Model fallback provenance (see GeminiFallbackProvider). For a stage
    # that never falls back, requested_model == model and fallback_used is
    # False. model_attempts counts every attempt across the whole chain
    # (initial try + retries + fallback switches) for this stage execution.
    requested_model: str | None = None
    fallback_used: bool = False
    fallback_reason: str | None = None
    model_attempts: int = 1
    attempt_log: list[dict] | None = None
    # Typed classification of why a "failed" stage failed -- "empty_output" |
    # "output_truncated" | "structured_output_invalid" | "provider_error" |
    # None (never failed, or predates this column). Always None for a
    # succeeded/skipped/pending/running stage. User-facing code (e.g. the
    # deliberation-quality indicator) must branch on this, never on parsing
    # the free-text `error` string. A stage created before this column
    # existed has this as None even if it once failed -- a neutral legacy
    # default, not an invented classification (see store._STAGE_MIGRATION_COLUMNS).
    failure_reason: str | None = None


@dataclass(slots=True)
class RunRecord:
    id: str
    question: str
    context: str | None
    profile: str
    red_team_enabled: bool
    status: str
    created_at: str
    started_at: str | None
    completed_at: str | None
    estimated_total_cost_usd: float
    # Output/content language for every user-facing generated stage in this
    # run ("en" | "et") -- distinct from, and never conflated with, a web
    # viewer's UI-chrome language preference (see web/i18n.py). Runs created
    # before this field existed are migrated to "en" (see
    # _RUN_MIGRATION_COLUMNS), never inferred from their stored content.
    language: str = "en"
    # Optional, user-defined per-run hard cost cap -- an application-side
    # safety limit, never the provider account's own balance (see
    # cost_budget.py). Stored as TEXT (the Decimal's exact string form, e.g.
    # "0.75") rather than REAL so the budget comparison never goes through a
    # float round-trip; None means "no cap" (current, pre-budget-feature
    # behavior), not "$0". A run created before this field existed is
    # migrated to NULL/None -- see _RUN_MIGRATION_COLUMNS -- which is the
    # correct, backward-compatible "no cap" default, not an invented one.
    max_run_cost_usd: str | None = None
    stages: list[StageRecord] = field(default_factory=list)


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    context TEXT,
    profile TEXT NOT NULL,
    red_team_enabled INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    estimated_total_cost_usd REAL NOT NULL DEFAULT 0.0,
    language TEXT NOT NULL DEFAULT 'en'
);

CREATE TABLE IF NOT EXISTS stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0.0,
    error TEXT,
    started_at TEXT,
    completed_at TEXT,
    requested_model TEXT,
    fallback_used INTEGER NOT NULL DEFAULT 0,
    fallback_reason TEXT,
    model_attempts INTEGER NOT NULL DEFAULT 1,
    attempt_log TEXT,
    failure_reason TEXT,
    UNIQUE(run_id, name)
);

CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    stage_id INTEGER NOT NULL REFERENCES stages(id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL,
    text_content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stages_run_id ON stages(run_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_stage_id ON artifacts(stage_id);
"""

# Columns added after the initial schema. CREATE TABLE IF NOT EXISTS above
# covers brand-new databases; existing ones (e.g. data/deliberation.db from
# before the Gemini fallback feature) are migrated in-place here so upgrading
# never requires deleting run history.
_STAGE_MIGRATION_COLUMNS: dict[str, str] = {
    "requested_model": "TEXT",
    "fallback_used": "INTEGER NOT NULL DEFAULT 0",
    "fallback_reason": "TEXT",
    "model_attempts": "INTEGER NOT NULL DEFAULT 1",
    "attempt_log": "TEXT",
    "failure_reason": "TEXT",
}

# A run created before bilingual support existed gets "en" -- a stated,
# backward-compatible default, never inferred from its stored content.
_RUN_MIGRATION_COLUMNS: dict[str, str] = {
    "language": "TEXT NOT NULL DEFAULT 'en'",
    # Nullable, no default value beyond SQL NULL -- see RunRecord.max_run_cost_usd.
    "max_run_cost_usd": "TEXT",
}


class Repository:
    """SQLite-backed persistence for runs, stages, and artifacts.

    Never stores API keys or other secrets; only questions, prompts-derived
    text, token counts, and cost estimates.
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._migrate_stage_columns()
        self._migrate_run_columns()
        self._conn.commit()

    def _migrate_stage_columns(self) -> None:
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(stages)")}
        for column, declaration in _STAGE_MIGRATION_COLUMNS.items():
            if column not in existing:
                self._conn.execute(f"ALTER TABLE stages ADD COLUMN {column} {declaration}")

    def _migrate_run_columns(self) -> None:
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(runs)")}
        for column, declaration in _RUN_MIGRATION_COLUMNS.items():
            if column not in existing:
                self._conn.execute(f"ALTER TABLE runs ADD COLUMN {column} {declaration}")

    def close(self) -> None:
        self._conn.close()

    # -- runs --------------------------------------------------------

    def insert_run(
        self,
        run_id: str,
        *,
        question: str,
        context: str | None,
        profile: str,
        red_team_enabled: bool,
        language: str = "en",
        max_run_cost_usd: str | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO runs "
            "(id, question, context, profile, red_team_enabled, status, "
            "created_at, estimated_total_cost_usd, language, max_run_cost_usd) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, 0.0, ?, ?)",
            (
                run_id,
                question,
                context,
                profile,
                int(red_team_enabled),
                utc_now_iso(),
                language,
                max_run_cost_usd,
            ),
        )
        self._conn.commit()

    def update_run_budget(self, run_id: str, max_run_cost_usd: str | None) -> None:
        """Change a run's stored budget cap -- the "Increase budget" action
        on a run blocked by run_budget_exceeded (see service.py). Deliberately
        separate from update_run(): a budget change is a distinct, explicit
        user action worth its own narrow method rather than one more optional
        keyword on the general updater.
        """
        self._conn.execute(
            "UPDATE runs SET max_run_cost_usd = ? WHERE id = ?", (max_run_cost_usd, run_id)
        )
        self._conn.commit()

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
        estimated_total_cost_usd: float | None = None,
        set_started_if_unset: bool = False,
    ) -> None:
        fields: list[str] = []
        values: list[object] = []
        if status is not None:
            fields.append("status = ?")
            values.append(status)
        if started_at is not None:
            fields.append("started_at = ?")
            values.append(started_at)
        if completed_at is not None:
            fields.append("completed_at = ?")
            values.append(completed_at)
        if estimated_total_cost_usd is not None:
            fields.append("estimated_total_cost_usd = ?")
            values.append(estimated_total_cost_usd)
        if set_started_if_unset:
            fields.append("started_at = COALESCE(started_at, ?)")
            values.append(utc_now_iso())
        if not fields:
            return
        values.append(run_id)
        self._conn.execute(f"UPDATE runs SET {', '.join(fields)} WHERE id = ?", values)
        self._conn.commit()

    def get_run(self, run_id: str) -> RunRecord:
        row = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown run_id: {run_id}")
        return self._run_from_row(row)

    def get_run_full(self, run_id: str) -> RunRecord:
        run = self.get_run(run_id)
        run.stages = self.list_stages(run_id)
        return run

    def list_runs(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RunRecord]:
        query = "SELECT * FROM runs"
        params: list[object] = []
        if status is not None:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self._conn.execute(query, params).fetchall()
        return [self._run_from_row(row) for row in rows]

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            id=row["id"],
            question=row["question"],
            context=row["context"],
            profile=row["profile"],
            red_team_enabled=bool(row["red_team_enabled"]),
            status=row["status"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            estimated_total_cost_usd=row["estimated_total_cost_usd"],
            language=row["language"],
            max_run_cost_usd=row["max_run_cost_usd"],
        )

    # -- stages ------------------------------------------------------

    def insert_stage(self, run_id: str, name: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO stages (run_id, name, status, attempt) VALUES (?, ?, 'pending', 0)",
            (run_id, name),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def get_stage(self, run_id: str, stage_ref: int | str) -> StageRecord:
        if isinstance(stage_ref, int):
            row = self._conn.execute(
                "SELECT * FROM stages WHERE run_id = ? AND id = ?",
                (run_id, stage_ref),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM stages WHERE run_id = ? AND name = ?",
                (run_id, stage_ref),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown stage {stage_ref!r} for run {run_id}")
        return self._stage_from_row(row)

    def list_stages(self, run_id: str) -> list[StageRecord]:
        rows = self._conn.execute(
            "SELECT * FROM stages WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()
        return [self._stage_from_row(row) for row in rows]

    def _stage_from_row(self, row: sqlite3.Row) -> StageRecord:
        text = None
        if row["status"] == "succeeded":
            art = self._conn.execute(
                "SELECT text_content FROM artifacts WHERE stage_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (row["id"],),
            ).fetchone()
            text = art["text_content"] if art else None
        attempt_log_raw = row["attempt_log"]
        return StageRecord(
            id=row["id"],
            run_id=row["run_id"],
            name=row["name"],
            provider=row["provider"],
            model=row["model"],
            status=row["status"],
            attempt=row["attempt"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            estimated_cost_usd=row["estimated_cost_usd"],
            error=row["error"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            text=text,
            requested_model=row["requested_model"],
            fallback_used=bool(row["fallback_used"]),
            fallback_reason=row["fallback_reason"],
            model_attempts=row["model_attempts"],
            attempt_log=json.loads(attempt_log_raw) if attempt_log_raw else None,
            failure_reason=row["failure_reason"],
        )

    def mark_stage_running(self, stage_id: int) -> None:
        self._conn.execute(
            "UPDATE stages SET status = 'running', attempt = attempt + 1, "
            "started_at = ?, error = NULL WHERE id = ?",
            (utc_now_iso(), stage_id),
        )
        self._conn.commit()

    def mark_stage_succeeded(
        self,
        stage_id: int,
        *,
        provider: str,
        model: str,
        text: str,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_usd: float,
        requested_model: str | None = None,
        fallback_used: bool = False,
        fallback_reason: str | None = None,
        model_attempts: int = 1,
        attempt_log: list[dict] | None = None,
    ) -> None:
        now = utc_now_iso()
        row = self._conn.execute(
            "SELECT run_id FROM stages WHERE id = ?", (stage_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown stage_id: {stage_id}")
        run_id = row["run_id"]
        # estimated_cost_usd accumulates (existing value + this attempt's
        # cost) rather than overwriting: a stage that previously failed one
        # or more paid attempts before eventually succeeding (via retry_stage,
        # or the one bounded truncation-recovery retry -- see
        # orchestrator.run_stage) must not have that earlier spend silently
        # replaced/dropped from the run's cost total (see sum_stage_costs).
        # reset_stage() deliberately never clears this column, so it is safe
        # to add to here.
        self._conn.execute(
            "UPDATE stages SET status = 'succeeded', provider = ?, model = ?, "
            "input_tokens = ?, output_tokens = ?, "
            "estimated_cost_usd = estimated_cost_usd + ?, "
            "requested_model = ?, fallback_used = ?, fallback_reason = ?, "
            "model_attempts = ?, attempt_log = ?, "
            "error = NULL, failure_reason = NULL, completed_at = ? WHERE id = ?",
            (
                provider,
                model,
                input_tokens,
                output_tokens,
                estimated_cost_usd,
                requested_model if requested_model is not None else model,
                int(fallback_used),
                fallback_reason,
                model_attempts,
                json.dumps(attempt_log) if attempt_log is not None else None,
                now,
                stage_id,
            ),
        )
        self._conn.execute(
            "INSERT INTO artifacts (run_id, stage_id, artifact_type, text_content, created_at) "
            "VALUES (?, ?, 'response_text', ?, ?)",
            (run_id, stage_id, text, now),
        )
        self._conn.commit()

    def mark_stage_failed(
        self,
        stage_id: int,
        *,
        error: str,
        requested_model: str | None = None,
        fallback_used: bool = False,
        fallback_reason: str | None = None,
        model_attempts: int = 1,
        attempt_log: list[dict] | None = None,
        estimated_cost_usd: float = 0.0,
        failure_reason: str | None = None,
    ) -> None:
        # estimated_cost_usd accumulates -- see the matching note in
        # mark_stage_succeeded. A stage retried multiple times, each
        # incurring some paid-but-unusable cost before finally succeeding or
        # being abandoned, keeps every attempt's spend in the run total.
        self._conn.execute(
            "UPDATE stages SET status = 'failed', error = ?, completed_at = ?, "
            "requested_model = ?, fallback_used = ?, fallback_reason = ?, "
            "model_attempts = ?, attempt_log = ?, "
            "estimated_cost_usd = estimated_cost_usd + ?, failure_reason = ? "
            "WHERE id = ?",
            (
                error,
                utc_now_iso(),
                requested_model,
                int(fallback_used),
                fallback_reason,
                model_attempts,
                json.dumps(attempt_log) if attempt_log is not None else None,
                estimated_cost_usd,
                failure_reason,
                stage_id,
            ),
        )
        self._conn.commit()

    def mark_stage_skipped(self, stage_id: int, *, reason: str) -> None:
        """Mark an optional stage as deliberately skipped by the user.

        Distinct from "failed": a skipped stage does not fail the run, and
        downstream stages proceed treating it as absent (the same way a
        disabled red-team stage is treated).
        """
        self._conn.execute(
            "UPDATE stages SET status = 'skipped', error = NULL, failure_reason = NULL, "
            "fallback_reason = ?, completed_at = ? WHERE id = ?",
            (reason, utc_now_iso(), stage_id),
        )
        self._conn.commit()

    def reset_stage(self, stage_id: int) -> None:
        # Deliberately does NOT touch estimated_cost_usd: any cost already
        # incurred by a prior attempt of this stage must survive a retry (see
        # mark_stage_succeeded/mark_stage_failed, which add to it rather than
        # overwrite it).
        self._conn.execute(
            "UPDATE stages SET status = 'pending', error = NULL, failure_reason = NULL, "
            "started_at = NULL, completed_at = NULL WHERE id = ?",
            (stage_id,),
        )
        self._conn.commit()

    def sum_stage_costs(self, run_id: str) -> float:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(estimated_cost_usd), 0.0) AS total "
            "FROM stages WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return float(row["total"])

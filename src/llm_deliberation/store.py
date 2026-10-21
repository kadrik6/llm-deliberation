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
    # Working-language contract provenance (see language_detect.py,
    # orchestrator.run_stage, AUDIT_REPORT.md's live-canary findings) --
    # "matched" | "mismatched" | "uncertain" | None. None for any stage
    # predating this feature, any stage the check does not apply to
    # (convergence_analysis, synthesis, or a stage with no working-language
    # override in effect), and for a "uncertain" observed_language pairing
    # is possible too (detector could not classify confidently). A mismatch
    # NEVER fails the stage on its own (see mark_stage_succeeded) -- do not
    # conflate this with failure_reason, which stays None here even when
    # language_contract_status == "mismatched".
    language_contract_status: str | None = None
    observed_language: str | None = None
    language_recovery_attempted: bool = False


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
    # Language verbose intermediate stages (orchestrator.WORKING_LANGUAGE_STAGES)
    # actually used for this run -- "en" | "et" | None. Distinct from
    # `language` (the OUTPUT language, unchanged): this field only ever
    # affects analysis_a/b, critique_*, red_team, revision_a/b; convergence_
    # analysis and synthesis always use `language` regardless (see
    # orchestrator.resolve_stage_language). None means "legacy run, this
    # feature did not exist yet" -- NOT "English was used": a legacy run's
    # effective working language is its own `language` (see
    # service._execute), never assumed to be "en". Set once at creation,
    # like `language`; never changed afterward, never rewritten for
    # historical runs.
    working_language: str | None = None
    stages: list[StageRecord] = field(default_factory=list)
    # Optional Stage 0 (question framing) record -- see ADR 009. None for
    # every run that didn't have it enabled (the overwhelming majority: it
    # is off by default) and for any run created before this feature
    # existed. Populated only by get_run_full(), exactly like `stages`.
    stage0: "Stage0Record | None" = None


@dataclass(slots=True)
class Stage0Record:
    """Optional, off-by-default pre-run phase (see ADR 009): one model
    classifies the problem type and writes an interview's worth of
    questions in a single call, the user answers them, and candidates A and
    B each independently produce a framing critique + premortem in one more
    call. `locked_at` is the one commit marker -- until it is set, this row
    represents a Stage 0 still in progress (see service.lock_question_framing).

    `*_json` columns hold canonical, schema-validated JSON (see
    question_framing.py) -- the same "validate once, store the canonical
    form" pattern convergence_analysis's artifact already uses. None means
    that sub-step hasn't completed yet, never an empty/invented result.
    """

    run_id: str
    created_at: str
    skipped: bool
    problem_type_json: str | None
    interview_json: str | None
    framing_analysis_a_json: str | None
    framing_analysis_b_json: str | None
    # The question actually used for the main pipeline once locked -- the
    # user's original question (runs.question, never mutated -- see ADR
    # 003/009) stays available alongside this for traceability; this is
    # only ever set by lock_stage0().
    final_question: str | None
    success_criteria: str | None
    locked_at: str | None
    estimated_cost_usd: float


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

-- Stage 0 (question framing, see ADR 009) -- optional, off by default.
-- A brand-new table (not new columns on an existing one), so no column-
-- migration machinery is needed: CREATE TABLE IF NOT EXISTS is itself
-- additive for an existing database.
CREATE TABLE IF NOT EXISTS stage0 (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    skipped INTEGER NOT NULL DEFAULT 0,
    problem_type_json TEXT,
    interview_json TEXT,
    framing_analysis_a_json TEXT,
    framing_analysis_b_json TEXT,
    final_question TEXT,
    success_criteria TEXT,
    locked_at TEXT,
    estimated_cost_usd REAL NOT NULL DEFAULT 0.0
);
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
    "language_contract_status": "TEXT",
    "observed_language": "TEXT",
    "language_recovery_attempted": "INTEGER NOT NULL DEFAULT 0",
}

# A run created before bilingual support existed gets "en" -- a stated,
# backward-compatible default, never inferred from its stored content.
_RUN_MIGRATION_COLUMNS: dict[str, str] = {
    "language": "TEXT NOT NULL DEFAULT 'en'",
    # Nullable, no default value beyond SQL NULL -- see RunRecord.max_run_cost_usd.
    "max_run_cost_usd": "TEXT",
    # Nullable, no default -- see RunRecord.working_language. NULL (not "en")
    # for every pre-existing row, since this feature did not exist for them.
    "working_language": "TEXT",
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
        # Wait up to 5s for a lock instead of raising "database is locked"
        # immediately -- protects against a second process/tool briefly
        # holding the file (e.g. a backup, or a `sqlite3` CLI session) even
        # though normal application writes never contend with each other
        # (every stage/run write in service._execute happens sequentially
        # in one coroutine -- see AUDIT_REPORT.md Section E). Does not
        # change that serialized write architecture; purely a safety net
        # for external contention.
        self._conn.execute("PRAGMA busy_timeout = 5000")
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
        working_language: str | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO runs "
            "(id, question, context, profile, red_team_enabled, status, "
            "created_at, estimated_total_cost_usd, language, max_run_cost_usd, "
            "working_language) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, 0.0, ?, ?, ?)",
            (
                run_id,
                question,
                context,
                profile,
                int(red_team_enabled),
                utc_now_iso(),
                language,
                max_run_cost_usd,
                working_language,
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
        run.stage0 = self.get_stage0(run_id)
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
            working_language=row["working_language"],
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
            language_contract_status=row["language_contract_status"],
            observed_language=row["observed_language"],
            language_recovery_attempted=bool(row["language_recovery_attempted"]),
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
        language_contract_status: str | None = None,
        observed_language: str | None = None,
        language_recovery_attempted: bool = False,
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
            "language_contract_status = ?, observed_language = ?, "
            "language_recovery_attempted = ?, "
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
                language_contract_status,
                observed_language,
                int(language_recovery_attempted),
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
        language_contract_status: str | None = None,
        observed_language: str | None = None,
        language_recovery_attempted: bool = False,
    ) -> None:
        # estimated_cost_usd accumulates -- see the matching note in
        # mark_stage_succeeded. A stage retried multiple times, each
        # incurring some paid-but-unusable cost before finally succeeding or
        # being abandoned, keeps every attempt's spend in the run total.
        self._conn.execute(
            "UPDATE stages SET status = 'failed', error = ?, completed_at = ?, "
            "requested_model = ?, fallback_used = ?, fallback_reason = ?, "
            "model_attempts = ?, attempt_log = ?, "
            "estimated_cost_usd = estimated_cost_usd + ?, failure_reason = ?, "
            "language_contract_status = ?, observed_language = ?, "
            "language_recovery_attempted = ? "
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
                language_contract_status,
                observed_language,
                int(language_recovery_attempted),
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

    # -- stage0 (question framing, see ADR 009) -----------------------

    def insert_stage0(self, run_id: str) -> None:
        self._conn.execute(
            "INSERT INTO stage0 (run_id, created_at, skipped, estimated_cost_usd) "
            "VALUES (?, ?, 0, 0.0)",
            (run_id, utc_now_iso()),
        )
        self._conn.commit()

    def get_stage0(self, run_id: str) -> Stage0Record | None:
        row = self._conn.execute(
            "SELECT * FROM stage0 WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return self._stage0_from_row(row)

    @staticmethod
    def _stage0_from_row(row: sqlite3.Row) -> Stage0Record:
        return Stage0Record(
            run_id=row["run_id"],
            created_at=row["created_at"],
            skipped=bool(row["skipped"]),
            problem_type_json=row["problem_type_json"],
            interview_json=row["interview_json"],
            framing_analysis_a_json=row["framing_analysis_a_json"],
            framing_analysis_b_json=row["framing_analysis_b_json"],
            final_question=row["final_question"],
            success_criteria=row["success_criteria"],
            locked_at=row["locked_at"],
            estimated_cost_usd=row["estimated_cost_usd"],
        )

    def update_stage0_problem_framing(
        self, run_id: str, *, problem_type_json: str, estimated_cost_usd: float
    ) -> None:
        """Persists the combined 0a+0b call's result (classification,
        reason, and the full list of interview questions -- see ADR 009's
        call-count trade-off: the interview is generated all at once, not
        adaptively). Cost accumulates, matching mark_stage_succeeded's own
        "never drop already-incurred cost" rule."""
        self._conn.execute(
            "UPDATE stage0 SET problem_type_json = ?, "
            "estimated_cost_usd = estimated_cost_usd + ? WHERE run_id = ?",
            (problem_type_json, estimated_cost_usd, run_id),
        )
        self._conn.commit()

    def update_stage0_interview(self, run_id: str, *, interview_json: str) -> None:
        """Persists the user's answers alongside the questions (see
        service.record_interview_answers) -- no cost of its own, the user
        typing an answer is not a provider call."""
        self._conn.execute(
            "UPDATE stage0 SET interview_json = ? WHERE run_id = ?",
            (interview_json, run_id),
        )
        self._conn.commit()

    def update_stage0_framing_analysis(
        self, run_id: str, *, candidate: str, analysis_json: str, estimated_cost_usd: float
    ) -> None:
        if candidate not in ("a", "b"):
            raise ValueError(f"candidate must be 'a' or 'b', got {candidate!r}")
        column = "framing_analysis_a_json" if candidate == "a" else "framing_analysis_b_json"
        self._conn.execute(
            f"UPDATE stage0 SET {column} = ?, "
            "estimated_cost_usd = estimated_cost_usd + ? WHERE run_id = ?",
            (analysis_json, estimated_cost_usd, run_id),
        )
        self._conn.commit()

    def record_stage0_cost(self, run_id: str, estimated_cost_usd: float) -> None:
        """Accumulates cost with no other column change -- used when a
        Stage 0 call fails (budget-blocked, or a paid call that failed
        schema validation) so the already-incurred cost is still counted,
        never silently dropped (see mark_stage_failed's matching rule)."""
        self._conn.execute(
            "UPDATE stage0 SET estimated_cost_usd = estimated_cost_usd + ? WHERE run_id = ?",
            (estimated_cost_usd, run_id),
        )
        self._conn.commit()

    def lock_stage0(
        self,
        run_id: str,
        *,
        final_question: str,
        success_criteria: str | None,
        skipped: bool = False,
    ) -> None:
        """The one commit marker for Stage 0 (see service.lock_question_framing).
        "Skip Stage 0" is a degenerate lock: final_question is set to the
        user's original question and skipped=True, so downstream code never
        needs to branch on skipped vs. completed -- it only ever asks
        "is locked_at set, and what is final_question"."""
        self._conn.execute(
            "UPDATE stage0 SET final_question = ?, success_criteria = ?, "
            "skipped = ?, locked_at = ? WHERE run_id = ?",
            (final_question, success_criteria, int(skipped), utc_now_iso(), run_id),
        )
        self._conn.commit()

    def sum_stage0_cost(self, run_id: str) -> float:
        row = self._conn.execute(
            "SELECT estimated_cost_usd FROM stage0 WHERE run_id = ?", (run_id,)
        ).fetchone()
        return float(row["estimated_cost_usd"]) if row is not None else 0.0

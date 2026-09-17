"""Dedicated evaluation-experiment persistence -- deliberately separate
tables, in a separate database file, from the production runs/stages
schema (see store.py). Reasons (see AUDIT_REPORT.md's next-phase report,
Part D item 10): evaluation runs have a different shape (variant axis, case
linkage, human-review metadata) that would force nullable, evaluation-only
columns onto the production schema for no production benefit; and ordinary
production run history must never be overwritten or mixed with evaluation
instrumentation (an explicit requirement).

Never stores API keys. Same additive-migration discipline as store.py.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_EVAL_DB_PATH = Path("data") / "evaluation.db"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_experiment_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass(slots=True)
class EvalStageRow:
    stage_name: str
    provider: str | None
    requested_model: str | None
    actual_model: str | None
    input_tokens: int
    output_tokens: int
    attempt_count: int
    cost_usd: float
    status: str
    truncated: bool
    text: str | None
    error: str | None = None


@dataclass(slots=True)
class EvalExperimentRecord:
    id: str
    case_id: str
    variant: str
    git_commit: str | None
    profile: str
    output_language: str
    working_language: str | None
    configured_openai_model: str | None
    configured_anthropic_model: str | None
    configured_gemini_model: str | None
    created_at: str
    completed_at: str | None
    status: str
    total_calls: int
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    duration_seconds: float | None
    quality_state: str | None
    final_output: str | None
    material_change_count: int | None
    convergence_status: str | None
    max_cost_per_variant_usd: str | None
    production_run_id: str | None
    stages: list[EvalStageRow] = field(default_factory=list)


SCHEMA = """
CREATE TABLE IF NOT EXISTS eval_experiments (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    variant TEXT NOT NULL,
    git_commit TEXT,
    profile TEXT NOT NULL,
    output_language TEXT NOT NULL,
    working_language TEXT,
    configured_openai_model TEXT,
    configured_anthropic_model TEXT,
    configured_gemini_model TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    total_calls INTEGER NOT NULL DEFAULT 0,
    total_input_tokens INTEGER NOT NULL DEFAULT 0,
    total_output_tokens INTEGER NOT NULL DEFAULT 0,
    total_cost_usd REAL NOT NULL DEFAULT 0.0,
    duration_seconds REAL,
    quality_state TEXT,
    final_output TEXT,
    material_change_count INTEGER,
    convergence_status TEXT,
    max_cost_per_variant_usd TEXT,
    production_run_id TEXT
);

CREATE TABLE IF NOT EXISTS eval_stage_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL REFERENCES eval_experiments(id) ON DELETE CASCADE,
    stage_name TEXT NOT NULL,
    provider TEXT,
    requested_model TEXT,
    actual_model TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL,
    truncated INTEGER NOT NULL DEFAULT 0,
    text TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS eval_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id TEXT NOT NULL REFERENCES eval_experiments(id) ON DELETE CASCADE,
    reviewer TEXT,
    created_at TEXT NOT NULL,
    dimension TEXT NOT NULL,
    rating TEXT,
    numeric_rating REAL,
    comment TEXT
);

CREATE INDEX IF NOT EXISTS idx_eval_stage_results_experiment_id ON eval_stage_results(experiment_id);
CREATE INDEX IF NOT EXISTS idx_eval_reviews_experiment_id ON eval_reviews(experiment_id);
CREATE INDEX IF NOT EXISTS idx_eval_experiments_case_id ON eval_experiments(case_id);
"""


class EvaluationRepository:
    def __init__(self, db_path: str | Path = DEFAULT_EVAL_DB_PATH):
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def save_experiment(
        self,
        *,
        case_id: str,
        variant: str,
        git_commit: str | None,
        profile: str,
        output_language: str,
        working_language: str | None,
        configured_openai_model: str | None,
        configured_anthropic_model: str | None,
        configured_gemini_model: str | None,
        status: str,
        stages: list[EvalStageRow],
        final_output: str | None,
        quality_state: str | None,
        material_change_count: int | None,
        convergence_status: str | None,
        duration_seconds: float | None,
        max_cost_per_variant_usd: str | None,
        production_run_id: str | None,
    ) -> str:
        """Persists one evaluation experiment (a case+variant run) with all
        its stage results in one transaction. Returns the new experiment_id.
        Never mutates an existing experiment -- every save is a new,
        immutable historical record."""
        experiment_id = new_experiment_id()
        now = utc_now_iso()
        total_calls = sum(s.attempt_count for s in stages)
        total_input = sum(s.input_tokens for s in stages)
        total_output = sum(s.output_tokens for s in stages)
        total_cost = sum(s.cost_usd for s in stages)
        self._conn.execute(
            "INSERT INTO eval_experiments (id, case_id, variant, git_commit, profile, "
            "output_language, working_language, configured_openai_model, "
            "configured_anthropic_model, configured_gemini_model, created_at, "
            "completed_at, status, total_calls, total_input_tokens, "
            "total_output_tokens, total_cost_usd, duration_seconds, quality_state, "
            "final_output, material_change_count, convergence_status, "
            "max_cost_per_variant_usd, production_run_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                experiment_id, case_id, variant, git_commit, profile, output_language,
                working_language, configured_openai_model, configured_anthropic_model,
                configured_gemini_model, now, now, status, total_calls, total_input,
                total_output, total_cost, duration_seconds, quality_state, final_output,
                material_change_count, convergence_status, max_cost_per_variant_usd,
                production_run_id,
            ),
        )
        for s in stages:
            self._conn.execute(
                "INSERT INTO eval_stage_results (experiment_id, stage_name, provider, "
                "requested_model, actual_model, input_tokens, output_tokens, "
                "attempt_count, cost_usd, status, truncated, text, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    experiment_id, s.stage_name, s.provider, s.requested_model,
                    s.actual_model, s.input_tokens, s.output_tokens, s.attempt_count,
                    s.cost_usd, s.status, int(s.truncated), s.text, s.error,
                ),
            )
        self._conn.commit()
        return experiment_id

    def get_experiment(self, experiment_id: str) -> EvalExperimentRecord:
        row = self._conn.execute(
            "SELECT * FROM eval_experiments WHERE id = ?", (experiment_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown experiment_id: {experiment_id}")
        stage_rows = self._conn.execute(
            "SELECT * FROM eval_stage_results WHERE experiment_id = ? ORDER BY id",
            (experiment_id,),
        ).fetchall()
        return EvalExperimentRecord(
            id=row["id"], case_id=row["case_id"], variant=row["variant"],
            git_commit=row["git_commit"], profile=row["profile"],
            output_language=row["output_language"], working_language=row["working_language"],
            configured_openai_model=row["configured_openai_model"],
            configured_anthropic_model=row["configured_anthropic_model"],
            configured_gemini_model=row["configured_gemini_model"],
            created_at=row["created_at"], completed_at=row["completed_at"],
            status=row["status"], total_calls=row["total_calls"],
            total_input_tokens=row["total_input_tokens"],
            total_output_tokens=row["total_output_tokens"],
            total_cost_usd=row["total_cost_usd"], duration_seconds=row["duration_seconds"],
            quality_state=row["quality_state"], final_output=row["final_output"],
            material_change_count=row["material_change_count"],
            convergence_status=row["convergence_status"],
            max_cost_per_variant_usd=row["max_cost_per_variant_usd"],
            production_run_id=row["production_run_id"],
            stages=[
                EvalStageRow(
                    stage_name=r["stage_name"], provider=r["provider"],
                    requested_model=r["requested_model"], actual_model=r["actual_model"],
                    input_tokens=r["input_tokens"], output_tokens=r["output_tokens"],
                    attempt_count=r["attempt_count"], cost_usd=r["cost_usd"],
                    status=r["status"], truncated=bool(r["truncated"]), text=r["text"],
                    error=r["error"],
                )
                for r in stage_rows
            ],
        )

    def list_experiments(self, *, case_id: str | None = None) -> list[EvalExperimentRecord]:
        if case_id is not None:
            rows = self._conn.execute(
                "SELECT id FROM eval_experiments WHERE case_id = ? ORDER BY created_at", (case_id,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT id FROM eval_experiments ORDER BY created_at").fetchall()
        return [self.get_experiment(r["id"]) for r in rows]

    def save_review(
        self,
        *,
        experiment_id: str,
        reviewer: str | None,
        dimension: str,
        rating: str | None,
        numeric_rating: float | None,
        comment: str | None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO eval_reviews (experiment_id, reviewer, created_at, dimension, "
            "rating, numeric_rating, comment) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (experiment_id, reviewer, utc_now_iso(), dimension, rating, numeric_rating, comment),
        )
        self._conn.commit()

    def list_reviews(self, experiment_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM eval_reviews WHERE experiment_id = ? ORDER BY created_at", (experiment_id,)
        ).fetchall()
        return [dict(r) for r in rows]

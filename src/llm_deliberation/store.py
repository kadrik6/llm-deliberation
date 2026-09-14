from __future__ import annotations

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
    estimated_total_cost_usd REAL NOT NULL DEFAULT 0.0
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
        self._conn.commit()

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
    ) -> None:
        self._conn.execute(
            "INSERT INTO runs "
            "(id, question, context, profile, red_team_enabled, status, "
            "created_at, estimated_total_cost_usd) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, 0.0)",
            (run_id, question, context, profile, int(red_team_enabled), utc_now_iso()),
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
    ) -> None:
        now = utc_now_iso()
        row = self._conn.execute(
            "SELECT run_id FROM stages WHERE id = ?", (stage_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown stage_id: {stage_id}")
        run_id = row["run_id"]
        self._conn.execute(
            "UPDATE stages SET status = 'succeeded', provider = ?, model = ?, "
            "input_tokens = ?, output_tokens = ?, estimated_cost_usd = ?, "
            "error = NULL, completed_at = ? WHERE id = ?",
            (provider, model, input_tokens, output_tokens, estimated_cost_usd, now, stage_id),
        )
        self._conn.execute(
            "INSERT INTO artifacts (run_id, stage_id, artifact_type, text_content, created_at) "
            "VALUES (?, ?, 'response_text', ?, ?)",
            (run_id, stage_id, text, now),
        )
        self._conn.commit()

    def mark_stage_failed(self, stage_id: int, *, error: str) -> None:
        self._conn.execute(
            "UPDATE stages SET status = 'failed', error = ?, completed_at = ? WHERE id = ?",
            (error, utc_now_iso(), stage_id),
        )
        self._conn.commit()

    def reset_stage(self, stage_id: int) -> None:
        self._conn.execute(
            "UPDATE stages SET status = 'pending', error = NULL, started_at = NULL, "
            "completed_at = NULL WHERE id = ?",
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

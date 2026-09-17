"""Deterministic blind-review export (Part B, Section 16). Reduces
confirmation bias: a reviewer sees only the final outputs, labeled A/B/C/...
in an order derived from a seed -- never the variant name, model names,
stage counts, or costs. The label->variant mapping is a SEPARATE artifact,
never bundled with the review artifact.
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass

from llm_deliberation.evaluation.cases import EvalCase
from llm_deliberation.evaluation.store import EvalExperimentRecord

# Every human-review dimension the rubric (Section 15) asks for. Categorical
# and free-text comments are always allowed; a numeric rating is optional
# (see store.save_review's `rating`/`numeric_rating`/`comment` columns).
# Deliberately does NOT include anything like "overall winner" -- Section 15
# explicitly forbids collapsing this into one opaque score, and Section 18
# forbids declaring a winner at all.
RUBRIC_DIMENSIONS: tuple[str, ...] = (
    "usefulness",
    "important_constraint_coverage",
    "correctness_factual_fidelity",
    "unsupported_claims",
    "missing_alternatives",
    "useful_blind_spots_surfaced",
    "final_deliverable_quality",
    "unresolved_uncertainty_handling",
    "unnecessary_verbosity",
    "completion_or_truncation",
    "material_improvement_vs_simpler_variant",
)


@dataclass(slots=True)
class BlindExport:
    case_id: str
    seed: int
    review_markdown: str  # PUBLIC -- give this to the reviewer
    mapping: dict[str, str]  # PRIVATE (label -> variant) -- keep separate


def build_blind_export(
    case: EvalCase, experiments: list[EvalExperimentRecord], *, seed: int
) -> BlindExport:
    """One experiment per variant expected (callers should pre-filter to the
    experiments they actually want reviewed -- e.g. the latest run of each
    variant for this case). Labels are assigned by shuffling A, B, C, ...
    with `random.Random(seed)` against a *stable* (variant-name-sorted)
    experiment order -- so the same seed always reproduces the same mapping
    (for audit), while different seeds give different, non-obvious
    label orders.
    """
    if len(experiments) > len(string.ascii_uppercase):
        raise ValueError("Too many experiments for a single-letter label per variant.")
    canonical = sorted(experiments, key=lambda e: e.variant)
    labels = list(string.ascii_uppercase[: len(canonical)])
    rng = random.Random(seed)
    shuffled_labels = labels[:]
    rng.shuffle(shuffled_labels)

    mapping = {label: exp.variant for label, exp in zip(shuffled_labels, canonical)}

    lines = [f"Case: {case.case_id}", "", case.question]
    if case.context:
        lines += ["", case.context]
    lines.append("")
    for label, exp in zip(shuffled_labels, canonical):
        lines.append(f"Output {label}")
        lines.append("")
        lines.append(exp.final_output or "(no output produced)")
        lines.append("")
    review_markdown = "\n".join(lines)

    return BlindExport(case_id=case.case_id, seed=seed, review_markdown=review_markdown, mapping=mapping)


def write_blind_export(export: BlindExport, *, review_path, mapping_path) -> None:
    """Writes the review artifact and the private mapping to two SEPARATE
    files -- never combined into one, so the review artifact can be handed
    to a reviewer without exposing the mapping."""
    import json
    from pathlib import Path

    Path(review_path).write_text(export.review_markdown)
    Path(mapping_path).write_text(
        json.dumps({"case_id": export.case_id, "seed": export.seed, "mapping": export.mapping}, indent=2)
    )

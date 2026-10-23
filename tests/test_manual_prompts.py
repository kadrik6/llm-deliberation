"""Offline tests for manual_prompts: the paste-ready script for a manual run
in chat apps. Pure text generation, no provider calls."""
from __future__ import annotations

import pytest

from llm_deliberation import manual_prompts, prompts


def test_script_contains_every_stage_built_from_the_real_prompt_builders():
    script = manual_prompts.build_script("Should we do X?")
    assert prompts.problem_framing_and_interview("Should we do X?", None) in script
    assert prompts.independent_analysis("Should we do X?") in script
    assert prompts.critique("Should we do X?", "<<PASTE B1 HERE>>") in script
    assert prompts.revision("Should we do X?", "<<PASTE A1 HERE>>", "<<PASTE KB HERE>>", None) in script
    assert prompts.synthesis("Should we do X?", "<<PASTE A2 HERE>>", "<<PASTE B2 HERE>>", None) in script


def test_each_model_revises_against_the_other_models_critique():
    script = manual_prompts.build_script("Q?")
    step_3a = script.split("## 3a.")[1].split("## 3b.")[0]
    step_3b = script.split("## 3b.")[1].split("## 4.")[0]
    assert "<<PASTE A1 HERE>>" in step_3a and "<<PASTE KB HERE>>" in step_3a
    assert "<<PASTE B1 HERE>>" in step_3b and "<<PASTE KA HERE>>" in step_3b


def test_estonian_script_uses_the_estonian_system_text():
    script = manual_prompts.build_script("Kas teha X?", language="et")
    assert prompts.base_system("et", "et") in script


def test_context_is_included_in_every_stage_question():
    script = manual_prompts.build_script("Q?", context="Official forecast: 3%")
    # Stage 0 plus the seven deliberation steps.
    assert script.count("Official forecast: 3%") == 8


def test_unknown_language_is_rejected():
    with pytest.raises(ValueError):
        manual_prompts.build_script("Q?", language="fi")


def test_main_writes_the_file(tmp_path):
    out = tmp_path / "script.md"
    assert manual_prompts.main(["Q?", "--language", "et", "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("# Manual deliberation run")


def test_stages_ask_for_a_ranked_sized_answer_not_a_checklist():
    # A blind eval on 22.10.2026 scored the deliberated answers below a
    # single model because they refused to rank or size the options.
    analysis = prompts.independent_analysis("Q?")
    critique = prompts.critique("Q?", "A")
    revision = prompts.revision("Q?", "A", "K", None)
    synthesis = prompts.synthesis("Q?", "A2", "B2", None)
    assert "Declining to rank is not caution" in analysis
    assert "do not ask for it to be removed" in critique
    assert "Do not\ndrop a sized estimate" in revision
    assert "Refusing to rank is not caution" in synthesis
    assert "Rank options only where" not in synthesis


def test_verify_script_answers_checks_then_corrects():
    script = manual_prompts.build_verify_script("Q?")
    assert prompts.verified_answer("Q?") in script
    assert prompts.claim_check("Q?", "<<PASTE V1 HERE>>") in script
    assert prompts.apply_verified_corrections("Q?", "<<PASTE V1 HERE>>", "<<PASTE K HERE>>") in script
    assert script.startswith("# Manual verify run")


def test_checker_needs_a_source_to_call_a_claim_wrong():
    check = prompts.claim_check("Q?", "A")
    assert "only with a source you can name" in check
    assert "Do not write your own" in check


def test_main_writes_the_verify_script(tmp_path):
    out = tmp_path / "verify.md"
    assert manual_prompts.main(["Q?", "--pattern", "verify", "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("# Manual verify run")


def test_synthesis_keeps_the_better_based_estimate_instead_of_merging_ranges():
    # The 23.10.2026 eval: merging two answers' ranges into one wide range
    # made the synthesis less coherent than a single model's answer.
    synthesis = prompts.synthesis("Q?", "A2", "B2", None)
    assert "Never merge different estimates" in synthesis
    assert "show the range rather than choosing one" not in synthesis

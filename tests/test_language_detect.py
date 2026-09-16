"""Offline unit tests for language_detect.py -- the local, deterministic
detector behind the working-language contract check (see orchestrator.py's
language-contract block, AUDIT_REPORT.md's live-canary findings).

No network calls, no LLM calls: this module is pure stdlib regex/arithmetic,
verified here by a source-level check as well as behaviorally.
"""

from __future__ import annotations

import ast
import inspect

from llm_deliberation import language_detect
from llm_deliberation.language_detect import classify_language_contract, detect_language

_REAL_ENGLISH_PROSE = (
    "The committee reviewed the proposal carefully and concluded that the "
    "budget assumptions were reasonable, although the timeline should be "
    "extended by two weeks to accommodate the vendor's onboarding process. "
    "This recommendation reflects the strongest available evidence and "
    "should be revisited if new information becomes available before the "
    "next quarterly review."
)

_REAL_ESTONIAN_PROSE = (
    "Komisjon vaatas ettepaneku hoolikalt läbi ja jõudis järeldusele, et "
    "eelarve eeldused olid mõistlikud, kuigi ajakava tuleks pikendada kahe "
    "nädala võrra, et võtta arvesse tarnija sisseelamisprotsessi. See "
    "soovitus kajastab parimaid olemasolevaid tõendeid ja seda tuleks "
    "uuesti kaaluda, kui enne järgmist kvartaliülevaatust ilmneb uut teavet."
)


def test_detects_clear_english_prose():
    lang, _ = detect_language(_REAL_ENGLISH_PROSE)
    assert lang == "en"


def test_detects_clear_estonian_prose():
    lang, _ = detect_language(_REAL_ESTONIAN_PROSE)
    assert lang == "et"


def test_quoted_estonian_phrase_inside_english_prose_does_not_falsely_flip():
    text = (
        _REAL_ENGLISH_PROSE
        + ' One reviewer noted, in the original language, "see on hea otsus," '
        "but the English-language analysis above remains the operative "
        "recommendation for this working stage."
    )
    lang, _ = detect_language(text)
    assert lang == "en"


def test_code_heavy_output_becomes_uncertain():
    text = "```json\n" + ('{"key": "value", "n": 1}\n' * 30) + "```"
    lang, evidence = detect_language(text)
    assert lang is None
    assert evidence.code_fraction >= 0.5


def test_very_short_output_becomes_uncertain():
    lang, evidence = detect_language("OK, agreed.")
    assert lang is None
    assert evidence.word_count < language_detect.MIN_WORDS_FOR_CONFIDENCE


def test_near_balanced_mixed_text_becomes_uncertain():
    # Half English function words, half Estonian function words (none
    # diacritic-bearing, to isolate the stopword-fraction tie from the
    # separate diacritic-ratio signal), deliberately constructed so neither
    # side reaches the required margin.
    mixed = " ".join(
        ["the", "and", "is", "are", "was", "were", "will", "would", "should"] * 4
        + ["ja", "ei", "et", "kui", "ning", "aga", "siis", "selle", "seda"] * 4
    )
    lang, _ = detect_language(mixed)
    assert lang is None


def test_empty_text_is_uncertain():
    lang, evidence = detect_language("")
    assert lang is None
    assert evidence.word_count == 0


def test_classify_language_contract_matched():
    status, detected, _ = classify_language_contract(_REAL_ENGLISH_PROSE, expected_language="en")
    assert status == "matched"
    assert detected == "en"


def test_classify_language_contract_mismatched():
    status, detected, _ = classify_language_contract(_REAL_ESTONIAN_PROSE, expected_language="en")
    assert status == "mismatched"
    assert detected == "et"


def test_classify_language_contract_uncertain_for_short_text():
    status, detected, _ = classify_language_contract("OK.", expected_language="en")
    assert status == "uncertain"
    assert detected is None


def test_no_llm_or_provider_sdk_is_imported_by_the_detector():
    """The detector must never call an LLM to detect language (explicit
    project requirement) -- verified at the source level, not just by
    absence of a mock, so a future edit that quietly adds an SDK import
    would fail this test immediately."""
    source = inspect.getsource(language_detect)
    tree = ast.parse(source)
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module.split(".")[0])
    forbidden = {"openai", "anthropic", "google"}
    assert not (imported_names & forbidden), f"detector imports a provider SDK: {imported_names & forbidden}"

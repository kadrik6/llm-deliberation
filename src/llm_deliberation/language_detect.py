"""Local, deterministic language detection for the working-language contract
check (see orchestrator.run_stage, AUDIT_REPORT.md's live-canary findings).

Why not an LLM or an off-the-shelf NLP library: a real live canary showed
gpt-5.6-terra silently answering in Estonian on an English-working-language
stage despite an explicit, verified-correct instruction -- the application
had no way to know this happened short of a human reading the artifact. An
LLM-based checker would add cost, latency, and its own compliance risk to a
check whose entire point is to be a cheap, reliable safety net. A general
NLP language-ID library was considered and rejected: this project only ever
needs to distinguish two languages (SUPPORTED_LANGUAGES: "en" | "et"), and a
general-purpose detector is not obviously *better* at the specific failure
modes that matter here (a short quoted phrase in the other language, a
code/JSON-heavy artifact, a very short response) than a small, transparent,
purpose-built heuristic -- and it would add a new dependency for a narrowly
scoped need. No dependency was added; this module is pure stdlib.

Method: closed-class stopword-fraction matching (deliberately using only
words that are unambiguous to one language -- e.g. Estonian "on"/"see" are
excluded because they are also common English words) plus Estonian-exclusive
diacritic density (õäöüšž essentially never appear in English prose),
computed over code/JSON-stripped text. Returns "uncertain" whenever evidence
is thin, mixed, or ambiguous -- a false "mismatched" verdict is worse than an
honest "uncertain" one (see classify_language_contract's docstring).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Closed-class function words that are effectively exclusive to one language
# -- deliberately excludes short words that are valid in both (e.g. Estonian
# "on" is also an English word; Estonian "see" is also an English verb) so a
# single incidental match can never look like signal.
_ET_STOPWORDS: frozenset[str] = frozenset(
    {
        "ja", "ei", "et", "kui", "või", "ning", "aga", "siis", "selle", "seda",
        "need", "oma", "nagu", "kõik", "veel", "juba", "ainult", "mida", "kes",
        "mis", "miks", "kus", "palun", "tänan", "väga", "peaks", "saab", "tuleb",
        "tuleks", "jaoks", "ilma", "koos", "üle", "alla", "enne", "pärast",
        "vahel", "samas", "seega", "kuna", "ehkki", "kuigi", "ometi", "samuti",
        "näiteks", "seetõttu", "seepärast", "mistõttu", "vastavalt",
        "arvestades", "hetkel", "hiljem", "varem", "kohe", "kuidas", "kellel",
        "kellele", "endale", "tegelikult", "tänu", "seoses", "mõlemad",
        "praegu", "praegune", "järgmine", "eelmine", "olulisem", "vähemalt",
    }
)

_EN_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "and", "is", "are", "was", "were", "will", "would", "should",
        "could", "can", "not", "but", "however", "therefore", "because",
        "although", "though", "while", "when", "where", "which", "who", "whom",
        "whose", "this", "these", "those", "with", "without", "within",
        "between", "before", "after", "since", "until", "than", "then", "also",
        "only", "still", "already", "please", "thank", "very", "most", "more",
        "less", "least", "each", "every", "both", "either", "neither", "some",
        "any", "all", "none", "such", "same", "other", "another", "about",
        "above", "below", "into", "onto", "from", "over", "under", "across",
    }
)

# Letters that are effectively exclusive to Estonian in ordinary prose (never
# appear in English text; õ in particular is a strong, near-unambiguous
# signal). Case-insensitive.
_ET_DIACRITIC_CHARS: frozenset[str] = frozenset("õäöüšž")

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")

# Below this many qualifying words, there is not enough evidence to classify
# confidently -- see classify_language_contract's "uncertain is safer than a
# false positive" rule.
MIN_WORDS_FOR_CONFIDENCE = 25

# A stopword-fraction margin: the leading language's fraction must exceed the
# other's by at least this multiplier, not just be numerically larger, to
# rule out near-balanced/mixed text.
_MARGIN_MULTIPLIER = 1.5
_MIN_LEADING_FRACTION = 0.03
_STRONG_DIACRITIC_RATIO = 0.008
_CODE_HEAVY_FRACTION = 0.5


def _strip_code(text: str) -> tuple[str, float]:
    """Removes fenced and inline code spans; returns (stripped_text,
    fraction_of_original_length_that_was_code) so callers can treat a
    heavily code/JSON-laden artifact as low-confidence prose evidence."""
    stripped = _FENCED_CODE_RE.sub(" ", text)
    stripped = _INLINE_CODE_RE.sub(" ", stripped)
    if not text:
        return stripped, 0.0
    code_fraction = max(0.0, (len(text) - len(stripped)) / len(text))
    return stripped, code_fraction


@dataclass(slots=True)
class DetectionEvidence:
    """Raw signal behind a detect_language() call -- kept only for
    diagnostics/attempt_log provenance, never used for control flow beyond
    what detect_language() itself already decided."""

    word_count: int
    code_fraction: float
    et_fraction: float
    en_fraction: float
    diacritic_ratio: float


def detect_language(text: str) -> tuple[str | None, DetectionEvidence]:
    """Best-effort local detection of "en" vs "et" in `text`.

    Returns (language, evidence): language is "en", "et", or None
    ("uncertain" -- either too little evidence, or the signal does not
    clearly favor one language). Never raises; never claims certainty for
    short, code-heavy, or near-balanced/mixed text -- see the module
    docstring and MIN_WORDS_FOR_CONFIDENCE/_MARGIN_MULTIPLIER.
    """
    stripped, code_fraction = _strip_code(text)
    words = _WORD_RE.findall(stripped)
    word_count = len(words)

    if word_count == 0:
        evidence = DetectionEvidence(0, code_fraction, 0.0, 0.0, 0.0)
        return None, evidence

    alpha_chars = sum(len(w) for w in words)
    diacritic_count = sum(1 for ch in stripped.lower() if ch in _ET_DIACRITIC_CHARS)
    diacritic_ratio = diacritic_count / alpha_chars if alpha_chars else 0.0

    lowered = [w.lower() for w in words]
    et_hits = sum(1 for w in lowered if w in _ET_STOPWORDS)
    en_hits = sum(1 for w in lowered if w in _EN_STOPWORDS)
    et_fraction = et_hits / word_count
    en_fraction = en_hits / word_count

    evidence = DetectionEvidence(word_count, code_fraction, et_fraction, en_fraction, diacritic_ratio)

    if word_count < MIN_WORDS_FOR_CONFIDENCE:
        return None, evidence
    if code_fraction >= _CODE_HEAVY_FRACTION:
        return None, evidence

    # A strong Estonian-exclusive diacritic signal is decisive on its own --
    # English prose essentially never contains õ/ä/ö/ü/š/ž at this density.
    if diacritic_ratio >= _STRONG_DIACRITIC_RATIO and et_fraction >= en_fraction:
        return "et", evidence

    if et_fraction >= _MIN_LEADING_FRACTION and et_fraction > en_fraction * _MARGIN_MULTIPLIER:
        return "et", evidence
    if en_fraction >= _MIN_LEADING_FRACTION and en_fraction > et_fraction * _MARGIN_MULTIPLIER and diacritic_ratio < _STRONG_DIACRITIC_RATIO:
        return "en", evidence
    return None, evidence


def classify_language_contract(
    text: str, *, expected_language: str
) -> tuple[str, str | None, DetectionEvidence]:
    """Compares a stage's actual output against the language it was asked to
    use. Returns (status, detected_language, evidence):

    - "matched": confidently detected, and it is `expected_language`.
    - "mismatched": confidently detected, and it is NOT `expected_language`.
    - "uncertain": detect_language() could not classify confidently (too
      short, code-heavy, or mixed/ambiguous) -- never treated as a violation,
      per the "false positive is worse than uncertain" rule (see orchestrator
      .run_stage: only "mismatched" ever triggers a corrective recovery
      attempt).
    """
    detected, evidence = detect_language(text)
    if detected is None:
        return "uncertain", None, evidence
    if detected == expected_language:
        return "matched", detected, evidence
    return "mismatched", detected, evidence

"""Lightweight UI internationalization: English + Estonian interface labels.

This is UI-chrome translation only -- it has nothing to do with a run's
output language (see prompts.SUPPORTED_LANGUAGES / store.RunRecord.language).
The two are deliberately unrelated concepts that happen to share the same
two-letter codes:

- `ui_lang` here controls which language *labels like "New", "History",
  "Final answer"* are shown in, for the current browser session only
  (a cookie, see web/app.py's /ui-language route). It never touches
  stored data.
- A run's `language` controls what language the *model-generated content*
  of that run is written in, is persisted with the run, and is completely
  independent of whoever is currently viewing it.

No separate template files per language, no translated copies of pages --
every template calls the same `{{ "some_key"|t }}` filter (registered in
web/app.py), which looks up TRANSLATIONS[key][ui_lang] with the current
request's UI language already bound to it.

Only visible interface labels live here. Never put model/provider names,
stage identifiers, model IDs, or any persisted/schema value in this file --
those are either technical provenance (never translated) or actual model
output (governed by the run's own language, not this one).
"""

from __future__ import annotations

SUPPORTED_UI_LANGUAGES: tuple[str, ...] = ("en", "et")
DEFAULT_UI_LANGUAGE = "en"

# Native, self-referential names for run/output language options and the UI
# language switcher -- shown the same regardless of the current UI language
# (the conventional "English | Eesti" pattern, not "English | Estonian").
NATIVE_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "et": "Eesti",
}

TRANSLATIONS: dict[str, dict[str, str]] = {
    # -- navigation ----------------------------------------------------
    "nav_new": {"en": "New", "et": "Uus"},
    "nav_history": {"en": "History", "et": "Ajalugu"},
    "brand": {"en": "LLM Deliberation", "et": "LLM Deliberation"},

    # -- new deliberation form ------------------------------------------
    "new_deliberation_title": {"en": "New deliberation", "et": "Uus arutelu"},
    "question_label": {
        "en": "What do you want the models to think through?",
        "et": "Mille üle soovid, et mudelid arutleksid?",
    },
    "question_placeholder": {
        "en": "Describe the question or decision...",
        "et": "Kirjelda küsimust või otsust...",
    },
    "context_summary": {"en": "Add context (optional)", "et": "Lisa kontekst (valikuline)"},
    "context_placeholder": {
        "en": "Extra background, constraints, prior discussion...",
        "et": "Lisataust, piirangud, varasem arutelu...",
    },
    "profile_legend": {"en": "Profile", "et": "Profiil"},
    "profile_economy_blurb": {
        "en": "Fastest/cheapest. Good for routine deliberation.",
        "et": "Kiireim/odavaim. Sobib igapäevaseks aruteluks.",
    },
    "profile_balanced_blurb": {
        "en": "Stronger models for important questions.",
        "et": "Tugevamad mudelid oluliste küsimuste jaoks.",
    },
    "profile_max_blurb": {
        "en": "Highest-quality configuration for difficult decisions.",
        "et": "Parima kvaliteediga seadistus keeruliste otsuste jaoks.",
    },
    "language_legend": {"en": "Language", "et": "Keel"},
    "language_hint": {
        "en": "Controls the language of the models' generated analysis and final "
              "answer, not just this form. Defaults to the interface language, "
              "but you can choose either language for any question.",
        "et": "Määrab mudelite loodava analüüsi ja lõppvastuse keele, mitte ainult "
              "selle vormi keele. Vaikimisi järgib liidese keelt, kuid saad valida "
              "kummagi keele mistahes küsimuse jaoks.",
    },
    "red_team_legend": {"en": "Independent red-team", "et": "Sõltumatu punane meeskond"},
    "red_team_hint": {
        "en": "A third model does not vote on the answer. It looks for assumptions "
              "and blind spots shared by both primary models.",
        "et": "Kolmas mudel ei hääleta vastuse üle. Ta otsib eeldusi ja pimealasid, "
              "mis on ühised mõlemale peamisele mudelile.",
    },
    "on_label": {"en": "On", "et": "Sees"},
    "start_deliberation": {"en": "Start deliberation", "et": "Alusta arutelu"},
    "missing_config_notice": {"en": "Missing configuration", "et": "Puudub seadistus"},
    "missing_config_hint": {
        "en": "Add them to .env before starting a run. Red-team requires "
              "GEMINI_API_KEY only when enabled.",
        "et": "Lisa need faili .env enne arutelu alustamist. Punane meeskond "
              "vajab GEMINI_API_KEY väärtust ainult siis, kui see on sisse lülitatud.",
    },

    # -- run detail: meta row --------------------------------------------
    "deliberation_heading": {"en": "Deliberation", "et": "Arutelu"},
    "question_meta_label": {"en": "Question:", "et": "Küsimus:"},
    "context_meta_label": {"en": "Context:", "et": "Kontekst:"},
    "run_id_label": {"en": "Run ID", "et": "Käigu ID"},
    "profile_meta_label": {"en": "Profile", "et": "Profiil"},
    "language_meta_label": {"en": "Language", "et": "Keel"},
    "red_team_meta_label": {"en": "Red-team", "et": "Punane meeskond"},
    "red_team_on": {"en": "on", "et": "sees"},
    "red_team_off": {"en": "off", "et": "väljas"},
    "elapsed_label": {"en": "Elapsed", "et": "Möödunud aeg"},
    "cost_meta_label": {"en": "Cost", "et": "Maksumus"},

    # -- pipeline (in-progress / failed view) ----------------------------
    "stage_disabled": {"en": "disabled", "et": "pole lubatud"},
    "stage_skipped": {"en": "skipped", "et": "vahele jäetud"},
    "status_label": {"en": "Status", "et": "Olek"},
    "status_pending": {"en": "pending", "et": "ootel"},
    "status_running": {"en": "running", "et": "käib"},
    "status_succeeded": {"en": "succeeded", "et": "õnnestus"},
    "status_failed": {"en": "failed", "et": "ebaõnnestus"},
    "status_skipped": {"en": "skipped", "et": "vahele jäetud"},
    "retry_failed_stage": {"en": "Retry failed stage", "et": "Proovi uuesti"},
    "retry_preferred_model": {
        "en": "Retry preferred model", "et": "Proovi eelistatud mudelit uuesti",
    },
    "retry_with_fallback_chain": {
        "en": "Retry with fallback chain", "et": "Proovi varuahelaga uuesti",
    },
    "skip_and_continue": {"en": "Skip and continue", "et": "Jäta vahele ja jätka"},
    "skip_red_team_and_continue": {
        "en": "Skip red-team and continue", "et": "Jäta punane meeskond vahele ja jätka",
    },
    "skip_convergence_and_continue": {
        "en": "Skip convergence analysis and continue",
        "et": "Jäta konsensuse analüüs vahele ja jätka",
    },
    "resume_run": {"en": "Resume run", "et": "Jätka käiku"},

    # -- final answer / decision snapshot ---------------------------------
    "final_answer_heading": {"en": "Final answer", "et": "Lõppvastus"},
    "decision_snapshot_label": {"en": "Decision Snapshot", "et": "Otsuse ülevaade"},
    "copy_final_answer": {"en": "Copy final answer", "et": "Kopeeri lõppvastus"},
    "copied_label": {"en": "Copied", "et": "Kopeeritud"},
    "export_markdown": {"en": "Export Markdown", "et": "Ekspordi Markdown"},
    "total_cost_label": {"en": "Total cost", "et": "Kogumaksumus"},
    "duration_label": {"en": "Duration", "et": "Kestus"},

    # -- convergence badges ------------------------------------------------
    "convergence_converged": {"en": "Full convergence", "et": "Konsensus saavutatud"},
    "convergence_partial": {"en": "Partial convergence", "et": "Osaline konsensus"},
    "convergence_diverged": {"en": "Diverged", "et": "Seisukohad jäid lahku"},
    "convergence_insufficient_information": {
        "en": "Insufficient information", "et": "Info on ebapiisav",
    },
    "convergence_unavailable_badge": {
        "en": "Convergence analysis unavailable", "et": "Analüüs pole saadaval",
    },
    "convergence_unavailable_message": {
        "en": "Change/convergence analysis unavailable for this run.",
        "et": "Muutuste ja konsensuse analüüs pole selle arutelu jaoks saadaval.",
    },

    # -- decision-evolution sections ---------------------------------------
    "what_changed_heading": {"en": "What changed?", "et": "Mis muutus?"},
    "no_material_changes": {
        "en": "No material position changes were identified.",
        "et": "Sisulisi seisukohamuutusi ei tuvastatud.",
    },
    "candidate_label": {"en": "Candidate", "et": "Kandidaat"},
    "before_label": {"en": "Before", "et": "Enne"},
    "after_label": {"en": "After", "et": "Pärast"},
    "trigger_label": {"en": "Trigger", "et": "Põhjus"},
    "triggers_label": {"en": "Triggers", "et": "Põhjused"},
    "material_change_label": {"en": "Material change", "et": "Sisuline muutus"},
    "yes_label": {"en": "Yes", "et": "Jah"},
    "no_label": {"en": "No", "et": "Ei"},
    "cause_uncertain": {"en": "(cause uncertain)", "et": "(põhjus ebaselge)"},

    "disagreement_map_heading": {
        "en": "Where they still disagree", "et": "Milles nad endiselt ei nõustu?",
    },
    "no_disagreements": {
        "en": "No material disagreements remain.",
        "et": "Olulisi erimeelsusi ei ole enam.",
    },
    "why_unresolved_label": {"en": "Why unresolved", "et": "Miks lahendamata"},
    "decision_impact_label": {"en": "Decision impact", "et": "Mõju otsusele"},

    "agreements_heading": {
        "en": "Agreements reached", "et": "Milles jõuti kokkuleppele?",
    },
    "no_agreements": {
        "en": "No shared agreements were identified.",
        "et": "Ühiseid kokkuleppeid ei tuvastatud.",
    },

    "remaining_unknowns_heading": {
        "en": "What should you find out next?",
        "et": "Mida tasuks järgmisena välja selgitada?",
    },
    "no_unknowns": {
        "en": "No outstanding unknowns were identified.",
        "et": "Lahtisi küsimusi ei tuvastatud.",
    },
    "why_it_matters_label": {"en": "Why it matters", "et": "Miks see on oluline"},
    "evidence_needed_label": {"en": "Evidence needed", "et": "Vajalikud tõendid"},

    "human_judgement_heading": {"en": "You decide", "et": "Sina otsustad"},
    "human_judgement_intro": {
        "en": "More analysis cannot resolve these -- they are values, "
              "risk-appetite, or stakeholder choices only a person can make.",
        "et": "Rohkem analüüsi ei lahenda neid -- need on väärtused, "
              "riskivalmidus või osapoolte valikud, mida saab teha vaid inimene.",
    },
    "no_human_judgement": {
        "en": "No issues were flagged as requiring human judgement.",
        "et": "Inimese otsust vajavaid küsimusi ei tuvastatud.",
    },

    # -- full deliberation trace --------------------------------------------
    "full_trace_heading": {"en": "Full deliberation trace", "et": "Kogu arutelu käik"},
    "artifact_analysis_a": {
        "en": "Independent analysis A", "et": "Sõltumatu analüüs A",
    },
    "artifact_analysis_b": {
        "en": "Independent analysis B", "et": "Sõltumatu analüüs B",
    },
    "artifact_critique_a_of_b": {
        "en": "Cross-critique A → B", "et": "Ristkriitika A → B",
    },
    "artifact_critique_b_of_a": {
        "en": "Cross-critique B → A", "et": "Ristkriitika B → A",
    },
    "artifact_red_team": {
        "en": "Independent red-team", "et": "Sõltumatu punane meeskond",
    },
    "artifact_revision_a": {
        "en": "Revised candidate A", "et": "Täiendatud kandidaat A",
    },
    "artifact_revision_b": {
        "en": "Revised candidate B", "et": "Täiendatud kandidaat B",
    },
    "artifact_convergence_analysis": {
        "en": "Convergence analysis (raw)", "et": "Konsensuse analüüs (toorandmed)",
    },

    # -- stage group titles (pipeline view) ----------------------------------
    "stage_group_analysis": {
        "en": "Independent analysis", "et": "Sõltumatu analüüs",
    },
    "stage_group_critique": {"en": "Cross-critique", "et": "Ristkriitika"},
    "stage_group_red_team": {"en": "Red-team", "et": "Punane meeskond"},
    "stage_group_revision": {"en": "Revision", "et": "Täiendamine"},
    "stage_group_convergence": {
        "en": "Convergence analysis", "et": "Konsensuse analüüs",
    },
    "stage_group_synthesis": {"en": "Final synthesis", "et": "Lõppsüntees"},

    # -- history ------------------------------------------------------------
    "history_heading": {"en": "History", "et": "Ajalugu"},
    "history_created": {"en": "Created", "et": "Loodud"},
    "history_question": {"en": "Question", "et": "Küsimus"},
    "history_profile": {"en": "Profile", "et": "Profiil"},
    "history_red_team": {"en": "Red-team", "et": "Punane meeskond"},
    "history_status": {"en": "Status", "et": "Olek"},
    "history_cost": {"en": "Cost", "et": "Maksumus"},
    "history_language": {"en": "Language", "et": "Keel"},
    "history_empty": {"en": "No runs yet.", "et": "Ühtegi käiku pole veel."},
}


def translate(key: str, lang: str) -> str:
    """Look up one UI label. Falls back to English text, then to the raw
    key itself, so a missing translation degrades visibly rather than
    crashing a page render."""
    entry = TRANSLATIONS.get(key)
    if entry is None:
        return key
    return entry.get(lang) or entry.get(DEFAULT_UI_LANGUAGE) or key


# Fixed singular/plural noun forms for the Decision Snapshot's compact counts
# (see web/app.py's `count_label` filter). Kept separate from TRANSLATIONS
# since these need a count, not just a language, to resolve.
_COUNT_NOUNS: dict[str, dict[str, tuple[str, str]]] = {
    "material_change": {
        "en": ("material change", "material changes"),
        "et": ("sisuline muutus", "sisulist muutust"),
    },
    "unresolved_disagreement": {
        "en": ("unresolved disagreement", "unresolved disagreements"),
        "et": ("lahendamata erimeelsus", "lahendamata erimeelsust"),
    },
    "missing_fact": {
        "en": ("missing fact", "missing facts"),
        "et": ("puuduv fakt", "puuduvat fakti"),
    },
    "human_judgement_item": {
        "en": ("human judgement item", "human judgement items"),
        "et": ("inimotsust vajav punkt", "inimotsust vajavat punkti"),
    },
}


def count_label(key: str, count: int, lang: str) -> str:
    """'{count} {noun}', with a grammatically appropriate singular/plural
    noun form for the given UI language -- not just an English "+s"."""
    forms = _COUNT_NOUNS[key].get(lang) or _COUNT_NOUNS[key][DEFAULT_UI_LANGUAGE]
    singular, plural = forms
    noun = singular if count == 1 else plural
    return f"{count} {noun}"

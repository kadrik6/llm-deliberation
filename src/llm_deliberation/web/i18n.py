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
    "question_helper_text": {
        "en": "Keep this focused on what you want the system to decide or "
              "produce. Move background, constraints, and supporting "
              "details to Context.",
        "et": "Hoia siin fookus sellel, mida soovid süsteemil otsustada või "
              "koostada. Taust, piirangud ja toetavad detailid lisa "
              "konteksti väljale.",
    },
    # Shown once the Question field passes RECOMMENDED_QUESTION_LENGTH's
    # QUESTION_LENGTH_WARNING_THRESHOLD (soft) or RECOMMENDED_QUESTION_LENGTH
    # itself (stronger) -- see service.py. Both are non-blocking UX nudges,
    # never a submission block; the hard cap has its own translated error
    # (question_too_long_error, below).
    "question_warning_soft": {
        "en": "This question is getting long. Consider moving background "
              "information and constraints to Context.",
        "et": "Küsimus muutub üsna pikaks. Kaalu taustainfo ja piirangute "
              "tõstmist konteksti väljale.",
    },
    "question_warning_hard": {
        "en": "Your question is longer than the recommended 2000 "
              "characters. For clearer deliberation, keep the task itself "
              "here and move supporting detail to Context.",
        "et": "Küsimus on pikem kui soovituslik 2000 tähemärki. Selgema "
              "arutelu jaoks jäta siia ülesanne ise ning tõsta toetav taust "
              "konteksti väljale.",
    },
    # Server-side validation error (service.QuestionTooLongError) when
    # `question` exceeds MAX_QUESTION_LENGTH -- the authoritative check;
    # HTML maxlength is only a browser-side convenience, never trusted alone.
    "question_too_long_error": {
        "en": "Question is too long. Keep the task itself under 4000 "
              "characters and move background information to Context.",
        "et": "Küsimus on liiga pikk. Hoia ülesanne alla 4000 tähemärgi ja "
              "tõsta taustainfo konteksti väljale.",
    },
    "context_summary": {"en": "Add context (optional)", "et": "Lisa kontekst (valikuline)"},
    "context_placeholder": {
        "en": "Extra background, constraints, prior discussion...",
        "et": "Lisataust, piirangud, varasem arutelu...",
    },
    "context_helper_text": {
        "en": "Use this for background, facts, constraints, examples, and "
              "other details the models should consider.",
        "et": "Lisa siia taust, faktid, piirangud, näited ja muud detailid, "
              "millega mudelid peaksid arvestama.",
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
    # Display names for the `profile` enum ("economy" | "balanced" | "max"),
    # used anywhere a run's profile is shown as ordinary UI text (the new-run
    # picker, run metadata, history table). Distinct from
    # profile_*_blurb above (the descriptive sentence) and from the raw
    # `profile` value itself, which is never displayed directly -- see the
    # bilingual UI audit ("stored: profile='balanced' -> Estonian display:
    # 'Tasakaalustatud'").
    "profile_name_economy": {"en": "Economy", "et": "Säästlik"},
    "profile_name_balanced": {"en": "Balanced", "et": "Tasakaalustatud"},
    "profile_name_max": {"en": "Max", "et": "Maksimaalne"},
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
    # -- run detail: compact Question/Context preview (see
    # presenter.input_preview) -- never a summary of the content, just a
    # native <details> disclosure over the exact stored text.
    "show_full_question": {"en": "Show full question", "et": "Näita kogu küsimust"},
    "show_context_button": {"en": "Show context", "et": "Näita konteksti"},
    "run_id_label": {"en": "Run ID", "et": "Käigu ID"},
    "profile_meta_label": {"en": "Profile", "et": "Profiil"},
    "language_meta_label": {"en": "Language", "et": "Keel"},
    "red_team_meta_label": {"en": "Red-team", "et": "Punane meeskond"},
    "red_team_on": {"en": "on", "et": "sees"},
    "red_team_off": {"en": "off", "et": "väljas"},
    # Shown on the final results page (see presenter.red_team_skipped_for_
    # budget_reserve) when red-team was configured on but automatically
    # skipped to protect the budget needed to complete the run -- distinct
    # from the plain "off" label above, which means the user never enabled
    # it in the first place.
    "red_team_skipped_for_budget_notice": {
        "en": "Red-team was skipped to preserve enough budget to complete the final result.",
        "et": "Punase meeskonna etapp jäeti vahele, et säilitada piisav eelarve lõpptulemuse valmimiseks.",
    },

    # -- Decision Cockpit Phase 1: truthful red-team status component -----
    # (see presenter.red_team_state). Each key names one of 7 semantically
    # distinct states -- never collapsed into a single on/off/failed label.
    # "skipped_for_budget" deliberately reuses red_team_skipped_for_budget_
    # notice above rather than a near-duplicate string, so the exact
    # sentence tests assert on stays a single source of truth.
    "red_team_status_label": {"en": "Red-team status", "et": "Punase meeskonna olek"},
    "red_team_state_configured_off": {
        "en": "Off (not configured for this run)",
        "et": "Väljas (selle käigu jaoks pole sisse lülitatud)",
    },
    "red_team_state_completed": {"en": "Completed", "et": "Lõpetatud"},
    "red_team_state_skipped": {"en": "Skipped", "et": "Vahele jäetud"},
    "red_team_state_skipped_for_budget": {
        "en": "Skipped to preserve budget",
        "et": "Vahele jäetud eelarve säilitamiseks",
    },
    "red_team_state_failed": {"en": "Failed", "et": "Ebaõnnestus"},
    "red_team_state_externally_blocked": {
        "en": "Externally blocked by the provider",
        "et": "Pakkuja blokeeris juurdepääsu väliselt",
    },
    "red_team_state_externally_blocked_hint": {
        "en": "The provider account/credential denied this request. This is not a code or model defect.",
        "et": "Pakkuja konto/mandaat keeldus sellest päringust. See ei ole koodi ega mudeli viga.",
    },
    "red_team_state_unavailable": {"en": "Not recorded", "et": "Andmed puuduvad"},
    # "Referenced in" a material change is the only red-team "contribution"
    # claim the persisted schema supports (see presenter.
    # red_team_material_change_reference_count) -- deliberately never
    # "found N errors" or "caught N issues", since a trigger is the
    # convergence analyst's own attribution, not a verified fact.
    "red_team_referenced_in_changes_prefix": {"en": "Referenced in", "et": "Mainitud"},
    "red_team_not_referenced_in_changes": {
        "en": "Not referenced in any material change.",
        "et": "Ei ole mainitud üheski sisulises muutuses.",
    },
    "trace_summary_heading": {"en": "Operational trace", "et": "Tehniline jälg"},
    "elapsed_label": {"en": "Elapsed", "et": "Möödunud aeg"},
    "cost_meta_label": {"en": "Cost", "et": "Maksumus"},

    # Display name for a run's *output* language (record.language), shown as
    # ordinary UI text describing an attribute of the run ("Language:
    # inglise" / "Language: English") -- translated according to the
    # CURRENT UI language, same as every other enum display in this table.
    # Deliberately distinct from NATIVE_LANGUAGE_NAMES (below), which is the
    # self-referential "English | Eesti" convention used only for the
    # language *picker* on the new-run form -- a language name shown in its
    # own tongue, the universal convention for a language switcher/picker,
    # never translated into the viewer's UI language.
    "language_display_en": {"en": "English", "et": "inglise"},
    "language_display_et": {"en": "Estonian", "et": "eesti"},

    # Shown near run metadata only when the viewer's UI language differs
    # from this run's own output language (record.language) -- see
    # web/app.py's run_detail route. Keyed by the run's language: the
    # dict's own "en"/"et" slots are filled for completeness, but in
    # practice only the slot matching the *other* language is ever reached,
    # since the notice is hidden entirely when the languages match.
    "language_mismatch_run_en": {
        "en": "This deliberation's content was created in English.",
        "et": "Selle arutelu sisu loodi inglise keeles. Kasutajaliides on praegu eesti keeles.",
    },
    "language_mismatch_run_et": {
        "en": "This deliberation was generated in Estonian. The interface is currently in English.",
        "et": "See arutelu loodi eesti keeles.",
    },

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
    # More specific wording for convergence_analysis specifically -- the
    # only stage that currently reaches the plain (non-fallback-chain)
    # skippable-retry branch in partials/pipeline.html, so this never
    # collides with any other stage's retry button.
    "retry_convergence_analysis": {
        "en": "Retry convergence analysis", "et": "Proovi konsensuse analüüsi uuesti",
    },
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
    # Shown above "Resume run" only when a stage is persisted as "running"
    # but nothing is actually executing it right now -- a previous process
    # was killed/restarted mid-call (see web/app.py's stale_running_stage,
    # AUDIT_REPORT.md Section D.1/J.3). Resuming cannot know whether that
    # original call actually completed; it can only make a new one.
    # Compact technical notice for a working-language stage where the
    # response still did not match the requested working language even
    # after the one bounded recovery attempt (see orchestrator.run_stage's
    # language-contract check, AUDIT_REPORT.md's live-canary findings). The
    # stage's artifact is still shown/used normally -- see
    # presenter.build_pipeline's docstring -- this is a diagnostic notice
    # about the internal working-language optimization, never a claim that
    # the final (output-language) answer itself is wrong.
    "working_language_contract_mismatch_notice": {
        "en": "Working-language instruction was not followed for this stage.",
        "et": "Selle etapi töökeele juhist ei järgitud.",
    },
    "stale_running_notice": {
        "en": "A stage was left running from a previous session and never "
              "finished. Resuming will make a new provider call for it, "
              "which may incur additional cost. All prior completed stages "
              "are unaffected.",
        "et": "Üks etapp jäi eelmisest seansist pooleli ega lõppenud. "
              "Jätkamine teeb selle jaoks uue päringu teenusepakkujale, "
              "mis võib kaasa tuua lisakulu. Kõik varem valminud etapid "
              "jäävad muutumatuks.",
    },
    "starting_label": {"en": "Starting...", "et": "Käivitub..."},

    # Shown near a skipped stage's status note (see build_pipeline's
    # skip_note_key) -- a UI-owned explanatory sentence, not the stored
    # fallback_reason value itself (which stays whatever free text was
    # persisted at skip time, untranslated, for historical/debug purposes).
    "skip_note_red_team": {
        "en": "Skipped after the red-team stage could not complete.",
        "et": "Vahele jäetud, sest punase meeskonna etapp ei õnnestunud.",
    },
    # Distinct from skip_note_red_team above: this is a PROACTIVE, automatic
    # skip (see cost_budget.estimate_remaining_completion_reserve /
    # service._execute) -- red_team was never attempted at all, not failed.
    # Never shown for any stage other than red_team (see
    # orchestrator.RED_TEAM_COMPLETION_RESERVE_SKIP_REASON).
    "skip_note_red_team_budget_reserve": {
        "en": "Red-team was skipped to preserve enough budget to complete the final result.",
        "et": "Punase meeskonna etapp jäeti vahele, et säilitada piisav eelarve lõpptulemuse valmimiseks.",
    },
    "skip_note_convergence_analysis": {
        "en": "Skipped after convergence analysis could not complete.",
        "et": "Vahele jäetud, sest konsensuse analüüs ei õnnestunud.",
    },

    # Shown on the main pipeline view in place of the raw stored parser/
    # schema error when convergence_analysis fails with
    # failure_reason="structured_output_invalid" -- see
    # orchestrator._finalize_convergence_response. The raw technical detail
    # (a bounded excerpt of the actual malformed response) stays in the
    # stage's attempt_log provenance, never shown here.
    "convergence_structured_output_invalid_message": {
        "en": "Convergence analysis could not produce a valid structured result.",
        "et": "Konsensuse analüüs ei suutnud luua korrektset struktureeritud tulemust.",
    },
    "convergence_prior_work_preserved": {
        "en": "Your completed analyses and revisions are preserved.",
        "et": "Sinu valminud analüüsid ja täiendused on säilinud.",
    },

    # -- fallback/provenance labels (backward-compat display; see
    # partials/pipeline.html and partials/result.html) -----------------
    "requested_label": {"en": "Requested", "et": "Soovitud"},
    "used_label": {"en": "Used", "et": "Kasutati"},
    "fallback_reason_label": {"en": "Fallback reason", "et": "Varulahenduse põhjus"},
    "attempts_this_try_label": {"en": "Attempts (this try)", "et": "Katsed (see kord)"},
    "reason_label": {"en": "Reason", "et": "Põhjus"},

    # -- pipeline row labels that mix a provider name with a generic term --
    # (see presenter.STAGE_GROUPS). The provider name half (e.g. "OpenAI",
    # "Anthropic", "OpenAI → Anthropic") is never translated -- these are
    # the generic-term halves only.
    "pipeline_row_candidate_a": {"en": "Candidate A", "et": "Kandidaat A"},
    "pipeline_row_candidate_b": {"en": "Candidate B", "et": "Kandidaat B"},
    "pipeline_row_meta_analysis": {"en": "Meta-analysis", "et": "Metaanalüüs"},
    "pipeline_row_synthesis": {"en": "Synthesis", "et": "Süntees"},

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

    # -- deliberation quality indicator (near Decision Snapshot / pipeline) --
    "quality_heading": {"en": "Deliberation quality", "et": "Arutelu kvaliteet"},
    "quality_complete": {"en": "Complete", "et": "Täielik"},
    "quality_degraded": {"en": "Degraded", "et": "Piiratud"},
    "quality_incomplete": {"en": "Incomplete", "et": "Puudulik"},
    "quality_status_unavailable": {"en": "unavailable", "et": "puudub"},
    "quality_status_truncated": {
        "en": "output truncated", "et": "vastus katkes pikkusepiirangu tõttu",
    },
    "quality_status_skipped": {
        "en": "unavailable (skipped)", "et": "puudub (jäeti vahele)",
    },
    "quality_incomplete_note": {
        "en": "Required evidence is missing, so convergence and synthesis could "
              "not run normally. Retry or resume the failed stage below.",
        "et": "Vajalikud lähteandmed puuduvad, mistõttu konsensuse analüüs ja "
              "lõppsüntees ei saanud tavapäraselt käivituda. Proovi allpool "
              "ebaõnnestunud etappi uuesti või jätka käiku.",
    },
    "quality_degraded_note": {
        "en": "This run reached a final answer, but with less evidence than "
              "usual -- treat conclusions that depend on the missing part "
              "with extra caution.",
        "et": "See arutelu jõudis lõppvastuseni, kuid vähem lähteandmete "
              "põhjal kui tavaliselt -- puuduvast osast sõltuvatesse "
              "järeldustesse suhtu ettevaatlikult.",
    },
    "unknown_evidence_label": {
        "en": "Unknown (insufficient evidence)", "et": "Teadmata (ebapiisavad andmed)",
    },
    "fallback_indicator": {"en": "↓ fallback", "et": "↓ varuvariant"},

    # -- truncation-recovery provenance (initial generation vs the one
    # bounded concise-retry attempt -- see orchestrator.run_stage and
    # presenter.attempt_log_phases). Distinct from the Gemini fallback-chain
    # labels above: this is the *same* model called twice, not a different
    # model being substituted in.
    "attempt_phase_initial": {"en": "Initial generation", "et": "Esialgne genereerimine"},
    "attempt_phase_recovery": {
        "en": "Concise recovery", "et": "Lühendatud kordusgenereerimine",
    },
    # A bounded, at-most-once retry after language_detect.py confidently
    # found a working-language stage's response in the wrong language (see
    # orchestrator.run_stage, AUDIT_REPORT.md's live-canary findings) --
    # distinct from "recovery" above, which is truncation-specific.
    "attempt_phase_language_recovery": {
        "en": "Language correction retry", "et": "Keele parandamise korduskatse",
    },
    "attempt_outcome_output_truncated": {
        "en": "output truncated at max tokens", "et": "vastus katkes tokenite piirmäära tõttu",
    },
    "attempt_outcome_empty_output": {"en": "output empty", "et": "vastus tühi"},
    "attempt_outcome_succeeded": {"en": "succeeded", "et": "õnnestus"},
    "attempt_outcome_provider_error": {"en": "request failed", "et": "päring ebaõnnestus"},
    "attempt_outcome_language_mismatch": {
        "en": "wrong working language", "et": "vale töökeel",
    },
    "attempt_outcome_uncertain": {
        "en": "language uncertain", "et": "keel ebaselge",
    },

    # -- red-team fallback-aware timeout policy (providers.py's
    # GeminiFallbackProvider.generate) -- a model the wall-clock allocation
    # decided not to call at all gets this label instead of "failed", since
    # it never actually ran (see the _Attempt "not_attempted" outcome).
    "attempt_outcome_not_attempted": {"en": "not attempted", "et": "ei proovitud"},
    # Shown on the main pipeline view in place of the raw stored error when
    # a red_team stage's failure_reason is "red_team_budget_exhausted" (see
    # presenter.gemini_budget_timeout_model) -- the raw SDK/technical detail
    # stays in each attempt's own provenance, never shown here. The specific
    # variant names the model whose attempts consumed the budget (always
    # leads the sentence in both languages, so no word-splice is needed);
    # the general variant is used when no specific model can be identified.
    "gemini_timeout_specific_suffix": {
        "en": "timed out before the fallback chain could complete.",
        "et": "aegus enne, kui kogu varumudelite ahel jõuti läbi proovida.",
    },
    "gemini_timeout_general_message": {
        "en": "Gemini did not respond within the red-team time limit. The "
              "configured fallback models were tried as far as the "
              "remaining time allowed.",
        "et": "Gemini ei vastanud punase meeskonna ajapiiri jooksul. "
              "Varumudeleid prooviti nii palju, kui allesjäänud aeg "
              "võimaldas.",
    },

    # -- provider readiness (New Deliberation page) --------------------------
    "readiness_heading": {"en": "Provider readiness", "et": "Teenusepakkujate valmisolek"},
    "check_again_button": {"en": "Check again", "et": "Kontrolli uuesti"},
    # Status badges -- never claim a guarantee about later calls (see
    # readiness.READY_DISCLAIMER, shown alongside "ready" results).
    "readiness_status_ready": {"en": "ready", "et": "valmis"},
    "readiness_status_unavailable": {"en": "not configured", "et": "seadistamata"},
    "readiness_status_auth_error": {"en": "authentication failed", "et": "autentimine ebaõnnestus"},
    "readiness_status_billing_error": {"en": "billing problem", "et": "arveldusprobleem"},
    "readiness_status_permission_error": {"en": "access denied", "et": "juurdepääs keelatud"},
    "readiness_status_model_unavailable": {"en": "model unavailable", "et": "mudel pole saadaval"},
    "readiness_status_rate_limited": {"en": "rate-limited", "et": "piirmäär täitunud"},
    "readiness_status_transient_error": {"en": "temporarily unavailable", "et": "ajutiselt pole saadaval"},
    "readiness_status_unknown": {"en": "unexpected error", "et": "ootamatu viga"},
    "readiness_disclaimer": {
        "en": "A readiness check does not guarantee every later request will "
              "succeed -- a real call can still fail from overload, a "
              "timeout, or a transient provider error.",
        "et": "Valmisoleku kontroll ei taga, et iga hilisem päring õnnestub -- "
              "tegelik päring võib siiski ebaõnnestuda ülekoormuse, ajalõpu "
              "või ajutise teenusepakkuja vea tõttu.",
    },
    "readiness_required_failed_notice": {
        "en": "Deliberation cannot start: a required provider is not ready.",
        "et": "Arutelu ei saa alustada: nõutav teenusepakkuja pole valmis.",
    },
    # -- provider readiness (no default-visible UI -- see
    # docs/decisions/009-compact-readiness-ux.md and its follow-up).
    # Readiness runs automatically when Start is pressed; nothing is shown
    # here at all unless there is an actual failure/warning
    # (readiness_required_failed_notice below, or the Gemini-unavailable
    # notice), or the visitor opens the low-prominence "Advanced" disclosure
    # to trigger a manual recheck.
    "advanced_readiness_summary": {
        "en": "Advanced: provider readiness check",
        "et": "Täpsemalt: teenusepakkujate valmisoleku kontroll",
    },
    "provider_check_automatic_label": {
        "en": "Provider check: automatic", "et": "Teenusepakkuja kontroll: automaatne",
    },
    "check_now_button": {"en": "Check now", "et": "Kontrolli kohe"},
    # Shown inside the Advanced disclosure once a manual check has actually
    # been run and every required provider is ready -- deliberately
    # compact, never the full per-provider list (that stays reserved for an
    # actual failure/warning -- see index.html).
    "providers_ready_label": {"en": "Providers ready", "et": "Teenusepakkujad on valmis"},
    "checked_prefix": {"en": "checked", "et": "kontrollitud"},
    "checked_just_now": {"en": "checked just now", "et": "kontrollitud just nüüd"},
    "gemini_unavailable_notice": {
        "en": "Gemini red-team is currently unavailable.",
        "et": "Punase meeskonna Gemini pole hetkel saadaval.",
    },
    "start_without_red_team_button": {
        "en": "Start without red-team", "et": "Alusta ilma punase meeskonnata",
    },

    # -- cost budget (New Deliberation page + run detail) --------------------
    "max_run_cost_label": {"en": "Max run cost (USD)", "et": "Maksimaalne kulu (USD)"},
    "max_run_cost_helper": {
        "en": "Optional safety limit. The app will stop before sending a "
              "request that could push this run over the budget.",
        "et": "Valikuline kulupiir. Rakendus peatub enne päringut, mis "
              "võiks selle arutelu määratud eelarvet ületada.",
    },
    "max_run_cost_placeholder": {"en": "No limit", "et": "Piirang puudub"},
    "invalid_budget_error": {
        "en": "Max run cost must be a positive dollar amount.",
        "et": "Maksimaalne kulu peab olema positiivne dollarisumma.",
    },
    "cost_estimate_label": {"en": "Estimated cost", "et": "Hinnanguline kulu"},
    "cost_estimate_disclaimer": {
        "en": "A rough estimate, not a guarantee -- actual cost depends on "
              "how much the models actually write.",
        "et": "Ligikaudne hinnang, mitte garantii -- tegelik kulu sõltub "
              "sellest, kui palju mudelid tegelikult kirjutavad.",
    },
    "budget_meta_label": {"en": "Budget", "et": "Eelarve"},
    "budget_spent_of_label": {"en": "spent", "et": "kulutatud"},
    "no_budget_set": {"en": "no limit", "et": "piirang puudub"},
    "run_budget_exceeded_notice": {
        "en": "This run stopped before sending a request that could exceed "
              "its cost budget.",
        "et": "See arutelu peatus enne päringut, mis oleks võinud ületada "
              "selle kulueelarve.",
    },
    "increase_budget_label": {"en": "New max run cost (USD)", "et": "Uus maksimaalne kulu (USD)"},
    "increase_budget_button": {"en": "Update budget", "et": "Uuenda eelarvet"},

    # -- working language note (run metadata / full trace) -------------------
    # Shown only when a run's stored working_language actually differs from
    # its output language (currently: an Estonian-output run, which
    # defaults to an English working language -- see
    # orchestrator.default_working_language). Never shown for a legacy run
    # (working_language is None) or an English-output run (the two are
    # always equal there), so this note only ever appears when the split
    # genuinely happened. Deliberately does NOT say the original question
    # was "translated" -- see the product principle in
    # docs/decisions/008-working-language.md: Estonian stays fully
    # supported, English is only an internal working choice.
    "working_language_note_heading": {
        "en": "Working language: English", "et": "Töökeel: inglise",
    },
    "working_language_note_body": {
        "en": "Intermediate analysis uses English for efficiency and "
              "reliability. The final result remains in Estonian.",
        "et": "Vaheanalüüs tehakse töökindluse ja tokenikulu vähendamiseks "
              "inglise keeles. Lõpptulemus jääb eesti keelde.",
    },

    # -- privacy disclosure (near Start Deliberation) ------------------------
    "privacy_disclosure": {
        "en": "Your question and context are sent to the selected AI providers "
              "for processing. Avoid including information you do not want "
              "shared with those providers.",
        "et": "Sinu küsimus ja lisatud kontekst saadetakse töötlemiseks valitud "
              "tehisintellekti teenusepakkujatele. Ära lisa infot, mida sa ei "
              "soovi nende teenusepakkujatega jagada.",
    },
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
    "attempt": {
        "en": ("attempt", "attempts"),
        "et": ("katse", "katset"),
    },
    # Used by the compact readiness status line (e.g. "checked 4 minutes
    # ago" / "kontrollitud 4 minutit tagasi") -- see index.html. Only
    # reached for count >= 1; a 0-minute case uses the dedicated
    # "checked_just_now" key instead (see _profile_context's
    # readiness_minutes_ago).
    "minute_ago": {
        "en": ("minute ago", "minutes ago"),
        "et": ("minut tagasi", "minutit tagasi"),
    },
    # Run detail's compact Context summary (see presenter.input_preview /
    # run_detail.html) -- "character" via format_count_label (thousands
    # separator, since Context has no length cap and can run into the
    # thousands), "section" via the plain count_label (paragraph counts
    # stay small).
    "character": {
        "en": ("character", "characters"),
        "et": ("tähemärk", "tähemärki"),
    },
    "section": {
        "en": ("section", "sections"),
        "et": ("jaotis", "jaotist"),
    },
    # Decision Cockpit Phase 1's compact trace/observability rollup (see
    # presenter.trace_summary) -- plain counts only, never implying more
    # stages/attempts/retries means a better or worse answer.
    "pipeline_stage": {
        "en": ("pipeline stage", "pipeline stages"),
        "et": ("etapp", "etappi"),
    },
    "provider_attempt": {
        "en": ("provider attempt", "provider attempts"),
        "et": ("pakkuja katse", "pakkuja katset"),
    },
    "stage_with_retry_or_fallback": {
        "en": ("stage needed a retry or fallback", "stages needed a retry or fallback"),
        "et": ("etapp vajas uut katset või varulahendust", "etappi vajasid uut katset või varulahendust"),
    },
}


def count_label(key: str, count: int, lang: str) -> str:
    """'{count} {noun}', with a grammatically appropriate singular/plural
    noun form for the given UI language -- not just an English "+s"."""
    forms = _COUNT_NOUNS[key].get(lang) or _COUNT_NOUNS[key][DEFAULT_UI_LANGUAGE]
    singular, plural = forms
    noun = singular if count == 1 else plural
    return f"{count} {noun}"


def format_count_label(key: str, count: int, lang: str) -> str:
    """Like count_label, but with a locale-appropriate thousands separator
    on the digits themselves (English: comma, "4,382"; Estonian: space,
    "4 382" -- the conventional Estonian digit-group separator). For a
    count that can plausibly run into the thousands (e.g. a long Context's
    character count -- Context has no stored length cap), where
    count_label's bare digits would read poorly.
    """
    forms = _COUNT_NOUNS[key].get(lang) or _COUNT_NOUNS[key][DEFAULT_UI_LANGUAGE]
    singular, plural = forms
    noun = singular if count == 1 else plural
    separator = " " if lang == "et" else ","
    formatted_number = f"{count:,}".replace(",", separator)
    return f"{formatted_number} {noun}"

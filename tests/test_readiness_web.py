"""Offline web-layer tests for the provider-readiness preflight and cost
budget UI (index.html / run_detail.html / web/app.py). Uses the same
`client`/`service` fixtures as test_web.py (FakeOrchestrator, no network);
readiness.check_run_readiness is stubbed to always-ready by conftest.py's
`_always_ready_readiness_report` by default -- tests here that need a
specific readiness outcome monkeypatch it again locally, which layers on
top and wins (see conftest.py's `service` fixture).
"""

from __future__ import annotations

from decimal import Decimal

import pytest


def _submit(
    client,
    *,
    question="What should we do?",
    profile="economy",
    red_team=False,
    context="",
    language="en",
    max_run_cost=None,
    confirm_no_red_team=False,
):
    data = {"question": question, "profile": profile, "context": context, "language": language}
    if red_team:
        data["red_team"] = "1"
    if max_run_cost is not None:
        data["max_run_cost"] = max_run_cost
    if confirm_no_red_team:
        data["confirm_no_red_team"] = "1"
    return client.post("/runs", data=data)


def _patch_readiness(monkeypatch, *, openai_ready=True, anthropic_ready=True, gemini_ready=True):
    import llm_deliberation.service as service_module
    from llm_deliberation.readiness import ProviderReadiness, ReadinessReport, _now_iso

    def _result(provider, model, check_type, ready):
        return ProviderReadiness(
            provider=provider,
            configured_model=model,
            status="ready" if ready else "auth_error",
            checked_at=_now_iso(),
            check_type=check_type,
            paid_probe=False,
            estimated_probe_cost_usd=0.0,
            user_message="Provider readiness check passed." if ready else "Rejected: bad key.",
        )

    def fake(settings, *, red_team_enabled, force=False):
        return ReadinessReport(
            openai=_result("openai", settings.openai_model, "model_retrieve", openai_ready),
            anthropic=_result("anthropic", settings.anthropic_model, "model_retrieve", anthropic_ready),
            gemini=(
                _result("gemini", settings.gemini_model, "model_get", gemini_ready)
                if red_team_enabled
                else None
            ),
        )

    monkeypatch.setattr(service_module.readiness, "check_run_readiness", fake)


# -- 1: no readiness row/button appears in the default new-run form ----------


def test_1_no_readiness_row_visible_by_default(client):
    body = client.get("/").text
    assert "readiness-panel" not in body
    assert "readiness-list" not in body
    assert "readiness-details" not in body
    assert "Provider readiness" not in body  # the full-card/failure heading
    # No compact status line either -- nothing about readiness is shown up
    # front at all now, not even a one-line summary.
    assert "Providers ready" not in body
    assert "readiness-status-line" not in body


def test_1_manual_check_control_exists_but_stays_closed_by_default(client):
    """The optional manual recheck control still exists in the page (it is
    not deleted -- see the task's "Do not remove readiness functionality"),
    but lives inside a closed <details> disclosure so nothing about it is
    visible until the visitor explicitly opens it.
    """
    body = client.get("/").text
    assert '<details class="advanced-diagnostics"' in body
    assert "Check now" in body  # present in markup...
    # ...but the <details> tag itself carries no "open" attribute, so it
    # renders collapsed until the visitor clicks its <summary>.
    advanced_start = body.index('<details class="advanced-diagnostics"')
    tag_end = body.index(">", advanced_start)
    assert "open" not in body[advanced_start:tag_end]


def test_1_large_readiness_card_not_rendered_after_successful_check(client, monkeypatch):
    _patch_readiness(monkeypatch)
    body = client.post(
        "/providers/check",
        data={"profile": "economy", "language": "en", "question": "", "context": ""},
    ).text
    # A successful check stays compact, tucked inside the Advanced
    # disclosure -- no per-provider list/expanded card anywhere.
    assert "readiness-list" not in body
    assert "readiness-details" not in body


# -- 2: Start triggers readiness automatically when cache is absent/stale ----


def test_2_start_triggers_readiness_automatically_with_no_prior_check(
    client, service, monkeypatch
):
    """A plain Start click (no prior "Check now") must still run the
    readiness preflight -- proven here by a required-provider failure
    blocking run creation even though the visitor never manually checked.
    """
    _patch_readiness(monkeypatch, openai_ready=False)
    before = len(service.list_runs())

    response = _submit(client)
    assert response.status_code == 400
    assert len(service.list_runs()) == before


def test_2_start_triggers_readiness_automatically_calls_check_run_readiness(
    client, monkeypatch
):
    calls = {"count": 0}
    import llm_deliberation.service as service_module
    from llm_deliberation.readiness import ProviderReadiness, ReadinessReport, _now_iso

    def fake(settings, *, red_team_enabled, force=False):
        calls["count"] += 1
        ready = ProviderReadiness(
            provider="x", configured_model="m", status="ready", checked_at=_now_iso(),
            check_type="model_retrieve", paid_probe=False, estimated_probe_cost_usd=0.0,
            user_message="ok",
        )
        return ReadinessReport(openai=ready, anthropic=ready, gemini=None)

    monkeypatch.setattr(service_module.readiness, "check_run_readiness", fake)
    _submit(client)
    assert calls["count"] == 1


# -- 3: fresh cached readiness avoids duplicate (SDK-level) checks -----------


def test_3_fresh_cached_readiness_avoids_duplicate_provider_calls(tmp_path, monkeypatch):
    """Exercises the REAL readiness cache (not the always-ready stub the
    `service`/`client` fixtures install), to prove "Check now" followed by
    Start reuses the cached result instead of re-probing the provider SDKs.
    A fresh DeliberationService/app are built here specifically to avoid
    conftest.py's service_module.readiness stub.
    """
    from types import SimpleNamespace

    import openai
    import anthropic
    from fastapi.testclient import TestClient

    from llm_deliberation import readiness as readiness_module
    from llm_deliberation.service import DeliberationService
    from llm_deliberation.web.app import create_app

    readiness_module.invalidate_readiness_cache()
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")

    calls = {"openai": 0, "anthropic": 0}

    def _openai_retrieve(self, model, **kw):
        calls["openai"] += 1
        return SimpleNamespace(id=model)

    def _anthropic_retrieve(self, model_id, **kw):
        calls["anthropic"] += 1
        return SimpleNamespace(id=model_id)

    monkeypatch.setattr(openai.resources.models.Models, "retrieve", _openai_retrieve)
    monkeypatch.setattr(anthropic.resources.models.Models, "retrieve", _anthropic_retrieve)

    svc = DeliberationService(tmp_path / "deliberation.db")
    app = create_app(service=svc)
    with TestClient(app, follow_redirects=True) as test_client:
        # "Check now" -- a forced recheck, one real probe per provider.
        test_client.post(
            "/providers/check",
            data={"profile": "economy", "language": "en", "question": "", "context": ""},
        )
        assert calls == {"openai": 1, "anthropic": 1}

        # Start, immediately after -- must reuse the still-fresh cache
        # rather than probing again.
        test_client.post(
            "/runs", data={"question": "Q?", "profile": "economy", "language": "en"}
        )
        assert calls == {"openai": 1, "anthropic": 1}

    readiness_module.invalidate_readiness_cache()


# -- 4: successful readiness proceeds directly, no extra confirmation --------


def test_4_successful_readiness_proceeds_directly_to_run(client, service):
    response = _submit(client, question="Ship on Friday?")
    assert response.status_code == 200  # followed redirect straight to run detail
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    record = service.get_run(run_id)
    assert record.status == "succeeded"  # FakeOrchestrator resolves instantly
    # No leftover readiness card/notice on the resulting run page -- a
    # successful check is entirely silent, not just compact.
    assert "readiness-details" not in response.text
    assert "Providers ready" not in response.text
    assert "Provider readiness" not in response.text


# -- 7: compact manual "Check now" control -----------------------------------


def test_7_check_now_control_present_with_correct_form_wiring(client):
    body = client.get("/").text
    assert 'formaction="/providers/check"' in body
    assert "Check now" in body


def test_7_check_now_control_triggers_a_real_readiness_check(client, monkeypatch):
    calls = {"count": 0}
    import llm_deliberation.service as service_module
    from llm_deliberation.readiness import ProviderReadiness, ReadinessReport, _now_iso

    def fake(settings, *, red_team_enabled, force=False):
        calls["count"] += 1
        assert force is True  # a manual click always forces a recheck
        ready = ProviderReadiness(
            provider="x", configured_model="m", status="ready", checked_at=_now_iso(),
            check_type="model_retrieve", paid_probe=False, estimated_probe_cost_usd=0.0,
            user_message="ok",
        )
        return ReadinessReport(openai=ready, anthropic=ready, gemini=None)

    monkeypatch.setattr(service_module.readiness, "check_run_readiness", fake)
    response = client.post(
        "/providers/check",
        data={"profile": "economy", "language": "en", "question": "", "context": ""},
    )
    assert response.status_code == 200
    assert calls["count"] == 1
    assert "Providers ready" in response.text


# -- 30/31/8: compact readiness strings render in EN/ET -----------------------


def test_readiness_strings_render_en(client):
    body = client.get("/").text
    assert "Provider check: automatic" in body
    assert "Check now" in body
    # The full "Provider readiness" heading/per-provider list is reserved
    # for an actual failure -- see test_1_large_readiness_card_not_rendered_by_default.
    assert "Provider readiness" not in body


def test_readiness_strings_render_et(client):
    client.get("/ui-language/et", follow_redirects=True)
    body = client.get("/").text
    assert "Teenusepakkuja kontroll: automaatne" in body
    assert "Kontrolli kohe" in body
    assert "Teenusepakkujate valmisolek" not in body


def test_readiness_ready_status_strings_render_en(client, monkeypatch):
    _patch_readiness(monkeypatch)
    body = client.post(
        "/providers/check",
        data={"profile": "economy", "language": "en", "question": "", "context": ""},
    ).text
    # Shown inside the low-prominence Advanced disclosure only -- see
    # test_1_large_readiness_card_not_rendered_after_successful_check.
    assert "Providers ready" in body
    assert "checked" in body.lower()
    assert "Check now" in body


def test_readiness_ready_status_strings_render_et(client, monkeypatch):
    client.get("/ui-language/et", follow_redirects=True)
    _patch_readiness(monkeypatch)
    body = client.post(
        "/providers/check",
        data={"profile": "economy", "language": "et", "question": "", "context": ""},
    ).text
    assert "Teenusepakkujad on valmis" in body
    assert "kontrollitud" in body.lower()
    assert "Kontrolli kohe" in body


# -- Check providers route ---------------------------------------------------


def test_check_providers_shows_ready_status(client):
    response = client.post(
        "/providers/check",
        data={"profile": "economy", "language": "en", "question": "", "context": ""},
    )
    assert response.status_code == 200
    body = response.text
    assert "ready" in body
    assert "Check now" in body


def test_check_providers_preserves_typed_question_and_context(client):
    response = client.post(
        "/providers/check",
        data={
            "profile": "economy",
            "language": "en",
            "question": "Should we do X?",
            "context": "Some background",
        },
    )
    body = response.text
    assert "Should we do X?" in body
    assert "Some background" in body


def test_check_providers_does_not_create_a_run(client, service):
    before = len(service.list_runs())
    client.post(
        "/providers/check",
        data={"profile": "economy", "language": "en", "question": "", "context": ""},
    )
    assert len(service.list_runs()) == before


# -- 7/8: required provider failure blocks run creation ----------------------


def test_required_provider_failure_blocks_run_creation(client, service, monkeypatch):
    _patch_readiness(monkeypatch, openai_ready=False)
    before = len(service.list_runs())

    response = _submit(client)
    assert response.status_code == 400
    assert len(service.list_runs()) == before  # no run/stage rows created
    body = response.text
    assert "auth_error" in body or "authentication failed" in body


def test_required_provider_failure_message_never_claims_guarantee(client, monkeypatch):
    _patch_readiness(monkeypatch, anthropic_ready=False)
    body = _submit(client).text
    assert "will definitely work" not in body.lower()


# -- 9: gemini failure + red-team enabled -> explicit choice -----------------


def test_gemini_unavailable_shows_explicit_choice_not_silent_disable(client, service, monkeypatch):
    _patch_readiness(monkeypatch, gemini_ready=False)
    before = len(service.list_runs())

    response = _submit(client, red_team=True)
    assert response.status_code == 200  # re-rendered form, not a redirect to a new run
    assert len(service.list_runs()) == before
    body = response.text
    assert "Gemini red-team is currently unavailable" in body
    assert "Start without red-team" in body


def test_start_without_red_team_creates_run_without_red_team(client, service, monkeypatch):
    _patch_readiness(monkeypatch, gemini_ready=False)

    response = _submit(client, red_team=True, confirm_no_red_team=True)
    assert response.status_code == 200  # redirected (TestClient follows) to run detail
    run_id = str(response.url).rstrip("/").rsplit("/", 1)[-1]
    record = service.get_run(run_id)
    assert record.red_team_enabled is False


# -- 10: gemini not checked when red-team disabled ---------------------------


def test_gemini_not_checked_when_red_team_disabled_in_submitted_form(client, monkeypatch):
    calls = {"gemini": 0}
    import llm_deliberation.service as service_module
    from llm_deliberation.readiness import ProviderReadiness, ReadinessReport, _now_iso

    def fake(settings, *, red_team_enabled, force=False):
        if red_team_enabled:
            calls["gemini"] += 1
        ready = ProviderReadiness(
            provider="x", configured_model="m", status="ready", checked_at=_now_iso(),
            check_type="model_retrieve", paid_probe=False, estimated_probe_cost_usd=0.0,
            user_message="ok",
        )
        return ReadinessReport(openai=ready, anthropic=ready, gemini=None)

    monkeypatch.setattr(service_module.readiness, "check_run_readiness", fake)
    _submit(client, red_team=False)
    assert calls["gemini"] == 0


# -- 34/35: UI never overclaims readiness or mislabels the budget -----------


def test_provider_readiness_passed_does_not_claim_guaranteed_success(client):
    # The compact success line itself is terse by design (no large panel --
    # see the module docstring), so it must not contain overclaiming
    # language rather than being required to repeat the full disclaimer.
    body = client.post(
        "/providers/check",
        data={"profile": "economy", "language": "en", "question": "", "context": ""},
    ).text
    assert "will definitely work" not in body.lower()
    assert "guaranteed" not in body.lower()


def test_readiness_disclaimer_still_shown_when_expanded_on_failure(client, monkeypatch):
    # The full "does not guarantee" disclaimer remains present once details
    # are actually expanded (a real failure) -- it's just no longer shown
    # unconditionally on every successful check.
    _patch_readiness(monkeypatch, openai_ready=False)
    body = _submit(client).text
    assert "does not guarantee" in body


def test_ui_never_labels_budget_as_provider_account_balance(client):
    body = client.get("/").text
    lowered = body.lower()
    assert "account balance" not in lowered
    assert "provider balance" not in lowered


# -- 32/33: budget helper text renders in EN/ET ------------------------------


def test_budget_helper_renders_en(client):
    body = client.get("/").text
    assert "Optional safety limit" in body


def test_budget_helper_renders_et(client):
    client.get("/ui-language/et", follow_redirects=True)
    body = client.get("/").text
    assert "Valikuline kulupiir" in body


# -- 19: estimated cost renders before start ---------------------------------


def test_estimated_cost_renders_on_new_deliberation_page(client):
    body = client.get("/").text
    assert "Estimated cost" in body
    assert "$" in body


# -- 17/18: budget persists / invalid budget rejected ------------------------


def test_positive_budget_persists_and_renders_on_run_detail(client):
    response = _submit(client, max_run_cost="0.75")
    body = response.text
    assert "0.75" in body


def test_invalid_budget_shows_translated_error_en(client, service):
    before = len(service.list_runs())
    response = _submit(client, max_run_cost="-1")
    assert response.status_code == 400
    assert "positive dollar amount" in response.text
    assert len(service.list_runs()) == before


def test_invalid_budget_shows_translated_error_et(client, service):
    client.get("/ui-language/et", follow_redirects=True)
    response = _submit(client, max_run_cost="-1")
    assert response.status_code == 400
    assert "positiivne dollarisumma" in response.text


def test_run_with_no_budget_shows_no_limit_on_run_detail(client):
    body = _submit(client).text
    assert "no limit" in body.lower()


# -- runtime budget block surfaced on run detail -----------------------------


def test_budget_exceeded_stage_shows_update_budget_form(client, fake_orchestrator_state):
    fake_orchestrator_state["upper_bound_cost_usd"] = {"analysis_a": Decimal("5.00")}
    response = _submit(client, max_run_cost="0.01")
    body = response.text
    assert "run_budget_exceeded" in body or "stopped before sending" in body
    assert 'action="/runs/' in body and "/budget" in body

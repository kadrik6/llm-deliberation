"""Proves the autouse network/credential safety net in conftest.py
actually works -- a real network attempt fails loudly instead of either
silently succeeding or silently being skipped. If this file's tests ever
start failing because the guard stopped blocking something, that is
itself the signal to investigate, not something to work around here.

Asserts on RuntimeError (BlockedNetworkAccess's base class) and the
message text, not on `BlockedNetworkAccess` itself: pytest loads
tests/conftest.py (no __init__.py in tests/) as a bare top-level
`conftest` module via its own rootdir-based collection, which is a
*different* module object from `tests.conftest` (importable here only
because of this project's `pythonpath = ["."]` setting) -- so the class
object a `from tests.conftest import BlockedNetworkAccess` would bind is
not the same object the fixture actually raises. RuntimeError is a
builtin, never duplicated this way, so asserting against it (plus the
message) verifies the real behavior without being tripped up by that.
"""
from __future__ import annotations

import os
import socket

import pytest

_BLOCKED_MESSAGE = "real network connection"


def test_a_direct_outbound_connection_attempt_is_blocked():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(RuntimeError, match=_BLOCKED_MESSAGE):
        s.connect(("8.8.8.8", 443))


def test_connect_ex_is_also_blocked():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(RuntimeError, match=_BLOCKED_MESSAGE):
        s.connect_ex(("8.8.8.8", 443))


def test_socket_create_connection_is_blocked_too():
    """socket.create_connection() -- what http.client/urllib and many
    other libraries call internally -- goes through socket.connect()
    under the hood, so patching connect() alone catches this too."""
    with pytest.raises(RuntimeError, match=_BLOCKED_MESSAGE):
        socket.create_connection(("8.8.8.8", 443), timeout=1)


def test_a_real_openai_sdk_client_call_is_blocked_not_silently_allowed():
    """Reproduces the exact mistake this guard exists to prevent: a real
    OpenAI() client constructed with a (fake-looking, but not faked by
    FakeOrchestrator) key, actually attempting a call. Before this guard,
    this made a real, auth-failed-but-real outbound HTTP request to
    OpenAI's actual production IP -- confirmed directly in CI, where this
    test's first version failed because the installed openai/httpx version
    there propagates the block raw instead of wrapping it in
    APIConnectionError like the version in local dev does. Either way is a
    loud, unmistakable failure, never a silent success -- so this asserts
    on the underlying cause (walking __cause__) rather than one specific
    wrapper exception type, to stay correct across SDK versions."""
    from openai import OpenAI

    client = OpenAI(api_key="sk-this-should-never-reach-the-network", timeout=5.0, max_retries=0)
    with pytest.raises(Exception) as excinfo:
        client.models.retrieve("gpt-5.6-terra")

    chain = []
    exc: BaseException | None = excinfo.value
    while exc is not None:
        chain.append(str(exc))
        exc = exc.__cause__
    assert any(_BLOCKED_MESSAGE in msg for msg in chain)


def test_api_keys_are_never_the_real_local_environment_values():
    """The autouse _neutralize_real_api_keys fixture always wins by
    default -- a test that forgets to fake its own key still never
    inherits whatever a developer's local .env happens to have."""
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        assert os.environ[key] == "sk-test-network-blocked-do-not-use"


def test_a_fixture_that_sets_its_own_fake_key_still_wins(service):
    """The `service` fixture (used by most of this suite) sets its own
    fake key values -- confirms that still works on top of the sentinel
    default, i.e. setenv-after-setenv, never a delenv-after-setenv
    ordering risk."""
    assert os.environ["OPENAI_API_KEY"] == "test-openai-key"

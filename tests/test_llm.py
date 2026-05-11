"""Tests for the LLM transport abstraction.

We don't hit the network. We patch the two backend functions and verify
that ``complete`` routes to the right one given the right combination of
explicit api_key, CLAUDECODE env, and ANTHROPIC_API_KEY env.
"""

from __future__ import annotations

import asyncio

import pytest

import weavenames.llm as llm


# --- Helpers ---------------------------------------------------------------


def _make_fake_anthropic_call() -> tuple[list[dict], callable]:
    """Returns (calls_list, fake_async_fn). The fake records every call."""

    calls: list[dict] = []

    async def fake(*, system, user, model, max_tokens, api_key):
        calls.append(
            dict(
                system=system,
                user=user,
                model=model,
                max_tokens=max_tokens,
                api_key=api_key,
            )
        )
        return "anthropic-text"

    return calls, fake


def _make_fake_oauth_call() -> tuple[list[dict], callable]:
    calls: list[dict] = []

    async def fake(*, system, user, model):
        calls.append(dict(system=system, user=user, model=model))
        return "oauth-text"

    return calls, fake


# --- Tests -----------------------------------------------------------------


def test_explicit_api_key_routes_to_anthropic_sdk(monkeypatch):
    """Explicit api_key wins over CLAUDECODE env."""

    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")

    anth_calls, anth_fake = _make_fake_anthropic_call()
    oauth_calls, oauth_fake = _make_fake_oauth_call()
    monkeypatch.setattr(llm, "_complete_via_anthropic_sdk", anth_fake)
    monkeypatch.setattr(llm, "_complete_via_agent_sdk", oauth_fake)

    out = asyncio.run(
        llm.complete(
            system="sys",
            user="usr",
            model="m",
            max_tokens=10,
            api_key="explicit-key",
        )
    )

    assert out == "anthropic-text"
    assert len(anth_calls) == 1
    assert anth_calls[0]["api_key"] == "explicit-key"
    assert oauth_calls == []


def test_env_api_key_wins_over_oauth_when_both_present(monkeypatch):
    """ANTHROPIC_API_KEY in env wins over CLAUDECODE — matches CLI precedence.

    The Claude CLI itself uses env API key over OAuth when both are present;
    we mirror that to avoid silent billing (user thinks they're on OAuth
    but the underlying CLI charges the env key).
    """

    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")

    anth_calls, anth_fake = _make_fake_anthropic_call()
    oauth_calls, oauth_fake = _make_fake_oauth_call()
    monkeypatch.setattr(llm, "_complete_via_anthropic_sdk", anth_fake)
    monkeypatch.setattr(llm, "_complete_via_agent_sdk", oauth_fake)

    out = asyncio.run(
        llm.complete(system="sys", user="usr", model="m", max_tokens=10)
    )

    assert out == "anthropic-text"
    assert len(anth_calls) == 1
    assert anth_calls[0]["api_key"] == "env-key"
    assert oauth_calls == []


def test_oauth_fires_when_only_claude_code_signal_present(monkeypatch):
    """CLAUDECODE=1 with no env API key -> OAuth path."""

    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    anth_calls, anth_fake = _make_fake_anthropic_call()
    oauth_calls, oauth_fake = _make_fake_oauth_call()
    monkeypatch.setattr(llm, "_complete_via_anthropic_sdk", anth_fake)
    monkeypatch.setattr(llm, "_complete_via_agent_sdk", oauth_fake)

    out = asyncio.run(
        llm.complete(system="sys", user="usr", model="m", max_tokens=10)
    )

    assert out == "oauth-text"
    assert len(oauth_calls) == 1
    assert oauth_calls[0] == {"system": "sys", "user": "usr", "model": "m"}
    assert anth_calls == []


def test_env_api_key_used_when_not_in_claude_code(monkeypatch):
    """No CLAUDECODE -> use ANTHROPIC_API_KEY via anthropic SDK."""

    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")

    anth_calls, anth_fake = _make_fake_anthropic_call()
    oauth_calls, oauth_fake = _make_fake_oauth_call()
    monkeypatch.setattr(llm, "_complete_via_anthropic_sdk", anth_fake)
    monkeypatch.setattr(llm, "_complete_via_agent_sdk", oauth_fake)

    out = asyncio.run(
        llm.complete(system="sys", user="usr", model="m", max_tokens=10)
    )

    assert out == "anthropic-text"
    assert len(anth_calls) == 1
    assert anth_calls[0]["api_key"] == "env-key"
    assert oauth_calls == []


def test_no_auth_raises(monkeypatch):
    """No explicit key, no CLAUDECODE, no env key -> RuntimeError."""

    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="No authentication available"):
        asyncio.run(
            llm.complete(system="sys", user="usr", model="m", max_tokens=10)
        )


def test_in_claude_code_session_signal(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    assert llm._in_claude_code_session() is True

    monkeypatch.setenv("CLAUDECODE", "0")
    assert llm._in_claude_code_session() is False

    monkeypatch.delenv("CLAUDECODE", raising=False)
    assert llm._in_claude_code_session() is False

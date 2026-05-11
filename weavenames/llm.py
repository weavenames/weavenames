"""Unified LLM call abstraction for weavenames.

We support two transports for the same logical operation ("send a system +
user prompt, get text back"):

1. **Claude Code OAuth** via `claude-agent-sdk`. Free when the caller is
   running inside Claude Code with a Max / Pro subscription — no per-call
   API charges.
2. **Anthropic API key** via the `anthropic` SDK. The portable fallback for
   standalone / OSS users with no Claude Code session.

Both paths are kept as dependencies on purpose: OAuth-only would break OSS
users without Claude Code installed; API-key-only would force the developer
running this inside Claude Code to pay for calls their subscription already
covers.

Auth resolution order (see ``complete``):

1. **Explicit ``api_key`` arg** — for tests and explicit override. Forces
   the ``anthropic`` SDK path regardless of environment.
2. **Claude Code OAuth via ``claude-agent-sdk``** — selected when
   ``CLAUDECODE=1`` is set in the environment (the canonical signal that
   we are running inside a Claude Code session) AND the SDK is importable.
   On any runtime failure here we surface the error rather than silently
   falling through; the whole point of this branch is to bill OAuth, not
   the API key.
3. **``ANTHROPIC_API_KEY`` env var via the ``anthropic`` SDK** — used when
   we are not in a Claude Code session.
4. **``RuntimeError``** with a helpful message if none of the above
   resolve.

The shape of the call is intentionally tiny: one system prompt, one user
message, no tool use, no streaming to the caller, no conversation history.
That keeps the two backends interchangeable without leaking transport
details into the rest of the codebase.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Only imported for type checking; avoids a hard dep at import time
    # in environments where one SDK or the other is missing.
    pass


def _in_claude_code_session() -> bool:
    """Return True iff we're running inside a Claude Code session.

    The Claude Code harness sets ``CLAUDECODE=1`` in the child process
    environment for every shell / subagent it spawns. That's the cleanest
    signal we have — it doesn't depend on which CLI binary is installed
    or where the OAuth tokens live on disk.
    """

    return os.environ.get("CLAUDECODE") == "1"


async def _complete_via_agent_sdk(
    *,
    system: str,
    user: str,
    model: str,
) -> str:
    """OAuth path. Drives the Claude Code CLI via ``claude-agent-sdk``.

    We disable all tool use (``allowed_tools=[]``), cap to a single turn,
    and ignore filesystem settings so the call is fully self-contained and
    doesn't pick up the user's project ``CLAUDE.md`` or skills.

    Note: ``ClaudeAgentOptions`` does not expose a ``max_tokens`` field —
    the SDK is a CLI wrapper, not a direct API client. For the prompts
    weavenames issues (50 names per response, single-token verdicts),
    that's fine; responses are tiny.
    """

    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        query,
    )

    # Scrub ANTHROPIC_API_KEY from the env we pass to the SDK subprocess.
    # If both signals are present, the user almost certainly wants OAuth
    # (free) instead of API key (paid). Leaving the key in the env risks
    # the CLI billing the API key path.
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    options = ClaudeAgentOptions(
        system_prompt=system,
        model=model,
        max_turns=1,
        allowed_tools=[],
        # setting_sources=None (default) means the CLI does NOT load the
        # user's CLAUDE.md, project settings, or skills. Fully isolated.
        env=env,
    )

    chunks: list[str] = []
    result_error: str | None = None

    async for message in query(prompt=user, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    chunks.append(block.text)
        elif isinstance(message, ResultMessage) and message.is_error:
            # Capture the structured error if the CLI surfaces one. We
            # still let the loop drain so the subprocess closes cleanly.
            result_error = message.result or "claude-agent-sdk reported an error"

    if result_error and not chunks:
        raise RuntimeError(f"Claude Code OAuth call failed: {result_error}")

    return "".join(chunks)


async def _complete_via_anthropic_sdk(
    *,
    system: str,
    user: str,
    model: str,
    max_tokens: int,
    api_key: str,
) -> str:
    """API-key path. Direct ``anthropic`` SDK call, one client per request.

    Lifecycle is intentionally short — the abstraction owns its own
    client so callers don't have to thread an ``AsyncAnthropic`` through.
    For the volume weavenames issues (a few dozen calls per pipeline)
    the per-call client overhead is negligible.
    """

    from anthropic import AsyncAnthropic
    from anthropic.types import TextBlock

    client = AsyncAnthropic(api_key=api_key)
    async with client:
        msg = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    return "".join(block.text for block in msg.content if isinstance(block, TextBlock))


async def complete(
    *,
    system: str,
    user: str,
    model: str,
    max_tokens: int,
    api_key: str | None = None,
) -> str:
    """Return the assistant's text response. Auth resolved automatically.

    Resolution order:

    1. Explicit ``api_key`` — forces the ``anthropic`` SDK path.
    2. Claude Code OAuth (``CLAUDECODE=1`` env + ``claude-agent-sdk``
       importable) — free under a Max / Pro subscription.
    3. ``ANTHROPIC_API_KEY`` env var via the ``anthropic`` SDK.
    4. Otherwise: ``RuntimeError``.

    Args:
        system: System prompt.
        user: User message text.
        model: Model identifier (e.g. ``claude-haiku-4-5-20251001``).
        max_tokens: Response cap. Honored on the API-key path; ignored on
            the OAuth path (the SDK does not expose a per-call token cap).
        api_key: Explicit API key. When set, bypasses OAuth and uses the
            ``anthropic`` SDK directly. Primarily for tests.

    Returns:
        The concatenated text from the assistant's response.
    """

    # 1. Explicit override always wins.
    if api_key:
        return await _complete_via_anthropic_sdk(
            system=system,
            user=user,
            model=model,
            max_tokens=max_tokens,
            api_key=api_key,
        )

    # 2. OAuth via Claude Code, if available.
    if _in_claude_code_session():
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError:
            # SDK not installed for some reason — fall through to API key.
            pass
        else:
            return await _complete_via_agent_sdk(
                system=system,
                user=user,
                model=model,
            )

    # 3. API key from env.
    env_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_key:
        return await _complete_via_anthropic_sdk(
            system=system,
            user=user,
            model=model,
            max_tokens=max_tokens,
            api_key=env_key,
        )

    # 4. No auth at all.
    raise RuntimeError(
        "No authentication available. Either run inside Claude Code "
        "(OAuth, free under a Max subscription) or set ANTHROPIC_API_KEY."
    )

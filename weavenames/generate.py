"""LLM candidate generation via Anthropic.

Generates ~200 candidates in batches of 50, parallelized with asyncio.gather.
Output is deduplicated and lightly normalized. The taste profile, if present,
is woven into the system prompt as anchors and anti-anchors.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass

from anthropic import AsyncAnthropic

from weavenames.models import TasteProfile

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_BATCH_SIZE = 50
DEFAULT_TARGET = 200

# Lightweight regex to extract candidate-looking tokens from model output.
# We accept letters and digits, length 3-20 (filters take care of the real
# length window).
_NAME_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9]{2,19}\b")


@dataclass
class GenerationConfig:
    description: str
    keywords: list[str] | None = None
    negative_examples: list[str] | None = None
    target_count: int = DEFAULT_TARGET
    batch_size: int = DEFAULT_BATCH_SIZE
    model: str = DEFAULT_MODEL


def _build_system_prompt(profile: TasteProfile) -> str:
    prefs = profile.preferences
    accepted = ", ".join(profile.history.accepted) or "(none yet)"
    rejected_lines = (
        "\n".join(
            f"- {r.name}: {r.reason or 'rejected'}" for r in profile.history.rejected[:25]
        )
        or "(none yet)"
    )
    return f"""You are a naming expert for technical / developer-facing products. You generate name candidates that are:
- Short, pronounceable, distinctive, and memorable
- 1-3 syllables, {prefs.syllables} preferred
- Max length: {prefs.max_length} characters when possible
- Open to Greek, Latin, and Anglo-Saxon roots; compound words; coined words; modern coinages
- AVOID these suffixes: {", ".join(prefs.avoid_suffixes)}
- AVOID generic SaaS suffixes (-ly, -ify, -ster, -hub, -app, -kit, -box) unless genuinely earned
- AVOID literal product descriptions (e.g. "TaskApp", "NamingTool")
- AVOID names that look like company-named-after-the-feature

Examples the user has previously accepted (match this taste): {accepted}

Examples the user has rejected and why (avoid these patterns):
{rejected_lines}
"""


def _build_user_prompt(
    description: str,
    keywords: list[str] | None,
    negative_examples: list[str] | None,
    batch_size: int,
) -> str:
    kw_line = (
        f"Stylistic anchor keywords: {', '.join(keywords)}\n" if keywords else ""
    )
    neg_line = (
        f"Names already in this space (avoid being too close): {', '.join(negative_examples)}\n"
        if negative_examples
        else ""
    )
    return f"""Project description: {description}

{kw_line}{neg_line}
Generate exactly {batch_size} distinct name candidates.

Output format: one name per line, just the name. No numbering, no commentary, no quotes, no explanations. ASCII letters and digits only. Each name 4-14 characters."""


def _parse_response(text: str) -> list[str]:
    names: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Strip leading bullets / numbers / punctuation that the model might
        # add despite instructions.
        line = re.sub(r"^[\s\-\*\d\.\)\(]+", "", line).strip()
        # Take the first name-shaped token on the line.
        m = _NAME_RE.search(line)
        if m:
            names.append(m.group(0))
    return names


async def _generate_batch(
    client: AsyncAnthropic,
    cfg: GenerationConfig,
    profile: TasteProfile,
    batch_size: int,
) -> list[str]:
    msg = await client.messages.create(
        model=cfg.model,
        max_tokens=2048,
        system=_build_system_prompt(profile),
        messages=[
            {
                "role": "user",
                "content": _build_user_prompt(
                    cfg.description, cfg.keywords, cfg.negative_examples, batch_size
                ),
            }
        ],
    )
    # Concatenate all text blocks the response returns.
    text = "".join(
        block.text for block in msg.content if getattr(block, "type", None) == "text"
    )
    return _parse_response(text)


async def generate_candidates(
    cfg: GenerationConfig,
    profile: TasteProfile,
    *,
    api_key: str | None = None,
) -> list[str]:
    """Generate ~target_count deduplicated candidates."""

    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Export it or pass --api-key."
        )

    client = AsyncAnthropic(api_key=api_key)
    n_batches = max(1, (cfg.target_count + cfg.batch_size - 1) // cfg.batch_size)

    async with client:
        results = await asyncio.gather(
            *[
                _generate_batch(client, cfg, profile, cfg.batch_size)
                for _ in range(n_batches)
            ],
            return_exceptions=True,
        )

    seen: set[str] = set()
    unique: list[str] = []
    for batch in results:
        if isinstance(batch, Exception):
            # One bad batch should not nuke the run; we'll just have fewer
            # candidates. The orchestrator can decide what to do with the
            # final count.
            continue
        for name in batch:
            lowered = name.lower()
            if lowered not in seen:
                seen.add(lowered)
                unique.append(name)
    return unique

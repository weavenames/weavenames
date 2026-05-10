# Weavenames

**Naming for builders.**

Weavenames generates project name candidates and verifies their availability across PyPI, npm, GitHub, and domain registries — in parallel — so you stop hand-checking 20 things per name.

```bash
uv tool install weavenames
export ANTHROPIC_API_KEY=sk-ant-...
weavenames "open-source memory layer for AI agents, Postgres-native"
```

## Why this exists

Existing naming tools (Namelix, Namesnack, Naming.fyi) generate names but don't check developer registries. You can spend an afternoon hand-verifying PyPI, npm, GitHub, and domains for a single shortlist.

Weavenames does the whole pipeline:

1. **Generates** ~200 candidates via Claude Haiku 4.5 with taste guardrails.
2. **Filters** for length, pronounceability, profanity, and similarity to existing infra packages.
3. **Checks** PyPI, npm, GitHub user/repo, RDAP domains (`.com .dev .io .ai`), and USPTO trademarks in parallel.
4. **Interprets** raw availability — flags squatters, premium-priced domains, GitHub-reserved names, pending-delete states (RFC 8056).
5. **Ranks** with pairwise LLM comparison (ELO over rounds — more stable than 0–10 scoring).
6. **Reports** a markdown matrix with per-registry status glyphs and rationale.

## Phase 1 capabilities

| Stage | Status | Notes |
|---|---|---|
| LLM generation | done | Claude Haiku 4.5, batches of 50, parallelized |
| Heuristic filters | done | length / profanity / pronounceability / collisions / Levenshtein + n-gram similarity |
| PyPI / npm | done | Direct registry GET |
| GitHub user + repo | done | `GITHUB_TOKEN` lifts limits to 5K/hr |
| RDAP domains | done | `whodap` via IANA bootstrap, per-TLD asyncio semaphore |
| Squatter detection | done | Zero downloads + no GitHub link + 2yr inactive → `soft_claimed` |
| GitHub reserved-name filter | done | Static list of platform-reserved usernames |
| RDAP `pending delete` mapping | done | RFC 8056 statuses → `available_soon` |
| Fastly premium-domain check | done | Skips silently if `FASTLY_API_TOKEN` not set |
| USPTO TESS / TMSearch | done | Top 30, classes 9 (software) and 42 (SaaS) |
| Pairwise LLM ranking | done | ELO with stability-based early exit |
| Markdown + CSV report | done | Glyph matrix + auto-rationale |
| Interactive taste profile | done | `~/.weavenames/profile.json`, accept/reject prompt after each run |

## Usage

```bash
weavenames "your project description here"

# Common flags
weavenames "..." --keywords memory,agent,postgres
weavenames "..." --tlds .com,.dev,.io --top 30
weavenames "..." --target 300 --rank-rounds 6
weavenames "..." --output report.md --csv shortlist.csv
weavenames "..." --no-trademark --no-fastly --no-prompt
```

## Environment

| Variable | Required? | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | Required | Generation + ranking |
| `GITHUB_TOKEN` | Recommended | Without it, GitHub repo search is skipped and user check is rate-limited at 60/hr |
| `FASTLY_API_TOKEN` | Optional | Enables premium / aftermarket domain detection (10K free / month) |
| `WEAVENAMES_PROFILE` | Optional | Override default profile path (`~/.weavenames/profile.json`) |

## Output legend

| Glyph | Meaning |
|---|---|
| ✅ | Free |
| ❌ | Taken |
| ⚠️ | Soft-claimed (squatter / abandoned) |
| 🚫 | Reserved (e.g. GitHub platform name) |
| 🕒 | Available soon (RDAP pending-delete / redemption) |
| 💰 | Premium / aftermarket pricing |
| ❓ | Probe error (transient) |
| ➖ | Skipped (e.g. `.io` has no public RDAP server) |

## Status

**Pre-alpha — Phase 1.** Phases 2 (Claude skill wrapper) and 3 (social handles, multi-language profanity, Cloudflare Registrar) are on the roadmap.

## Development

```bash
uv sync --extra dev
uv run pytest
uvx ruff check .
```

## License

MIT

# Session Status

**Session:** Weavenames: spec → ship → OAuth
**Project:** weavenames
**Last Session:** 2026-05-11 08:45 MT
**Status:** Shipped

## What We Did

- Reviewed and rewrote the brandgen spec → `weavenames-spec.md` (taste fixes, scope cuts, pairwise ranking, interpretation layer, USPTO Phase 1)
- Researched domain availability landscape (Barbara) — found Domainr is deprecated, Fastly Domain Research API has 10K free/month, Cloudflare Registrar API now exists in beta
- Confirmed no existing OSS tool combines LLM generation + multi-registry availability — the wedge is real
- Ran Namelix manually, Kyle picked `weavenames` from a shortlist; verified clean across PyPI/npm/GitHub/.com/.dev/.io/.ai
- Locked the namespace: GitHub org `weavenames`, PyPI v0.0.1, npm v0.0.1, weavenames.com purchased
- Dispatched Nightwing → built Phase 1 (generate / filter / availability / interpret / trademark / rank / report)
- Dispatched Alfred → caught 3 blockers (path traversal, mypy errors, dead constant) → fixed verbatim → merged
- Dispatched Nightwing → OAuth refactor via claude-agent-sdk with API-key fallback
- Dispatched Alfred → caught the env-scrub flaw (`options.env` is override, not replacement)
- Reframed the fix: empirical test showed `ANTHROPIC_API_KEY` isn't exported in Kyle's shell, so scrub was dead code. Flipped resolution order (env key → OAuth) to match CLI precedence and eliminate silent-billing risk
- First successful OAuth dogfood run: `roost / tether / dote` for "developer-facing CLI for dotfile management"

## Key Decisions

- **Engine-first, skill-second architecture**: Python package `weavenames` is the asset, Claude skill wrapper is Phase 2 surface
- **Pairwise LLM ranking, not 0-10 scoring**: ~3x more stable
- **JSON taste profile, not memory-system integration**: 95% of value at 5% of complexity
- **whodap + IANA bootstrap, not rdap.org**: rdap.org rate-limits at 1 req/sec
- **USPTO TESS in Phase 1, not Phase 4**: trademark is a P0 filter for any name you'd commercialize
- **Resolution order (post-Alfred)**: explicit api_key → `ANTHROPIC_API_KEY` env → `CLAUDECODE=1` OAuth → RuntimeError. Mirrors underlying Claude CLI precedence, removes silent-billing edge case
- **Both SDKs as deps**: `anthropic` for portability, `claude-agent-sdk` for free OAuth — OSS users and Kyle both win

## What's Next

- [ ] Phase 2: Claude skill wrapper at `~/.claude/skills/weavenames/`
- [ ] Real dogfood: name something concrete (not a test description)
- [ ] Address watch-outs from Alfred (silent TIE on judge failure, missing buy-links in report, crates.io/Docker Hub not yet implemented)
- [ ] Bump to v0.1.0 and publish real pipeline to PyPI/npm (replace stub)
- [ ] OAuth subprocess overhead is ~5x slower than API — consider subprocess pooling in Phase 3 if it bugs

## Blockers

None.

## Files Modified

- `~/Desktop/weavenames-spec.md` — full v2 spec written
- `~/Desktop/weavenames-spec-v2.md` — intermediate spec, superseded
- `~/Desktop/brandgen-spec-v2.md` — first rewrite of brandgen-spec.md
- `~/Dev/weavenames/` — entire repo created from scratch
  - `pyproject.toml`, `README.md`, `LICENSE`, `.gitignore`, `package.json` — scaffolding
  - `weavenames/__init__.py`, `cli.py`, `generate.py`, `filters.py`, `interpret.py`, `rank.py`, `report.py`, `profile.py`, `pipeline.py`, `models.py`, `llm.py`
  - `weavenames/availability/{pypi,npm,github,domains,trademark}.py`
  - `weavenames/data/{collisions,known_packages,github_reserved}.json`
  - `tests/{test_filters,test_interpret,test_rank,test_llm,test_smoke}.py`
- `~/.claude/projects/-Users-kylenorthup/memory/MEMORY.md` — auto-edited during session

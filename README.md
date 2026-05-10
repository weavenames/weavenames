# Weavenames

**Naming for builders.**

Weavenames generates project name candidates and verifies their availability across PyPI, npm, GitHub, and domain registries — in parallel — so you stop hand-checking 20 things per name.

```bash
uv tool install weavenames
weavenames "open-source memory layer for AI agents, Postgres-native"
```

## Why this exists

Existing naming tools (Namelix, Namesnack, Naming.fyi) generate names but don't check developer registries. You can spend an afternoon hand-verifying PyPI, npm, GitHub, and domains for a single shortlist.

Weavenames does the whole pipeline:

1. **Generates** 200–500 candidates via LLM with taste guardrails.
2. **Filters** for length, pronounceability, profanity, and similarity to existing projects.
3. **Checks** PyPI, npm, GitHub user/repo, RDAP domains (`.com .dev .io .ai`), and social handles in parallel.
4. **Interprets** raw availability — flags squatters, premium-priced domains, pending-delete states.
5. **Trademark** screen via USPTO TESS on top 30.
6. **Ranks** with pairwise LLM comparison (more stable than 0–10 scoring).
7. **Reports** a markdown matrix with buy links.

## Status

**Pre-alpha.** Phase 1 in active development.

## License

MIT

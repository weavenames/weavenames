# Fix Spec: Greenfield .com is the product, so make the tool actually serve it

**Status:** queued for a dedicated window (NOT today). Surfaced by the 2026-06-23 dogfood
against the `screen-draw` macOS tool.

## The problem in one line
Weavenames' headline promise is a **free .com**, but it ranked taken-.com names above the one
candidate (`vexxel`) whose .com was actually free, and half the `.com` column came back `❓`
(unknown) instead of a real answer.

## Three separate bugs (root-caused, fix in this order)

### 1. .com reliability — kill the `❓` flood  (do FIRST, no new dependency)
- **Root cause:** `domains.py:~97`. Verisign rate-limits `.com` RDAP; the checker retries
  ONCE then returns `status="error"` (`❓`). A 60-name batch hits Verisign 60x at once, so most
  `.com` checks never resolve.
- **Fix (lazy, authoritative, zero new keys):**
  - **DNS pre-screen.** Before any RDAP call, do a cheap DNS lookup. A `.com` with NS/SOA
    records is definitely registered, mark taken and skip the RDAP call. Only spend an
    authoritative RDAP call on NXDOMAIN (looks-unregistered) names. Cuts Verisign hits ~90%.
  - **Real backoff** on the survivors: exponential, 3-4 retries, not 1.
  - **Define the `error`-status multiplier** so `❓` does NOT score as neutral-available.
- **Only if that's still throttled:** add a purpose-built oracle (see API note below).

### 2. Greenfield gate — stop averaging a dead .com away  (do SECOND)
- **Root cause:** `pipeline.py:~83` `_availability_score` is a WEIGHTED AVERAGE across all
  registries. A dead `.com` gets pulled back up by free PyPI/npm/.ai, so taken-.com names
  out-score clean ones. `.com` weight of 1.0 isn't enough when free package registries can
  compensate.
- **Fix:** a `--greenfield` (or `--require-tld .com`) mode where a TAKEN primary TLD is a
  HARD GATE — disqualify the candidate, or near-zero its composite — not one weighted term.
  Default the primary TLD to `.com`. Package registries become tiebreakers, not rescuers.
- **Acceptance:** re-run the `screen-draw` description in greenfield mode; every name at the
  top of the list has a confirmed-free primary TLD, and `❓`-on-.com is near zero.

### 3. Generation taste is weak  (SEPARATE window, separate file)
- Output was startup-mush (`clayix`, `brimox`, `zenolia`); the only evocative names
  (`inker`, `pictura`, `gestura`) all had taken .coms. This is `generate.py` prompt/guardrail
  tuning, unrelated to the ranking/availability bugs. Its own loop, its own session.

## API decision (the ".com lookup" question)
Stay on **RDAP/whodap + DNS pre-screen** first — RDAP is the authoritative source; the issue
was volume + backoff, not the source. If an oracle is still needed after #1:
- **Namecheap `domains.check`** — best free pick. Boolean available/taken, batches ~50/call,
  generous limits. Needs account + IP allowlist.
- **Porkbun / Dynadot** — free fallback, smaller batch.
- **Fastly Domain Research (ex-Domainr)** — re-verify it isn't sunset first; the May research
  notes contradict themselves (Domainr "deprecated" vs Fastly "10K/mo free" — same product).
  We already have a `FASTLY_API_TOKEN` hook, so it's least-new-plumbing IF it's alive.
- **Skip:** GoDaddy (gated behind spend), rdap.org (1 req/sec).

## Out of scope for this spec
Buy-links in the report, crates.io/Docker Hub checks, OAuth subprocess pooling (the 5m10s
ranking overhead) — all already tracked in SESSION_STATUS.md, not blockers for greenfield.

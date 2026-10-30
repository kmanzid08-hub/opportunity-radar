# Controlled development integration test

Run from the repository root, using the project virtual environment:

```powershell
venv\Scripts\python.exe -m scripts.controlled_live_test --database opportunities.db
```

This is a read-only preflight. It requires an existing SQLite file **inside this
repository**, Alembic `head`, existing active Business Profiles, and configured
`GEMINI_API_KEY` and `BRAVE_API_KEY` (or `BRAVE_SEARCH_API_KEY`). It never migrates,
seeds, repairs, or creates the application database. Resolve schema/profile
prerequisites separately. PostgreSQL URLs and production environments are refused.

After validation and deliberate review, explicitly allow the live test:

```powershell
venv\Scripts\python.exe -m scripts.controlled_live_test --database opportunities.db --execute
```

Optional `--company-id ID` (repeat at most twice) and `--country COUNTRY` select
existing profiles and one of their configured countries. With no selection, the
first two active profiles and their first configured country are used. No company,
country, opportunity, or source is fabricated. Discovery makes one search request
using an existing consulting-discovery query and considers at most three results.

## Hard bounds and costs

- At most 3 source candidates, 5 considered crawl pages **total**, and 2 companies.
- `--max-sources`, `--max-pages`, `--max-companies` can lower these limits, not raise them.
- The default global Gemini ceiling is **20 requests**, including failed requests.
  `--max-ai-calls` can explicitly select 1–43. Every attempt, including retries,
  consumes this same budget. The unique-operation envelope is 3 source + 5
  opportunity + 10 match + up to 10 qualifying-match triage operations.
  Nonqualifying matches stop at matching: all 3 sources, 5 pages and 2 companies
  need at most 18 requests without retries/cache hits in that case. A run can
  still stop short due to qualifying triage calls or transient retries.
- SDK retries are disabled; the central client permits at most three attempts
  for transient failures, with 1/2-second delays plus up to 250 ms jitter.
  Durable caches are checked before generation. Only successful structured
  results enter the success cache. After exhaustion, the per-run fingerprint
  guard blocks another operation on the same input; future runs may retry errors.
  The standalone one-request diagnostics explicitly use just one client attempt.
- HTTP: at most 3 source fetches, 5 crawl fetches and 3 robots fetches plus one search.
  Cached HTML is shared. No redirects, no automatic retries, 2 MB response cap,
  bounded timeouts, and no non-public hosts or credential-bearing URLs.
- Robots exclusions are respected; an unavailable robots file denies crawling.
  PDF/binary links are skipped rather than converted into synthetic opportunities.
- No full discovery job, full scan, scheduler, production settings change, or deploy.

The runner calls the existing source, opportunity, matching and qualifying-match
triage services and persists through their existing transaction boundaries.
Triage uses the authoritative stored match qualification, not a duplicate score
threshold. Readiness and requirement extraction are never automatic ingestion
steps; use the explicit Analyze/Refresh readiness action separately. A rejected
or failed candidate does not become verified; one failed source/company does not
justify fake data or keyword fallback. With no qualifying result, the run reports
that outcome rather than forcing a Discover entry.

## Reporting and manual inspection

The terminal reports stage decisions, confidence, request fingerprints, counts,
public opportunity facts, scores, strength and available priority. Query strings
are removed from printed URLs. No keys, full pages, raw profiles or documents are
printed. Company IDs identify selected companies without dumping profile details.

Company-derived explanations, detailed gaps and next actions can quote private
experience/personnel information. Those stay in the local detail page; terminal
quality output explicitly points to them and reports gap counts instead. Review
the actual stored rationale there; the runner does not invent a public substitute.

After processing, in-process HTTP requests verify Discover and detail against
stored data while external HTTP and AI are blocked. Results include visibility,
match consistency, stored triage and readiness. A nonqualifying match or existing
deadline/status filter may correctly exclude an opportunity; visibility is not
forced. Rendering does not trigger an analysis or crawl.

Run health is separate from business outcome: `run_success` is true when no
unrecovered technical failures remain, including rendering checks. Exit code 0
includes zero qualifying matches, legitimately rejected sources, and designed
triage/readiness skips. Execution failures (including exhausted request budgets)
exit 1; preflight blockers or uncaught exceptions exit 2.
`qualifying_result_found` and `qualifying_opportunities` report distinct verified
opportunities with at least one qualifying stored match, regardless of whether
triage succeeds or deadline/status filters hide them. `complete_flow` remains a
diagnostic for a qualifying result with triage reaching Discover, not an exit-code
requirement. The terminal reports run status and qualifying-opportunity count.
Existing stored readiness may still be displayed. Downstream
errors retain distinct provider, structured-output, local-validation,
persistence and request-budget categories; diagnostics contain only safe schema
paths/codes and application-owned reasons, never returned content. Local
validation failures do not enter successful caches and may be reanalyzed later.

Readiness uses the existing Phase 8 metadata boundary. There is no document library
yet, so unavailable document knowledge remains UNKNOWN, not proof of absence.

# Provenance and geography

`Opportunity.source_url` remains the first discovery URL; `discovered_from_url`
is its explicit read-only alias. Each subsequent discovery path retains its own
linked `AIOpportunityAnalysis.source_url`. `original_source_url` is nullable and
holds a resolved authoritative notice. `canonical_url` prefers that notice and
otherwise returns the first discovery URL. Canonical identity is the deduplicated
opportunity's database ID, not the discovery URL.

Sources have independent AUTHORITATIVE, DISCOVERY_ONLY, REPOST, or UNKNOWN roles.
The role does not change source acceptance or opportunity quality thresholds.
Old source records default to UNKNOWN, without forcing a SOURCE cache refresh.

Resolution uses already-observed links and issuer domains from approved
authoritative sources or an observed buyer website identified by the analysis.
URL shape helps distinguish a specific notice from a listing/homepage. Ambiguous
links remain unresolved. No extra crawling, URL invention, or remote validation
is performed. This is conservative provenance evidence, not proof of domain
ownership or document authenticity. Opaque document links require explicit
semantic identification as the original notice as well as observed issuer-domain
evidence. Missing evidence never invalidates an otherwise valid opportunity.

The strict opportunity analysis stores buyer_country, delivery_countries,
eligibility_countries, and geographic_scope in the existing analysis JSON.
Legacy country is derived only from delivery_countries:

- One delivery country: that country.
- Multiple delivery countries: MULTI_COUNTRY.
- No delivery countries: null, including when buyer/search/old country is known.

Unknown legacy geography is not backfilled from search context or buyer location.
The selected company countries still drive discovery, but are not delivery
evidence. REGIONAL, GLOBAL, and REMOTE can describe scope without invented country
lists. Eligibility and buyer countries remain separate.

Matching consumes the same semantic payload used by its fingerprint, including
geography. URL, source-role, buyer-website, and source-confidence changes alone
do not invalidate matches. Updated authoritative content or geography does.
When multiple analyses exist, stored rendering and matching prefer authoritative
content, then the most recent analysis. Rendering performs no AI or crawler work.

Migration 20260930_10 adds only opportunities.original_source_url and
sources.source_role. It performs no data backfill and is PostgreSQL/SQLite
compatible. It is intentionally forward-only: downgrade refuses to discard
provenance data. Use a database backup and a separately reviewed recovery plan
if rollback is required. No application startup/runtime DDL is introduced.

Deduplication uses resolved authoritative URL identity plus the existing semantic
deduplication. Unresolved copies with materially different content can still
remain separate; no speculative fuzzy merging is introduced.

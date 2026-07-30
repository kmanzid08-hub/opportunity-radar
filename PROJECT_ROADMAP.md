# Opportunity Radar: Multi-Tenant SaaS Architecture Roadmap

## Executive Vision

Opportunity Radar will evolve from a single-organization tender dashboard into a secure, configurable, international opportunity intelligence and pursuit-management platform. Any organization—consulting, audit, accounting, engineering, software, legal, healthcare, education, manufacturing, construction, logistics, recruitment, government contracting, nonprofit, or another sector—will be able to define what it does, where it works, which buyers it values, and what an attractive opportunity means.

The product will discover and normalize public opportunities such as government and private tenders, RFPs, RFQs, EOIs, grants, funding calls, subcontracting notices, research opportunities, innovation challenges, and partnerships. It will then match those opportunities independently against each tenant's private configuration. Each tenant will receive its own scores, explanations, workflow, documents, users, notifications, integrations, dashboards, and commercial subscription.

The platform should become:

- Global in country, language, currency, timezone, and opportunity coverage.
- Tenant-safe by construction, with defense-in-depth isolation.
- Configurable through data and administration screens rather than hard-coded country or service rules.
- API-first while retaining the current FastAPI application as the initial migration base.
- Explainable: users can see why an opportunity matched and which rules contributed.
- Operationally reliable: scanning, parsing, deduplication, scoring, and notifications are observable and retryable.
- Commercially viable: metering, plans, subscriptions, billing, entitlements, and enterprise controls are first-class.
- AI-assisted but not AI-dependent: deterministic fallbacks remain available, and human approval controls consequential actions.

Success means that two unrelated organizations can use the same deployment, configure entirely different markets and services, and never gain access to each other's identities, settings, searches, scores, pipeline, documents, billing, integrations, or activity.

### Current-State Baseline

The current repository is a synchronous FastAPI and Jinja2 application using SQLAlchemy. It serves three HTML views: an opportunity dashboard, an opportunity detail page, and a pipeline table. Forms post directly to FastAPI routes. There is no browser-side application framework and no material JavaScript.

The database defaults to local SQLite and can use PostgreSQL through `DATABASE_URL` and `psycopg`. Tables are created with `Base.metadata.create_all()`, and several scripts or runtime paths issue direct `ALTER TABLE` or `CREATE TABLE` statements. The active UI stores lead fields directly on `Opportunity`, while separate `Lead` and `Proposal` models and migration scripts also exist. Those separate models are not integrated consistently with the active route layer.

Discovery and scanning currently comprise:

- A dedicated Job in Rwanda scraper.
- A generic organization-website crawler that follows robots.txt and records source health.
- Two overlapping Brave Search discovery implementations.
- Hard-coded Rwanda queries, English phrases, service categories, URL rules, and organization-specific user-agent text.
- Deterministic keyword and evidence scoring in `app/filters.py`; there is no current machine-learning or LLM scoring service.
- Source confidence, URL relevance, and source-health priority calculations.
- GitHub Actions schedules for daily scanning and periodic website discovery.
- A local in-process scheduler and an additional automation script, creating overlapping scheduling paths.

The current implementation is a useful prototype, but it has no authentication, authorization, tenant key, tenant-scoped query layer, public API boundary, migration framework, automated test suite, background job system, durable event model, billing, or international configuration model. GitHub Actions connect directly to the application database, and scanning work is coupled to application models and synchronous HTTP requests.

The roadmap below treats the current behavior as functionality to preserve while replacing unsafe or non-scalable mechanisms incrementally.

## Guiding Principles

1. **Tenant safety is an invariant.** Every tenant-owned record, query, cache key, event, object-storage key, job, log context, and search index entry carries a tenant boundary.
2. **Preserve working behavior through incremental replacement.** Start with a modular monolith and strangler migrations; do not rewrite the product wholesale.
3. **Configuration over hard-coding.** Countries, languages, services, scoring, source policies, statuses, currencies, branding, notifications, and integrations are data.
4. **Separate public facts from private decisions.** A public tender may be shared platform intelligence; a tenant's match, notes, score, proposal, and decision are private.
5. **PostgreSQL is the production authority.** Use PostgreSQL constraints, transactions, row-level security, and indexes in production while keeping supported local workflows compatible with SQLite.
6. **One schema history.** All schema changes use versioned migrations. Runtime code must never mutate schema.
7. **Explicit boundaries.** Domain services own business decisions; routes handle transport; repositories handle persistence; workers handle asynchronous execution.
8. **API-first contracts.** Web, mobile, integrations, and automation consume versioned service contracts rather than duplicating business logic.
9. **Secure defaults and least privilege.** Deny access unless explicitly granted; scope credentials, sessions, jobs, and integration tokens narrowly.
10. **Explainability and provenance.** Preserve source URL, fetched time, parser version, original artifact, rule version, score components, and AI model/prompt metadata.
11. **Idempotency everywhere.** Crawls, imports, webhooks, notifications, payments, and writes must be safe to retry.
12. **Human control for consequential actions.** AI may recommend, summarize, extract, or draft; submission, deletion, payment, and external communication require authorization and audit.
13. **International by design.** Store UTC timestamps, IANA timezones, BCP 47 language tags, ISO country/currency codes, Unicode text, and locale-aware display preferences.
14. **Operational visibility is a feature.** Structured logs, metrics, traces, health checks, job histories, and alerting are required.
15. **Data minimization and lifecycle control.** Collect only needed data; provide retention, export, deletion, and legal-hold mechanisms.
16. **Quality gates before scale.** Tests, type checks, migrations, security checks, and tenant-isolation checks precede new surface area.

## Target Architecture

### Architectural Style

Begin with a modular monolith in FastAPI. It offers clear domain boundaries without the operational cost and distributed failure modes of premature microservices. Modules should communicate through explicit application services and domain events. Components that need independent scaling—crawler workers, document processing, notifications, search indexing, and AI inference—can later be extracted behind the same contracts.

Suggested logical modules:

- `identity`: users, sessions, invitations, memberships, roles, permissions, SSO.
- `tenancy`: organizations, settings, branding, domains, entitlements, tenant context.
- `catalog`: countries, regions, languages, currencies, sectors, services, opportunity types.
- `opportunities`: canonical opportunity facts, versions, buyers, lots, deadlines, tags.
- `discovery`: source catalog, search profiles, source discovery, connector configuration.
- `scanning`: jobs, runs, fetches, parsing, normalization, deduplication, health.
- `matching`: tenant eligibility, scoring rule sets, evaluations, explanations, saved searches.
- `pipeline`: tenant opportunity states, leads, tasks, activities, calendar events.
- `proposals`: go/no-go, proposal workflow, templates, contributors, submissions.
- `documents`: object metadata, attachments, versions, extraction, knowledge base.
- `people`: consultants, skills, certificates, availability, proposal staffing.
- `notifications`: preferences, subscriptions, templates, deliveries, digests.
- `reporting`: dashboards, widgets, reports, exports, metrics.
- `integrations`: API keys, OAuth connections, webhooks, provider adapters.
- `billing`: plans, subscriptions, usage, invoices, payments, Stripe references.
- `ai`: conversations, messages, prompt versions, model runs, feedback, guardrails.
- `audit`: immutable security and business audit records.

### Runtime Topology

The intended production topology is:

1. A load-balanced FastAPI web/API service.
2. PostgreSQL as the transactional system of record.
3. Redis, or an equivalent managed service, for short-lived caching, rate limits, distributed locks, and queue coordination.
4. Durable background workers for scanning, parsing, matching, document processing, notifications, exports, and integrations.
5. A scheduler that enqueues due work rather than performing crawling inside the web process.
6. S3-compatible object storage for fetched artifacts, attachments, generated proposals, and exports.
7. A search service when PostgreSQL full-text search no longer meets scale or multilingual relevance needs.
8. Transactional email and optional SMS/push providers.
9. An observability stack for logs, metrics, traces, errors, job dashboards, and alerts.
10. A CDN/WAF in front of static assets and public endpoints.

### Request and Data Flow

```text
Browser / Mobile / Customer API
              |
       FastAPI API + Web
              |
  Authentication -> Tenant Context -> Authorization
              |
        Domain Services
       /       |        \
PostgreSQL   Queue     Object Storage
                |
             Workers
     / scanning / scoring / notifications /
    documents / AI / webhooks / reporting
```

For opportunity ingestion:

```text
Country/source adapter
  -> fetch artifact
  -> parse candidate
  -> normalize fields
  -> detect language and country
  -> deduplicate/canonicalize
  -> persist opportunity version
  -> identify eligible tenant profiles
  -> evaluate tenant-specific rules
  -> create/update private tenant matches
  -> notify according to private preferences
```

### Backend Evolution

- Keep FastAPI, SQLAlchemy, and Jinja2 initially.
- Split the current `app/main.py` route collection into routers and application services.
- Introduce request-scoped database sessions and a mandatory tenant context.
- Replace dataclass transport objects with validated Pydantic request/response schemas at API boundaries.
- Add a repository/query layer that makes unscoped tenant reads difficult.
- Move all crawling and scheduled work outside the web process.
- Replace direct schema mutation with Alembic or an equivalent migration framework.
- Emit domain events through a transactional outbox; process them asynchronously.
- Preserve a compatibility route layer while a versioned JSON API is introduced.

### Frontend Evolution

The current Jinja UI can remain during foundation work. First create shared layouts, accessible components, CSRF protection, and tenant-aware navigation. Build all new capabilities against documented APIs. A separate TypeScript web application should be considered only when interaction complexity justifies it; it is not a prerequisite for tenancy.

The future web experience should include:

- Organization switcher for users who belong to multiple tenants.
- Role-aware navigation.
- Configurable onboarding and search-profile builder.
- Opportunity inbox with explanations, bulk actions, saved views, and collaboration.
- Pipeline and proposal workspaces.
- Source-health and scan-operations consoles for authorized users.
- Dashboard and report builders.
- Billing, branding, members, security, and integration administration.
- WCAG 2.2 AA accessibility, keyboard navigation, responsive behavior, and locale-aware formatting.

### Deployment and Environments

Maintain separate local, test, staging, and production environments with separate databases, object stores, queues, credentials, domains, and identity applications. Production data must never be used for routine development. Build immutable artifacts once, promote the same artifact between environments, and deploy migrations through controlled release jobs.

GitHub Actions should eventually perform linting, typing, tests, migration validation, dependency/security scans, and artifact creation. Production scanners should run in the application's worker infrastructure using least-privilege service identities, not connect to the production database directly from general CI runners.

## Multi-tenancy Strategy

### Recommended Model

Use a shared PostgreSQL database and shared schema with mandatory `organization_id` on every tenant-owned table. Reinforce application scoping with PostgreSQL Row-Level Security (RLS). This provides economical operation for small tenants while preserving a path to dedicated databases for regulated or very large enterprise tenants.

Classify data explicitly:

- **Platform-global reference data:** ISO countries, currencies, languages, permission definitions, public plan definitions.
- **Platform-curated public intelligence:** canonical public sources, buyers, and opportunity facts. Tenants do not own this data, but access still passes through an eligibility/match layer.
- **Tenant-private data:** profiles, preferences, rules, matches, saved searches, notes, workflow, users, documents, proposals, integrations, API keys, billing, and analytics.
- **Hybrid data:** sources may be global, privately created by a tenant, or shared after review. A `visibility_scope` and owner must be explicit.

A canonical `Opportunity` stores public facts. A private `OrganizationOpportunity` links a tenant to that opportunity and stores tenant-specific score, state, assignment, qualification decision, visibility, and timestamps. This prevents duplicating large public content while ensuring one tenant can never observe another tenant's decisions.

### Isolation Controls

- Resolve the active tenant from an authenticated membership, never from an unchecked request parameter.
- Set a transaction-local PostgreSQL tenant variable and enforce RLS policies against it.
- Require `organization_id` in composite foreign keys where practical so cross-tenant references fail at the database level.
- Include `organization_id` in unique constraints and indexes for tenant-owned data.
- Make platform administration a separate, strongly authenticated control plane; do not bypass RLS casually in normal services.
- Give workers explicit service identities and tenant context. Jobs without a valid scope fail closed.
- Prefix cache keys, object paths, search documents, analytics partitions, idempotency keys, and rate-limit buckets with the tenant identifier.
- Encrypt sensitive tenant integration credentials using a managed key service and tenant-aware envelope encryption.
- Redact tenant and personal data from logs and error messages.
- Record membership, role, export, impersonation, secret, and administrative actions in immutable audit logs.
- Add automated negative tests that attempt horizontal and vertical privilege escalation across every resource.

### SQLite Compatibility

SQLite cannot provide PostgreSQL RLS. Local SQLite support should therefore use the same mandatory tenant repository filters and composite ownership checks, with tests proving the behavior. PostgreSQL-specific RLS integration tests must run in CI and staging. Local SQLite is a developer convenience, not evidence that production isolation is correct.

### Enterprise Isolation Path

Add a tenant-placement abstraction early. Most tenants remain in the shared database; selected enterprise tenants may later use a dedicated database, region, encryption key, object-storage bucket, or deployment. Domain services should obtain storage through tenant-aware factories rather than assuming one connection forever.

## Authentication Strategy

Use standards-based OpenID Connect (OIDC) for authentication and keep application authorization in Opportunity Radar.

Recommended model:

- Use a reputable managed identity provider initially, with an abstraction that stores provider and subject identifiers rather than provider-specific assumptions.
- Support passwordless email and secure password login as product needs dictate.
- Add social or enterprise OIDC providers, SAML 2.0, and SCIM for enterprise plans.
- Use short-lived, secure, `HttpOnly`, `Secure`, `SameSite` server-side sessions for the web application.
- Use OAuth 2.1 authorization code with PKCE for mobile and third-party delegated access.
- Use hashed, scoped API keys only for server-to-server access; never expose them to browsers.
- Require verified email before accepting an organization invitation.
- Support MFA, recovery codes, device/session management, session revocation, and step-up authentication for billing, exports, credentials, and tenant administration.
- Model a user globally and membership per organization. A user may belong to multiple organizations with different roles.
- Use RBAC for stable duties and explicit permissions; add constrained ABAC checks for ownership, department, data classification, and workflow state.
- Provide time-bound support impersonation only in an enterprise control plane with user-visible indication, approval policy, reason, and audit trail.
- Protect state-changing web requests with CSRF tokens and validate redirect URLs.
- Rate-limit login, invitation, password recovery, token, and API-key endpoints.

Initial roles may include Organization Owner, Administrator, Opportunity Manager, Business Development User, Proposal Manager, Contributor, Viewer, Billing Administrator, Integration Administrator, and Auditor. Roles must be tenant-configurable without allowing a tenant to invent platform-level permissions.

## Database Evolution

### Modeling Conventions

- Use UUIDv7 or another sortable opaque identifier for externally visible records.
- Give tenant-owned records `organization_id`, `created_at`, `updated_at`, and where needed `created_by_id`, `updated_by_id`, version, archive, and soft-delete metadata.
- Use UTC timezone-aware timestamps and preserve source-local timezone information separately.
- Use ISO 3166 country codes, ISO 4217 currency codes, BCP 47 language tags, and IANA timezone names.
- Use normalized relational columns for security, joins, constraints, and reporting. Use JSON only for provider payloads, versioned rule expressions, and flexible metadata with validation.
- Store money as decimal minor-unit-safe values with an explicit currency.
- Maintain immutable versions for fetched content, scoring evaluations, documents, templates, prompts, and critical settings.
- Avoid database enums for tenant-configurable values; use reference tables or checked platform constants.

### Identity, Tenant, and Access Entities

| Entity | Purpose | Relationships | Important fields | Example |
|---|---|---|---|---|
| **Organizations** | Tenant root and legal/customer identity. | Has memberships, settings, profiles, subscriptions, and all private data. | `id`, `name`, `slug`, `legal_name`, `status`, `default_country_id`, `default_language_id`, `timezone`, `data_region`. | “Acme Engineering Group.” |
| **Users** | Global human identity independent of a tenant. | Has memberships, sessions, identities, and audit actions. | `id`, `email`, `display_name`, `status`, `locale`, `last_login_at`. | `alex@example.com`. |
| **User Identities** | Links a user to an OIDC/SAML provider. | Belongs to User and Identity Provider. | `provider`, `subject`, `email_at_provider`, `claims_version`. | An Entra ID subject. |
| **Memberships** | Connects a user to an organization. | Belongs to Organization and User; has roles and departments. | `status`, `joined_at`, `job_title`, `default_department_id`. | Alex is an active member of Acme. |
| **Invitations** | Controls tenant invitations safely. | Belongs to Organization; issued by User; becomes Membership. | `email`, `token_hash`, `expires_at`, `accepted_at`, `role_ids`. | A seven-day admin invitation. |
| **Roles** | Tenant or platform role definition. | Has permissions; assigned through Membership Roles. | `name`, `scope`, `is_system`, `organization_id`. | “Proposal Manager.” |
| **Permissions** | Stable application capabilities. | Many-to-many with Roles. | `code`, `resource`, `action`, `description`. | `proposal.approve`. |
| **Membership Roles** | Assigns roles within a tenant. | Links Membership and Role. | `membership_id`, `role_id`, `granted_by_id`, `expires_at`. | Temporary billing administrator. |
| **Departments** | Tenant organizational units. | Belongs to Organization; has members, leads, and profiles. | `name`, `code`, `parent_id`, `manager_membership_id`. | “Public Sector Advisory.” |
| **Teams** | Cross-department working groups. | Belongs to Organization; many members and pursuits. | `name`, `purpose`, `status`. | “East Africa Bid Team.” |
| **Email Domains** | Verified tenant domains and domain-join policies. | Belongs to Organization; has verification records. | `domain`, `verification_token_hash`, `verified_at`, `join_policy`. | `acme.example`. |
| **Identity Providers** | Enterprise SSO configuration. | Belongs to Organization; has user identities. | `type`, `issuer`, `client_id`, encrypted config, `enabled`. | Customer SAML connection. |
| **Sessions** | Revocable authenticated sessions. | Belongs to User; may bind current Organization. | `token_hash`, `expires_at`, `last_seen_at`, `ip_hash`, `user_agent`. | A logged-in browser session. |
| **Organization Branding** | Tenant presentation settings. | One current version per Organization; references assets. | `logo_document_id`, colors, typography, email footer, custom domain. | Acme logo and navy theme. |
| **Organization Settings** | General tenant behavior and defaults. | Belongs to Organization; references catalogs. | `timezone`, `date_format`, `default_currency_id`, `retention_policy`, feature preferences. | UTC+2 display and EUR default. |
| **Organization Feature Overrides** | Controlled tenant entitlements beyond plan defaults. | Belongs to Organization; references feature definition. | `feature_code`, `value`, `starts_at`, `ends_at`, `reason`. | Temporary extra scanner capacity. |

### International Catalog and Organization Profile Entities

| Entity | Purpose | Relationships | Important fields | Example |
|---|---|---|---|---|
| **Countries** | Global country catalog and procurement metadata. | Has regions, sources, profiles, and opportunities. | ISO alpha-2/alpha-3, name key, default timezone, procurement metadata. | Kenya (`KE`). |
| **Regions** | Administrative or commercial subdivisions. | Belongs to Country; self-references parent; links profiles/opportunities. | `code`, `name`, `type`, `parent_id`. | Ontario province. |
| **Languages** | Global language catalog. | Used by users, profiles, sources, documents, and opportunities. | BCP 47 tag, display-name key, direction, active flag. | French (`fr`), RTL flag false. |
| **Currencies** | Global currency catalog. | Used by opportunities, proposals, billing, and settings. | ISO code, exponent, symbol, active flag. | USD with exponent 2. |
| **Sectors** | Configurable industry/market taxonomy. | Self-hierarchical; linked to services, organizations, and opportunities. | `name`, `parent_id`, taxonomy source, active flag. | Renewable Energy. |
| **Service Categories** | Groups services into a navigable tenant catalog. | Has Services; may belong to Sector. | `name`, `description`, `parent_id`. | Assurance Services. |
| **Services** | Capabilities that organizations offer. | Belongs to Service Category; linked to profiles and scoring. | `name`, synonyms, language, active flag. | Statutory Audit. |
| **Organization Services** | Tenant-specific service offering. | Links Organization and Service. | `priority`, `description`, `countries`, `active`. | Acme offers bridge design at high priority. |
| **Opportunity Types** | Normalized procurement/opportunity classifications. | Linked to opportunities and search profiles. | `code`, localized name, parent type, lifecycle rules. | RFP, Grant, EOI, RFQ. |
| **Clients / Buyers** | Canonical issuing organizations. | Has buyer aliases and opportunities; tenants may prefer/exclude it. | legal/display names, domains, country, organization type. | A ministry or private corporation. |
| **Buyer Aliases** | Resolves source-specific buyer names. | Belongs to Client/Buyer and optionally Source. | `alias`, normalized alias, language, confidence. | “MoH” → Ministry of Health. |
| **Preferred Clients** | Tenant preference toward a buyer or buyer class. | Links Organization and Client/Buyer. | `preference`, `weight`, `reason`, `active`. | Add 15 points for a development bank. |
| **Organization Countries** | Markets a tenant monitors or serves. | Links Organization and Country. | `mode`, `priority`, regions, local presence, active. | Monitor Uganda; operate nationally. |
| **Organization Languages** | Languages a tenant can process or deliver in. | Links Organization and Language. | proficiency/policy, preferred, translation allowed. | English native, French supported. |
| **Organization Currencies** | Currencies a tenant accepts or displays. | Links Organization and Currency. | preferred, minimum/maximum value policy. | Quote in EUR and USD. |
| **Organization Sectors** | Tenant target-sector preferences. | Links Organization and Sector. | `weight`, `include_descendants`, `excluded`. | Prefer healthcare, exclude tobacco. |
| **Keyword Sets** | Reusable include/exclude/synonym phrases. | Belongs to Organization or platform locale; used by profiles/rules. | `name`, `kind`, `language_id`, matching mode. | “Cloud services synonyms.” |
| **Keywords** | Individual normalized matching terms. | Belongs to Keyword Set. | phrase, normalized phrase, weight, exact/fuzzy mode. | “enterprise resource planning,” +12. |

### Source Discovery, Scanning, and Ingestion Entities

| Entity | Purpose | Relationships | Important fields | Example |
|---|---|---|---|---|
| **Sources** | A canonical publisher, portal, feed, inbox, or search provider. | Has endpoints, countries, owners, scan runs, and opportunities. | `name`, `source_kind`, `visibility_scope`, `owner_organization_id`, trust state. | EU procurement portal or a private tenant feed. |
| **Source Endpoints** | Concrete URL/API/feed/mailbox location to monitor. | Belongs to Source; has connector and scan history. | normalized URL, endpoint type, language, country, cadence, parser key. | A ministry tender RSS feed. |
| **Source Countries** | Declares geographic coverage. | Links Source and Country. | coverage type, priority, notes. | A regional portal covers five countries. |
| **Source Credentials** | Encrypted credentials for authorized sources. | Belongs to Source and possibly Organization/Integration. | secret reference, auth type, expiry, rotation status. | OAuth token for a licensed API. |
| **Source Policies** | Legal and operational crawl constraints. | Belongs to Source/Endpoint. | robots policy, rate limit, terms review, retention, allowed content. | Maximum one request per second. |
| **Connector Definitions** | Versioned adapter/parser capabilities. | Used by endpoints and scan jobs. | `key`, version, supported protocols, configuration schema. | `generic_html_v2`. |
| **Scanning Profiles** | Tenant or platform scanning configuration. | Belongs to Organization or platform; targets sources/countries. | cadence, depth, limits, languages, enabled connectors, budget. | Daily French-language grant scan. |
| **Search Profiles** | Tenant definition of desired opportunities. | Belongs to Organization; references countries, services, types, rules. | `name`, scope, active dates, owner, notification policy. | “East African engineering tenders.” |
| **Saved Searches** | User-defined query/view over available tenant matches. | Belongs to Organization and User. | query AST, filters, sorting, sharing, alert flag. | Open high-score grants due in 30 days. |
| **Scan Jobs** | Durable unit of scheduled or manual work. | Belongs to endpoint/profile; has attempts/runs. | tenant scope, due time, priority, idempotency key, status. | Crawl endpoint 123 at midnight. |
| **Scan Runs** | Execution record for a scan job. | Belongs to Scan Job; has fetches, errors, metrics. | started/completed times, worker, status, counts, trace ID. | Run succeeded with 27 candidates. |
| **Fetch Artifacts** | Immutable record of fetched content and response metadata. | Belongs to Scan Run and Endpoint; references object storage. | URL, status, headers subset, content hash/type, fetched time, storage key. | Archived HTML page hash. |
| **Parser Runs** | Records how an artifact was interpreted. | Belongs to Fetch Artifact and Connector Definition. | parser version, status, warnings, output hash. | `generic_html_v2` extracted three notices. |
| **Raw Opportunity Candidates** | Untrusted extracted data before normalization. | Belongs to Parser Run; may resolve to Opportunity. | raw title/body/dates/URLs, detected locale, extraction confidence. | Date text “31/08/2026”. |
| **Ingestion Errors** | Structured retry/dead-letter diagnostics. | Belongs to a job, run, artifact, or candidate. | stage, error code, retryability, sanitized details, occurrence count. | Parser timeout, retryable. |
| **Source Health Snapshots** | Historical reliability and yield metrics. | Belongs to Endpoint and Scan Run. | latency, HTTP status, failures, candidates, accepted matches, quality score. | 99% success, low recent yield. |
| **Deduplication Decisions** | Explainable candidate-to-canonical resolution. | Links candidate and Opportunity. | strategy, signals, confidence, reviewer, decision. | Same notice number and buyer. |
| **Scan Budgets / Usage Counters** | Enforces plan and safety limits. | Belongs to Organization or platform period. | metric, allowance, consumed, reset time. | 50,000 fetched pages/month. |

### Opportunity and Matching Entities

| Entity | Purpose | Relationships | Important fields | Example |
|---|---|---|---|---|
| **Opportunities** | Canonical public opportunity fact. | Belongs to buyer/source/country/type; has versions, lots, documents, matches. | canonical title, reference, status, publish/deadline times, country, value range, source URL. | National hospital software RFP. |
| **Opportunity Versions** | Immutable source-fact history. | Belongs to Opportunity and Parser Run. | normalized snapshot, content hash, effective time, change summary. | Deadline changed from 10 to 17 May. |
| **Opportunity Lots** | Separately biddable portions of an opportunity. | Belongs to Opportunity; may have services and values. | lot number, title, scope, value, deadline. | Lot 2: Network equipment. |
| **Opportunity Locations** | Geographic delivery/eligibility requirements. | Links Opportunity to Country/Region. | location type, mandatory flag, remote allowed. | Delivery in Northern Province. |
| **Opportunity Services** | Normalized capability classification. | Links Opportunity and Service. | confidence, origin, reviewer state. | Cybersecurity assessment, 0.91. |
| **Opportunity Sectors** | Normalized industry classification. | Links Opportunity and Sector. | confidence, origin. | Public healthcare. |
| **Opportunity Languages** | Document/submission language requirements. | Links Opportunity and Language. | role, mandatory, confidence. | Submission must be French. |
| **Opportunity Values** | Structured monetary estimates. | Belongs to Opportunity/Lot. | amount/range, currency, tax treatment, confidence, source text. | USD 250,000–400,000. |
| **Opportunity Deadlines** | Multiple typed, timezone-aware dates. | Belongs to Opportunity/Lot. | type, instant/local date, timezone, mandatory, source text. | Clarification deadline at 17:00 EAT. |
| **Organization Opportunities** | Private tenant match and workflow root. | Links Organization, Search Profile, and Opportunity; owns scores/leads. | visibility, state, score, decision, assigned user, first/last matched. | Acme marks the hospital RFP “Pursue.” |
| **Scoring Rule Sets** | Versioned collection of scoring behavior. | Belongs to Organization/Search Profile; has Scoring Rules. | name, version, status, threshold, effective dates. | “Engineering profile v4.” |
| **Scoring Rules** | Configurable positive, negative, eligibility, or exclusion rule. | Belongs to Rule Set; references catalog/keywords. | condition AST, weight, hard-exclude flag, priority, explanation template. | +20 when country is Kenya. |
| **Scoring Evaluations** | Immutable result of applying a rule/model version. | Belongs to Organization Opportunity and Rule Set. | total score, eligibility, inputs hash, evaluator version, evaluated time. | Score 84, eligible. |
| **Scoring Contributions** | Per-rule explanation. | Belongs to Scoring Evaluation and Rule. | matched value, points, explanation, evidence reference. | +12 for preferred buyer. |
| **Match Feedback** | User correction used for tuning and evaluation. | Belongs to Organization Opportunity and User. | label, reason, comment, created time. | “Irrelevant: requires local license.” |
| **Eligibility Requirements** | Structured requirements extracted from notices. | Belongs to Opportunity/Lot. | type, text, normalized value, mandatory, confidence. | Five years of prior experience. |
| **Eligibility Assessments** | Private tenant check against requirements. | Belongs to Organization Opportunity and requirement. | result, evidence, reviewer, AI suggestion. | Certificate available; passes. |
| **Tags** | Tenant-defined or platform tags. | Belongs to Organization or platform; many-to-many with resources. | name, color, scope, parent. | “Strategic,” “Donor-funded.” |
| **Custom Fields** | Tenant-configurable schema extension. | Belongs to Organization and resource type. | name, data type, validation, options, sensitivity. | “Partner required?” boolean. |
| **Custom Field Values** | Value for a custom field on a tenant resource. | Belongs to definition and target resource. | typed value columns, version, updated by. | Partner required = Yes. |

### Pipeline, Proposal, People, and Collaboration Entities

| Entity | Purpose | Relationships | Important fields | Example |
|---|---|---|---|---|
| **Leads** | Private qualification and pursuit record. | Belongs to Organization Opportunity; has owner, tasks, activities, proposal. | stage, priority, owner, department, next action, probability, estimated value. | Qualified at 60% probability. |
| **Lead Stages** | Tenant-configurable pipeline stages. | Belongs to Organization; referenced by Leads. | name, order, category, terminal flag, SLA. | Review → Pursue → Submitted. |
| **Proposals** | Bid/proposal execution workspace. | Belongs to Lead/Organization Opportunity; has team, sections, documents, approvals. | status, manager, value, currency, dates, result, version. | Technical proposal in internal review. |
| **Proposal Stages** | Configurable proposal lifecycle. | Belongs to Organization; referenced by Proposals. | name, order, entry/exit rules, terminal flag. | Go/No-Go Approval. |
| **Proposal Contributors** | Assigns users/consultants to proposal responsibilities. | Links Proposal and Membership/Consultant. | role, responsibility, allocation, due date. | Technical lead at 40% allocation. |
| **Proposal Sections** | Structured sections and ownership. | Belongs to Proposal and optional Template Section. | title, order, status, owner, content/version reference. | Methodology section. |
| **Proposal Reviews** | Formal review and approval decisions. | Belongs to Proposal/Section and reviewer. | review type, decision, comments, completed time. | Finance approval granted. |
| **Proposal Submissions** | Immutable record of external submission. | Belongs to Proposal. | channel, submitted time, reference, receipt document, submitted by. | Portal receipt number 9872. |
| **Proposal Templates** | Reusable structured proposal blueprint. | Belongs to Organization; has versions/sections. | name, opportunity type, language, active version. | Standard audit proposal. |
| **Document Templates** | Reusable file or generated-document template. | Belongs to Organization; references Documents. | type, language, variables schema, version. | Cover letter DOCX template. |
| **Tasks** | Assignable work with due dates and status. | Belongs to Organization; may target lead/proposal/opportunity/document. | assignee, creator, status, priority, due time, recurrence. | Obtain bid bond by Friday. |
| **Task Dependencies** | Ordering/blocking between tasks. | Links predecessor and successor Tasks. | dependency type, lag. | Pricing waits for scope review. |
| **Calendar Events** | Meetings, deadlines, reminders, and synchronized events. | Belongs to Organization and related resource. | start/end, timezone, attendees, provider reference. | Pre-bid meeting. |
| **Activity Logs** | User-facing chronological collaboration history. | Belongs to Organization/resource and actor. | verb, summary, structured changes, occurred time. | Lead reassigned to Priya. |
| **Comments** | Threaded collaboration on resources. | Belongs to Organization/resource; authored by User. | body, parent, mentions, edited time. | Question on eligibility. |
| **Mentions** | Tracks users mentioned in comments/content. | Links comment/resource to Membership. | read/notified time. | Notify proposal manager. |
| **Consultants** | Tenant talent profile, including employees or associates. | Belongs to Organization; may link User; has skills/certificates. | name, title, bio, location, availability, billing rate visibility. | Senior civil engineer. |
| **Skills** | Global or tenant-curated capability vocabulary. | Many-to-many with Consultants and Services. | name, category, synonyms. | IFRS 9. |
| **Consultant Skills** | Evidence and proficiency for a consultant. | Links Consultant and Skill. | level, years, last used, verified by. | Advanced, eight years. |
| **Certificates** | Professional credentials and compliance evidence. | Belongs to Consultant/Organization; references issuer and document. | name, number, issued/expires dates, verification status. | ISO lead auditor certificate. |
| **Experience Records** | Structured project/reference experience. | Belongs to Consultant or Organization. | client, role, dates, country, value, description, permission to disclose. | Prior hospital ERP deployment. |

### Documents, Knowledge, Compliance, and Risk Entities

| Entity | Purpose | Relationships | Important fields | Example |
|---|---|---|---|---|
| **Documents** | Logical document metadata independent of storage provider. | Belongs to Organization or platform resource; has versions. | title, classification, owner, language, retention class, current version. | Tender terms of reference. |
| **Document Versions** | Immutable binary/content version. | Belongs to Document; references object storage. | storage key, hash, MIME type, size, malware status, created by. | Signed final PDF version 3. |
| **Attachments** | Associates a document with another resource. | Links Document to opportunity, proposal, task, comment, etc. | purpose, display order, visibility. | Bid receipt attached to submission. |
| **Document Extractions** | Structured text/OCR output and provenance. | Belongs to Document Version and AI/processor run. | extracted text key, language, page count, confidence, processor version. | OCR output for a scanned PDF. |
| **Knowledge Base Collections** | Tenant-controlled retrieval corpus. | Belongs to Organization; contains entries/documents. | name, access policy, language, retention. | Approved case studies. |
| **Knowledge Base Entries** | Searchable reusable knowledge. | Belongs to collection; references document/version. | title, content, metadata, embedding version, approval state. | Standard safeguarding response. |
| **Risk Assessments** | Tenant evaluation of pursuit/delivery risk. | Belongs to Lead/Proposal/Organization Opportunity. | framework version, overall rating, owner, review date. | High currency risk. |
| **Risk Items** | Individual likelihood/impact/control record. | Belongs to Risk Assessment. | category, likelihood, impact, mitigation, owner, status. | Partner capacity risk. |
| **Compliance Checks** | Required legal, policy, or tender compliance test. | Belongs to Proposal/Opportunity and checklist definition. | result, evidence document, reviewer, due date. | Conflict-of-interest check passed. |
| **Compliance Checklists** | Reusable tenant/country/opportunity-type requirements. | Belongs to Organization or platform catalog. | name, jurisdiction, version, items. | EU grant compliance checklist. |
| **Data Retention Policies** | Defines lifecycle rules by data class. | Belongs to Organization/plan/jurisdiction. | resource type, retention period, deletion/anonymization action. | Delete scan artifacts after 180 days. |
| **Legal Holds** | Suspends deletion for scoped records. | Belongs to Organization; targets resources. | reason, scope, start/end, authorized by. | Preserve a disputed proposal. |

### Notification, Integration, Analytics, AI, and Operations Entities

| Entity | Purpose | Relationships | Important fields | Example |
|---|---|---|---|---|
| **Notifications** | In-product notification record. | Belongs to Organization and recipient Membership. | type, subject, payload reference, read time, severity. | Deadline moved earlier. |
| **Notification Preferences** | Per-user/channel delivery policy. | Belongs to Membership; references event types. | channel, enabled, quiet hours, digest cadence, timezone. | Daily email at 08:00 local. |
| **Alert Subscriptions** | Connects saved searches/profiles to recipients. | Belongs to Organization; references profile/search and recipients. | event trigger, threshold, channel policy. | Alert when score ≥80. |
| **Notification Templates** | Localized, versioned delivery content. | Platform or tenant scoped; used by delivery attempts. | event type, channel, language, subject/body template. | French deadline reminder. |
| **Delivery Attempts** | Retryable outbound notification history. | Belongs to Notification/template/provider. | status, attempt, provider ID, error code, sent time. | Email accepted by provider. |
| **API Keys** | Scoped machine authentication. | Belongs to Organization and creator. | prefix, secret hash, scopes, expiry, last used, revoked time. | Read-only opportunities key. |
| **Webhooks** | Customer callback configuration. | Belongs to Organization; has deliveries. | URL, encrypted signing secret, event types, status. | Send `opportunity.matched`. |
| **Webhook Deliveries** | Signed, retryable callback history. | Belongs to Webhook and outbox event. | status, attempts, response code, next retry, payload hash. | Third retry succeeded. |
| **Integrations** | Tenant connection to an external provider. | Belongs to Organization; has credentials and sync state. | provider, status, scopes, encrypted secret reference, metadata. | Microsoft 365 connection. |
| **Integration Sync Runs** | Import/export synchronization execution. | Belongs to Integration. | direction, cursor, counts, status, error summary. | Imported calendar updates. |
| **Dashboards** | Tenant/user configurable analytic view. | Belongs to Organization/User; has Widgets. | name, visibility, layout version, filters. | Executive pursuit dashboard. |
| **Widgets** | Configured metric or visualization. | Belongs to Dashboard. | type, query definition, layout, display options. | Pipeline value by country. |
| **Reports** | Versioned reusable report definition. | Belongs to Organization. | name, query, columns, schedule, access policy. | Monthly win-rate report. |
| **Report Runs / Exports** | Immutable generated output. | Belongs to Report and Document. | parameters, status, format, generated time, expiry. | Quarterly XLSX export. |
| **AI Conversations** | Tenant-private AI assistance thread. | Belongs to Organization/User and optional resource. | title, purpose, model policy, retention class. | Discuss go/no-go decision. |
| **AI Messages** | Ordered conversation messages. | Belongs to AI Conversation. | role, content reference, citations, token counts, safety state. | Assistant summary with source pages. |
| **AI Runs** | Auditable model invocation. | Belongs to tenant/resource/message; references prompt/model versions. | provider, model, prompt hash, cost, latency, status, redaction state. | Eligibility extraction run. |
| **Prompt Versions** | Reviewed templates and output schemas. | Platform or tenant scoped; used by AI Runs. | purpose, version, template, schema, approval state. | Proposal outline prompt v5. |
| **AI Feedback** | Human evaluation of AI output. | Belongs to AI Run/User. | rating, correction, issue type. | Incorrect deadline extraction. |
| **Activity Events / Outbox Events** | Durable domain events for asynchronous work. | Belongs to aggregate and tenant; consumed by handlers. | event type, payload version, occurred/published times, idempotency key. | `proposal.submitted.v1`. |
| **Audit Logs** | Immutable security and administrative evidence. | Scoped to Organization or platform; references actor/resource. | actor, action, before/after hashes, IP context, occurred time. | API key revoked. |
| **Job Definitions** | Registered asynchronous job type and policy. | Has Job Executions. | name, queue, timeout, retry policy, payload schema. | `score_opportunity`. |
| **Job Executions** | Operational execution and retry state. | Belongs to definition/tenant/resource. | status, attempt, scheduled/start/end, worker, sanitized error. | Notification retry attempt 2. |
| **Feature Flags** | Controlled rollout and experimentation. | Platform definition with tenant/user targeting. | key, default, rules, expiry, owner. | Enable new scoring UI for beta tenants. |

### Commercial Entities

| Entity | Purpose | Relationships | Important fields | Example |
|---|---|---|---|---|
| **Plans** | Public commercial package definition. | Has prices, entitlements, subscriptions. | code, name, market, active dates, visibility. | Professional. |
| **Plan Entitlements** | Limits and capabilities included in a plan. | Belongs to Plan and feature definition. | feature code, limit value, enforcement mode. | 25 users, 20 profiles. |
| **Plan Prices** | Currency/interval-specific pricing. | Belongs to Plan. | currency, amount, interval, tax behavior, provider price ID. | USD 199 monthly. |
| **Subscriptions** | Organization's SaaS contract state. | Belongs to Organization and Plan; references Stripe Customer. | status, period, trial, renewal, cancellation, provider subscription ID. | Professional annual subscription. |
| **Subscription Changes** | Auditable scheduled or completed plan change. | Belongs to Subscription. | from/to plan, effective time, proration policy, actor. | Upgrade next billing cycle. |
| **Usage Records** | Metered billable consumption. | Belongs to Organization/Subscription. | metric, quantity, period, source event. | 12,400 pages scanned. |
| **Billing Accounts** | Legal billing profile. | Belongs to Organization; has invoices/payments. | legal name, address, tax ID, billing email, currency. | Acme UK billing entity. |
| **Invoices** | Provider-synchronized invoice record. | Belongs to Billing Account/Subscription; has lines/payments. | number, status, subtotal, tax, total, currency, due time. | INV-2026-0042. |
| **Invoice Lines** | Itemized charge or credit. | Belongs to Invoice. | description, quantity, unit amount, tax, period. | 10 additional users. |
| **Payments** | Payment lifecycle record. | Belongs to Invoice/Billing Account. | amount, currency, status, provider reference, received time. | Card payment succeeded. |
| **Refunds / Credits** | Money returned or credited. | Belongs to Payment/Invoice. | amount, reason, status, provider reference. | Service credit of USD 20. |
| **Stripe Customers** | Minimal mapping to Stripe, not a duplicate customer profile. | One-to-one with Billing Account or Organization. | Stripe customer ID, livemode, synchronized time. | `cus_...`. |
| **Tax Registrations** | Jurisdiction-specific billing tax data. | Belongs to Billing Account and Country. | type, value encrypted/masked, verification status. | VAT registration. |

### Migration of Existing Data

The current `Opportunity` rows should first be preserved in a legacy-compatible schema. Introduce a bootstrap organization and map all existing workflow fields to private `OrganizationOpportunity` and `Lead` records. Convert current source records to platform-global sources unless ownership is known. Preserve source URLs, timestamps, notes, status, match reason, and score.

Before migration:

- Resolve the mismatch between active embedded lead fields and the separate `Lead` model.
- Resolve incomplete `back_populates` declarations before importing separate lead/proposal tables.
- Define one canonical discovery implementation and retire duplicates after parity tests.
- Fix the current transport-contract mismatch between `FilteredOpportunity` and the object constructed by the classifier before using it as a migration baseline.
- Snapshot and reconcile actual database schema against models; do not infer production state from migration scripts alone.

Every conversion must be repeatable, measured, and reversible. Store migration mapping tables and row counts, validate checksums/samples, and never delete legacy columns until at least one stable release has read from the new model.

## Development Roadmap

### Phase 0 — Baseline, Tests, and Architecture Guardrails

- **Goal:** Make current behavior measurable without changing product scope.
- **Estimated complexity:** Medium.
- **Dependencies:** None.
- **Risks:** Existing behavior may already differ between SQLite and PostgreSQL; scanner contracts contain inconsistencies; live websites are unstable test dependencies.
- **Files likely to change:** `requirements.txt`, `app/main.py`, `app/scanner.py`, `app/filters.py`, scanner modules, and new `tests/`, configuration, and architecture-decision records.
- **Database impact:** None; introspection only.
- **Testing strategy:** Characterization tests for routes, filters, URL normalization, parsing fixtures, saving/deduplication, and SQLite/PostgreSQL behavior. Use recorded sanitized HTML fixtures, never live production sources in CI.
- **Deployment strategy:** Ship tests and observability with no schema change; use staging smoke tests.
- **Rollback strategy:** Revert test/instrumentation-only release; no data rollback.

### Phase 1 — Migration Framework and Modular Monolith Foundation

- **Goal:** Introduce migrations, configuration validation, routers, services, repositories, request-scoped sessions, and CI quality gates while retaining Jinja behavior.
- **Estimated complexity:** High.
- **Dependencies:** Phase 0 characterization coverage.
- **Risks:** Moving logic may change transaction or scheduler behavior; model metadata may not match deployed schema.
- **Files likely to change:** `app/database.py`, `app/main.py`, models, migration scripts, new `app/core/`, `app/modules/`, and migration directory; GitHub workflows.
- **Database impact:** Establish a baseline migration and schema-version table; no destructive conversion.
- **Testing strategy:** Migration upgrade/downgrade tests on fresh and copied sanitized databases; route parity; transaction tests; static typing and linting.
- **Deployment strategy:** Apply baseline in staging, verify schema fingerprint, then perform a backward-compatible production release.
- **Rollback strategy:** Keep old entry points as adapters; roll application back without reverting additive schema.

### Phase 2 — Tenant and Authentication Foundation

- **Goal:** Add organizations, users, memberships, sessions, invitations, RBAC, tenant context, and audit logs; place all existing data in a bootstrap tenant.
- **Estimated complexity:** Very high.
- **Dependencies:** Phase 1 migrations and service boundaries; selected OIDC provider.
- **Risks:** Any missed tenant predicate is a critical breach; account-linking mistakes; lockout during migration.
- **Files likely to change:** New identity/tenancy/audit modules, authentication middleware, all routes and repositories, templates/navigation, configuration, tests.
- **Database impact:** Add identity/access tables and `organization_id` to private tables; backfill bootstrap tenant; add composite constraints and PostgreSQL RLS policies.
- **Testing strategy:** Cross-tenant negative matrix, role/permission tests, session/CSRF tests, invitation abuse tests, RLS tests in PostgreSQL, SQLite scope tests.
- **Deployment strategy:** Dark-launch tenant columns and dual checks, migrate data, verify counts, then require authentication behind a feature flag.
- **Rollback strategy:** Retain bootstrap-tenant compatibility and reversible auth flag; never remove tenant keys after data begins.

### Phase 3 — Global Catalog and Tenant Configuration

- **Goal:** Replace hard-coded Rwanda, language, category, service, currency, and keyword behavior with catalogs and tenant settings.
- **Estimated complexity:** High.
- **Dependencies:** Phase 2 tenant identity and permissions.
- **Risks:** Poor taxonomy governance, ambiguous synonyms, and configuration that silently reduces matches.
- **Files likely to change:** Catalog/settings/profile modules, admin APIs, onboarding UI, `app/filters.py`, discovery queries, source configuration.
- **Database impact:** Add country, region, language, currency, sector, service, type, keyword, and tenant mapping tables; seed global ISO reference data through migrations or versioned import jobs.
- **Testing strategy:** Locale/taxonomy tests, configuration validation, default-profile parity with current rules, tenant-specific divergence tests.
- **Deployment strategy:** Create a default profile reproducing current behavior; read new config in shadow mode before switching.
- **Rollback strategy:** Feature flag back to legacy classifier; preserve all profile data.

### Phase 4 — Durable Scanning and Ingestion Platform

- **Goal:** Consolidate discovery paths and move scans to queued, idempotent workers with adapters, artifacts, job history, rate limits, and source policies.
- **Estimated complexity:** Very high.
- **Dependencies:** Phases 1–3, queue, worker runtime, object storage.
- **Risks:** Website blocking, legal/terms restrictions, runaway costs, duplicates, poisoned content, worker overload.
- **Files likely to change:** `app/scanner.py`, `app/scanners/`, `app/discovery/`, `app/source_discovery.py`, schedulers/jobs, GitHub workflow responsibilities, new worker/connector modules.
- **Database impact:** Add endpoints, connector versions, jobs/runs, artifacts, candidates, errors, dedup decisions, and source-health history.
- **Testing strategy:** Contract tests per adapter, recorded fixtures, retry/idempotency tests, robots/rate-limit tests, malformed-document security tests, load and queue-failure tests.
- **Deployment strategy:** Shadow workers ingest without publishing; compare yields and hashes; enable sources/countries gradually.
- **Rollback strategy:** Pause queues and route schedules to the legacy scanner; retain immutable artifacts for replay.

### Phase 5 — Canonical Opportunities and Configurable Matching

- **Goal:** Separate canonical public opportunities from private tenant matches and replace fixed scoring with versioned rule sets and explanations.
- **Estimated complexity:** Very high.
- **Dependencies:** Configurable catalogs and durable ingestion.
- **Risks:** Incorrect deduplication merges notices; scoring drift; notification floods; data migration ambiguity.
- **Files likely to change:** Opportunity, matching, scoring, search, and notification-event modules; dashboard/detail/pipeline routes and templates.
- **Database impact:** Add canonical versions, lots, locations, classifications, `organization_opportunities`, rule sets, evaluations, contributions, and feedback; migrate current rows.
- **Testing strategy:** Golden scoring corpus, property tests for rule engine, deterministic replay, dedup precision/recall review, cross-tenant score separation.
- **Deployment strategy:** Dual-write legacy and new structures, compare results, then read new structures per tenant under feature flag.
- **Rollback strategy:** Switch reads back to legacy tables; preserve dual-written new data and replay events after correction.

### Phase 6 — CRM Pipeline and Proposal Management

- **Goal:** Deliver configurable leads, stages, tasks, activities, go/no-go, proposal workflows, reviews, and submissions.
- **Estimated complexity:** High.
- **Dependencies:** Private organization-opportunity model, RBAC, audit.
- **Risks:** Workflow customization becomes unbounded; embedded legacy lead fields conflict; concurrent edits lose work.
- **Files likely to change:** `app/main.py`, `app/models.py`, `app/lead_models.py`, `app/proposal_models.py`, route/templates, new pipeline/proposal modules.
- **Database impact:** Add normalized lead/proposal/stage/task/activity tables; migrate embedded lead fields; add optimistic-lock versions.
- **Testing strategy:** State-machine tests, permission tests, concurrency tests, migration reconciliation, deadline/timezone tests.
- **Deployment strategy:** Offer new workspace to pilot tenants; migrate one tenant at a time; keep legacy views read-only during cutover.
- **Rollback strategy:** Re-enable legacy view from compatibility projection; do not discard normalized workflow records.

### Phase 7 — Documents, People, Knowledge, Compliance, and Risk

- **Goal:** Support reusable qualifications and controlled proposal content with secure document storage and compliance workflows.
- **Estimated complexity:** Very high.
- **Dependencies:** Tenant auth, proposals, object storage, malware scanning.
- **Risks:** Sensitive document leakage, excessive retention, malware, incorrect access inheritance, privacy obligations.
- **Files likely to change:** New documents/people/knowledge/compliance modules, proposal workspace, object-storage adapter, worker tasks.
- **Database impact:** Add document/version/attachment, consultant/skill/certificate/experience, knowledge, risk, and compliance entities.
- **Testing strategy:** Object authorization, signed-URL expiry, malware quarantine, retention/legal-hold, document-version integrity, permission inheritance.
- **Deployment strategy:** Start with restricted pilot and conservative file limits; enable OCR/knowledge features separately.
- **Rollback strategy:** Disable uploads/processors while retaining encrypted objects and metadata; restore prior version pointers.

### Phase 8 — Notifications and Integrations

- **Goal:** Provide in-app/email notifications, digests, calendars, APIs, signed webhooks, and customer productivity integrations.
- **Estimated complexity:** High.
- **Dependencies:** Domain events/outbox, RBAC, stable API contracts.
- **Risks:** Duplicate delivery, provider outages, secret leakage, webhook SSRF, excessive messaging.
- **Files likely to change:** Notification/integration modules, settings UI, API routers, worker queues, provider adapters.
- **Database impact:** Add preferences, alerts, templates, attempts, integrations, API keys, webhooks, and sync cursors.
- **Testing strategy:** Provider contract fakes, signature verification, SSRF controls, idempotency, retry/dead-letter, quiet-hours/timezone tests.
- **Deployment strategy:** Sandbox providers and allowlisted pilot tenants; ramp event types and delivery volume.
- **Rollback strategy:** Disable provider/tenant integrations and drain or quarantine queues without losing event records.

### Phase 9 — Analytics, Dashboards, and Reporting

- **Goal:** Offer tenant-configurable operational and executive insight.
- **Estimated complexity:** High.
- **Dependencies:** Stable event and workflow data; tenant isolation.
- **Risks:** Expensive arbitrary queries, metric inconsistency, data leakage through aggregates/exports.
- **Files likely to change:** Reporting/query modules, dashboards UI, export workers, analytics storage adapters.
- **Database impact:** Add dashboards, widgets, reports, runs, metric snapshots, and possibly read models/materialized views.
- **Testing strategy:** Metric definition tests, authorization on every aggregate/export, query-cost limits, large export and timezone tests.
- **Deployment strategy:** Predefined reports first, then constrained builders; build read models asynchronously.
- **Rollback strategy:** Disable expensive widgets/builders; fall back to predefined transactional reports.

### Phase 10 — Commercial SaaS and Billing

- **Goal:** Enforce plans, trials, subscriptions, entitlements, metering, invoices, and payments.
- **Estimated complexity:** Very high.
- **Dependencies:** Organizations, audit, usage events, stable product entitlements.
- **Risks:** Billing errors, webhook ordering, tax complexity, accidental service cutoff, provider coupling.
- **Files likely to change:** Billing/entitlement modules, account UI, webhook endpoints, workers, plan configuration.
- **Database impact:** Add plans, prices, entitlements, subscriptions, usage, billing accounts, invoices, payments, refunds, and provider mappings.
- **Testing strategy:** Provider test clocks/sandbox, webhook replay and ordering, idempotency, proration, currency/tax, grace-period and entitlement tests.
- **Deployment strategy:** Start with manual invoicing/read-only provider sync, then enable self-service purchases by market.
- **Rollback strategy:** Fail open for already-paid access during billing outages, pause automated changes, reconcile from provider events.

### Phase 11 — Internationalization and Country Expansion

- **Goal:** Localize the product and operate configurable country/source packs worldwide.
- **Estimated complexity:** Very high and ongoing.
- **Dependencies:** Catalogs, adapter framework, localized templates, global search model.
- **Risks:** Local procurement-law differences, inaccurate translations, timezone/date errors, source licensing, data residency.
- **Files likely to change:** Locale resources, frontend components, parsers, country packs, catalog admin, notification templates.
- **Database impact:** Add localized labels/content, country-pack versions, jurisdiction policies, translation metadata.
- **Testing strategy:** Pseudolocalization, RTL, Unicode, locale formats, per-country fixture suites, legal/source acceptance checklists.
- **Deployment strategy:** Launch country packs through controlled readiness gates and local subject-matter review.
- **Rollback strategy:** Disable an individual country pack/source without affecting other markets.

### Phase 12 — Governed AI Assistance

- **Goal:** Add extraction, multilingual classification, summarization, semantic matching, risk review, and proposal assistance with citations and controls.
- **Estimated complexity:** Very high.
- **Dependencies:** Clean data, document security, feedback, stable workflows, AI governance.
- **Risks:** Hallucination, prompt injection from crawled content, data disclosure, cost, model drift, copyright and regulatory obligations.
- **Files likely to change:** New AI gateway/policy/evaluation modules, workers, document/matching/proposal UI, provider adapters.
- **Database impact:** Add conversations, messages, prompt versions, model runs, citations, feedback, evaluation datasets, and cost records.
- **Testing strategy:** Curated evaluation sets by language/country, red-team prompt injection, citation accuracy, PII leakage, deterministic schema validation, cost/latency budgets.
- **Deployment strategy:** Suggestion-only beta, per-feature opt-in, provider data-retention review, gradual model rollout with offline evaluation.
- **Rollback strategy:** Disable a model or AI feature independently and fall back to deterministic extraction/rules.

### Phase 13 — Enterprise Scale and Mobile

- **Goal:** Add regional placement, dedicated-tenant options, SSO/SCIM, advanced governance, and production mobile clients.
- **Estimated complexity:** Very high.
- **Dependencies:** Stable versioned API, entitlement model, observability, queue/search scale.
- **Risks:** Operational fragmentation, offline conflicts, regional consistency, complex enterprise support.
- **Files likely to change:** Tenant placement, identity provisioning, sync API, mobile project, deployment infrastructure.
- **Database impact:** Provisioning metadata, device/push tokens, sync cursors, conflict versions, regional routing.
- **Testing strategy:** Multi-region failure tests, SCIM conformance, mobile contract/offline sync, performance/chaos/security assessments.
- **Deployment strategy:** Enterprise and mobile pilots, staged stores/regions, explicit SLOs.
- **Rollback strategy:** Route tenants to last-known-good placement, disable incremental sync, retain compatible API versions.

## Coding Standards

- Target a documented Python version and lock dependencies reproducibly.
- Format and lint consistently; enforce in CI.
- Use strict type checking for domain and service layers.
- Use Pydantic schemas at external boundaries and typed domain objects internally.
- Keep routes thin: validate input, establish context, call a service, serialize output.
- Use explicit transactions in application services; avoid hidden commits inside repositories.
- Never call external services while holding a database transaction open.
- Use dependency injection for database sessions, clocks, identifiers, providers, queues, and storage.
- Name modules by business capability, not generic technical layers alone.
- Keep pure parsing/scoring logic free of database and network side effects.
- Use timezone-aware datetimes and `Decimal` for money.
- Validate configuration at startup without logging secrets.
- Use structured exceptions with safe public messages and internal diagnostic codes.
- Require docstrings for public contracts and comments for decisions, not restatements.
- Keep migrations additive and backward-compatible by default; use expand/migrate/contract.
- Store architectural decisions in ADRs.
- Require unit, integration, authorization, and migration tests proportionate to risk.
- Never make CI depend on live third-party websites.
- Apply dependency, secret, license, SAST, and container scans.
- Use conventional, reviewable commits only after explicit approval.

## API Standards

- Prefix public APIs with `/api/v1`; version breaking changes explicitly.
- Use nouns for resources and standard HTTP verbs/status codes.
- Use opaque IDs and never expose sequential identifiers where enumeration increases risk.
- Derive tenant from authentication; do not trust a client-supplied `organization_id`.
- Return a consistent error envelope containing `code`, `message`, `details`, and `request_id`, without internal stack traces.
- Use cursor pagination for large mutable collections and stable deterministic sorting.
- Support sparse filtering and documented search syntax; reject unbounded queries.
- Use ISO 8601 timestamps with offsets and explicit currency/language/country codes.
- Require idempotency keys for retry-prone creates, submissions, imports, and billing actions.
- Use optimistic concurrency with entity versions or ETags for collaborative writes.
- Publish OpenAPI contracts and generate client SDKs where valuable.
- Define deprecation headers and a supported-version policy.
- Apply per-user, per-tenant, per-key, and per-endpoint rate limits.
- Scope API keys and OAuth tokens to explicit permissions.
- Sign webhooks, timestamp them, prevent replay, and retry with exponential backoff.
- Version event names and payload schemas, for example `opportunity.matched.v1`.
- Treat file upload/download as separate secure workflows using short-lived signed URLs and validation.
- Keep internal worker commands behind authenticated queues, never public HTTP endpoints without strong controls.

## Security Standards

- Follow OWASP ASVS for the web application, OWASP API Security Top 10 for APIs, and a documented secure development lifecycle.
- Enforce tenant isolation in application services, repositories, PostgreSQL RLS, caches, search, object storage, workers, exports, and analytics.
- Encrypt transport with TLS and sensitive storage with managed encryption keys.
- Store secrets only in an approved secret manager; rotate and scope them.
- Hash passwords with a current memory-hard algorithm only if passwords are managed locally.
- Hash API keys and session tokens; display secrets only once.
- Require MFA and step-up authentication for sensitive roles/actions.
- Protect web forms with CSRF defenses; apply secure cookies, CSP, HSTS, frame restrictions, and safe referrer policy.
- Validate, normalize, and encode all untrusted data. Sanitize any rich text.
- Defend crawlers and webhooks against SSRF: block private/link-local/metadata networks, re-resolve DNS, restrict redirects, validate schemes, and limit response sizes.
- Scan uploads for malware; quarantine until approved; disallow active content by default.
- Treat crawled documents as hostile prompt input and hostile file content.
- Apply egress controls and source-specific rate limits to workers.
- Use least-privilege database roles: web, worker, migration, reporting, and support roles should differ.
- Audit authentication, authorization changes, exports, secrets, billing, impersonation, and destructive actions.
- Make audit logs tamper-evident and restrict deletion.
- Implement backups, point-in-time recovery, restore drills, incident response, breach procedures, and disaster-recovery objectives.
- Support retention, tenant export, account closure, deletion, legal holds, consent, and applicable privacy regulation.
- Perform threat modeling before authentication, tenancy, documents, billing, AI, and integration releases.
- Commission independent penetration testing before general availability and after major security architecture changes.

## Scalability

### Application

Keep web instances stateless. Store sessions, jobs, artifacts, and state in managed services. Scale web and worker pools independently. Separate queues by workload and priority so a large crawl cannot delay interactive notifications or billing events.

### Database

- Index tenant-owned access paths with `organization_id` first where appropriate.
- Use connection pooling with bounded per-instance pools.
- Eliminate N+1 queries and set query timeouts.
- Use read models/materialized views for expensive dashboards.
- Partition very large append-only tables such as audit logs, scan runs, artifacts, deliveries, AI runs, and events by time and possibly tenant placement.
- Archive cold artifacts and logs according to retention policy.
- Add read replicas only for workloads that tolerate replica lag.
- Introduce tenant sharding/dedicated databases through the placement abstraction when measured load requires it.

### Scanning

Schedule fairly across tenants and sources with quotas and backpressure. Use idempotency keys, leases, heartbeats, bounded retries, circuit breakers, per-domain concurrency, and dead-letter queues. Cache robots policies carefully and honor source terms. Store content hashes to avoid parsing unchanged artifacts.

### Search

Begin with PostgreSQL full-text and trigram indexes. Add a dedicated multilingual search engine when relevance, faceting, vector/semantic retrieval, or volume demands it. Every indexed private document must contain an enforced tenant filter; test search isolation independently.

### Reliability and SLOs

Define service-level objectives for API availability, opportunity freshness, job completion, notification delay, and recovery. Measure queue age, ingestion yield, parser errors, source health, matching latency, database saturation, external-provider errors, cost per opportunity, and tenant-specific limits.

## AI Features

Potential governed AI capabilities include:

- Multilingual opportunity classification and opportunity-type detection.
- Structured extraction of buyers, lots, deadlines, budgets, eligibility, submission instructions, and evaluation criteria.
- Semantic tenant-to-opportunity matching alongside deterministic rules.
- Plain-language, cited match explanations.
- Opportunity summaries with links to source passages.
- Translation with source text preserved and confidence shown.
- Duplicate detection and change summarization.
- Go/no-go recommendations based on tenant policy, with explicit evidence and human decision.
- Eligibility gap analysis against certificates, skills, experience, and compliance records.
- Risk identification and mitigation suggestions.
- Proposal outlines, compliance matrices, work plans, and responsibility matrices.
- Retrieval-assisted drafting from an approved tenant knowledge base.
- Resume/consultant matching and compliant CV generation.
- Tender Q&A over authorized documents with page-level citations.
- Deadline and obligation extraction into tasks and calendars.
- Source quality and parser anomaly detection.
- Natural-language saved-search and report creation with confirmed structured output.
- Win/loss analysis and scoring-rule tuning recommendations.
- Conversational product assistance scoped to the active tenant and permissions.

All AI features require tenant-controlled enablement, approved data-use policy, redaction where needed, prompt/model versioning, citations, schema validation, cost limits, human review, feedback, evaluation datasets, and deterministic fallback. Customer data must not be used to train shared models without explicit contractual consent.

## Integrations

Prioritize integrations according to customer demand:

- Identity: Microsoft Entra ID, Google Workspace, Okta, generic OIDC, SAML, SCIM.
- Collaboration: Microsoft Teams, Slack.
- Email and calendar: Microsoft 365/Outlook, Gmail/Google Calendar.
- Documents: SharePoint, OneDrive, Google Drive, Box, Dropbox, S3-compatible stores.
- CRM: Salesforce, HubSpot, Dynamics 365.
- Work management: Jira, Asana, Monday.com, Trello.
- Communications: transactional email, SMS, WhatsApp Business where lawful, mobile push.
- Billing: Stripe first, with an adapter for regional payment providers and manual invoicing.
- Procurement/content: country e-procurement APIs, TED, UNGM, development-bank portals, grants databases, RSS/Atom, licensed data feeds.
- Search and translation: configurable search providers and approved translation providers.
- BI/data: Power BI, Tableau, Looker, secure warehouse exports.
- E-signature: DocuSign, Adobe Acrobat Sign.
- Accounting: QuickBooks, Xero, Sage, and market-specific systems when justified.
- Developer platform: REST API, outbound webhooks, service accounts, and eventually event streaming for enterprise customers.

Every integration needs scoped permissions, encrypted credentials, provider-specific rate limiting, audit logs, health status, disconnect/revocation, idempotent sync, and tenant-isolation tests.

## Commercial SaaS

Plans should be driven by entitlements rather than conditionals scattered through code.

Suggested packaging:

- **Free / Trial:** One organization, few users, limited profiles/sources, daily digest, short history, no premium integrations.
- **Starter:** Small teams, more profiles and countries, basic pipeline, email alerts, standard reports.
- **Professional:** Larger teams, configurable scoring, proposals, documents, calendars, integrations, API access, longer retention, AI allowance.
- **Business:** Departments, advanced permissions, custom branding, approval workflows, advanced analytics, higher scan/API limits.
- **Enterprise:** SSO/SAML, SCIM, audit export, custom retention/data region, dedicated capacity or database, contractual SLO, premium support.
- **Platform / Partner:** Multi-organization management for associations or consulting networks, subject to a separate delegated-administration security model.

Meter carefully selected cost drivers: active users, search profiles, monitored private sources, fetched pages, stored documents, AI tokens/runs, API calls, and notification volume. Avoid unpredictable pricing based on noisy crawler behavior without caps and visibility.

Support monthly/annual billing, trials, grace periods, coupons/credits, manual enterprise contracts, multiple currencies, tax handling, invoice downloads, usage dashboards, and safe plan downgrades. Entitlement failures should degrade gracefully and never delete customer data.

## Internationalization

- Externalize all user-facing strings from Python, templates, emails, PDFs, and notifications.
- Use BCP 47 locale tags and locale fallback, not country flags as language selectors.
- Store source language and translated variants separately.
- Support Unicode normalization, pluralization, grammatical variables, and right-to-left layouts.
- Format dates, times, numbers, names, addresses, and currencies by locale.
- Store instants in UTC while preserving the relevant IANA timezone and original source date text.
- Allow each user to choose interface locale; allow each organization to choose defaults and supported content languages.
- Version and review translations; use professional review for legal, billing, security, and procurement terminology.
- Add pseudolocalization, long-string, RTL, Unicode, and locale snapshot tests.
- Ensure search, stemming, synonyms, OCR, and AI evaluation are language-aware.
- Never assume English keywords, Latin scripts, Western name order, or two-decimal currencies.

## Country Support

Country coverage should be delivered as versioned **country packs**, not conditionals spread through scanners.

A country pack should declare:

- Country and supported languages.
- Trusted government/private portals and licensed feeds.
- Search-provider parameters and localized discovery queries.
- Opportunity-type terminology and abbreviations.
- Date, timezone, number, currency, and address conventions.
- Procurement identifiers and buyer registries.
- Source adapters and parser fixtures.
- Relevant legal/terms-of-use and retention constraints.
- Expected update cadence and health thresholds.
- Local taxonomy mappings and translation glossary.
- Readiness owner, review date, and quality metrics.

Generic HTML/RSS/API adapters should be reused, while site-specific adapters remain isolated plugins with versioned contracts. A source can cover one country, several countries, or global opportunities. Opportunity country should be determined from explicit fields and evidence, not only domain suffix.

Launch gates for a country should include legal/source review, representative fixtures, parser accuracy, deduplication quality, deadline timezone validation, language quality, monitoring, and a rollback switch. Local experts should be able to curate sources and mappings without editing core application code.

## Future Mobile Application

Build mobile only after the versioned API, authentication, authorization, notification, and sync contracts are stable. Use OAuth 2.1 authorization code with PKCE, secure OS credential storage, device registration, and remote session revocation.

Initial mobile scope:

- Opportunity inbox, search, filters, and match explanations.
- Save/reject/pursue decisions.
- Lead status, assignment, comments, and tasks.
- Deadline calendar and push notifications.
- Secure document preview and limited upload.
- Approval actions with step-up authentication.

Use an offline-tolerant read cache for selected records, explicit synchronization cursors, optimistic concurrency, and conflict resolution. Tenant switching must clear or partition all local caches and push registrations. Highly sensitive documents should not be retained offline by default. Mobile APIs must remain the same authorized domain services used by web clients.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Missed tenant filter or RLS bypass | Critical cross-tenant disclosure | Mandatory context, RLS, composite keys, repository rules, negative tests, security review. |
| Legacy schema differs from model/scripts | Data loss or failed migration | Schema inventory, baseline migration, backups, rehearsal on sanitized copies, expand/migrate/contract. |
| Embedded and separate lead/proposal models conflict | Duplicate or inconsistent workflow state | Choose canonical target, reconciliation report, dual-read period, mapping table. |
| Current classifier contract mismatch | Scanner failures and unreliable baseline | Characterization tests and contract correction before architectural migration. |
| Hard-coded Rwanda/English/company assumptions | Incorrect global results and poor adoption | Catalogs, country packs, localized fixtures, configurable profiles. |
| Duplicate discovery/scheduling paths | Duplicate work and inconsistent state | Consolidate behind one job/connector interface; idempotency and distributed locks. |
| Direct schema changes at runtime | Production drift and unsafe releases | Versioned migrations only; deny DDL to runtime roles. |
| CI runners directly access production data | Credential and network exposure | Private worker scheduler, scoped service identities, no general CI database access. |
| Scraping legality and site blocking | Legal, reputational, and availability risk | Source policy registry, terms review, robots compliance, rate limits, licensed feeds. |
| Bad deduplication | Lost notices or duplicated work | Immutable candidates, explainable decisions, reversible merges, review queue. |
| Notification floods | Customer dissatisfaction and provider suspension | Thresholds, digests, quotas, deduplication, quiet hours, staged rollout. |
| AI hallucination or prompt injection | Wrong decisions or data disclosure | Retrieval citations, hostile-input isolation, output schemas, human approval, red-team tests. |
| AI/provider data retention | Confidentiality or regulatory breach | Provider review, no-training terms, redaction, regional options, tenant opt-in. |
| Arbitrary configurable rules become unsafe/slow | Outages or confusing scores | Validated rule DSL, execution limits, versioning, simulation, no arbitrary code. |
| Search/cache/object storage omit tenant scope | Cross-tenant leakage outside SQL | Tenant-prefixed keys, filtered indexes, signed paths, dedicated isolation tests. |
| Billing event disorder or duplicates | Incorrect charges/access | Provider as payment authority, idempotent event ledger, reconciliation, grace periods. |
| International date/currency ambiguity | Missed deadlines or incorrect values | Preserve source text/timezone, confidence, locale parsing, review warnings. |
| Unbounded document storage and exports | Cost, privacy, and availability issues | Quotas, retention, asynchronous exports, size/type limits, lifecycle policies. |
| Premature microservices | Delivery slowdown and operational fragility | Modular monolith first; extract only measured scaling boundaries. |
| SQLite masks PostgreSQL behavior | Production-only defects | Dual-database CI, PostgreSQL migration/RLS tests, documented SQLite limitations. |
| Insufficient observability | Silent stale data and slow incident response | SLOs, structured telemetry, source freshness/yield alerts, job dashboards. |
| Country-pack quality varies | Uneven product reliability | Readiness gates, local review, per-pack metrics, independent disable switch. |

## Recommended Development Order

1. Freeze and document the current runtime behavior with characterization tests and representative scanner fixtures.
2. Reconcile model/transport inconsistencies and inventory the real SQLite and PostgreSQL schemas without altering production data.
3. Introduce a migration framework, CI quality gates, structured configuration, and application/request service boundaries.
4. Establish PostgreSQL integration testing while retaining supported SQLite local tests.
5. Add organizations, users, memberships, sessions, roles, permissions, tenant context, audit events, and bootstrap-tenant migration.
6. Enforce tenant ownership in repositories, composite keys, PostgreSQL RLS, caches, workers, files, and negative security tests.
7. Create global international catalogs and tenant configuration for countries, languages, currencies, sectors, services, types, keywords, and buyers.
8. Create search/scanning profiles and reproduce the current behavior as one default Rwanda profile rather than hard-coded logic.
9. Consolidate source discovery and scheduling into one queued, observable, idempotent ingestion platform.
10. Introduce canonical opportunities, immutable versions, artifacts, deduplication decisions, and tenant-private organization-opportunity matches.
11. Implement versioned configurable scoring rules, contribution explanations, eligibility checks, feedback, and replay.
12. Normalize the lead pipeline, tasks, activities, and proposal workflow; migrate existing embedded lead data safely.
13. Add secure documents, consultants, skills, certificates, knowledge, compliance, and risk modules.
14. Add notification preferences, digests, calendars, integrations, scoped API keys, and signed webhooks.
15. Add dashboards, governed reports, exports, and operational analytics.
16. Add entitlements, plans, metering, billing accounts, subscriptions, invoices, and payments.
17. Internationalize the UI and communications, then launch additional versioned country packs through readiness gates.
18. Add governed AI features one at a time, beginning with cited extraction and summarization and retaining deterministic fallbacks.
19. Introduce enterprise placement, SSO/SCIM, data-region controls, and dedicated capacity only after shared tenancy is proven.
20. Build mobile clients against the stable versioned API and tenant-safe synchronization model.

## Architecture Acceptance Criteria

The architecture is ready for general multi-tenant use only when:

- Every private resource has a documented owner and tenant boundary.
- Automated tests prove users cannot read or mutate another tenant's data through SQL, API, search, cache, files, exports, jobs, or integrations.
- Authentication, authorization, audit, session security, and incident procedures have been independently reviewed.
- Schema changes are migration-driven, rehearsed, observable, and reversible.
- Scanner work is isolated from web requests and production CI credentials.
- A tenant can configure countries, languages, sectors, services, keywords, exclusions, preferred buyers, opportunity types, currencies, scoring, users, departments, branding, notifications, and integrations without code changes.
- At least two materially different tenant profiles and two country packs operate concurrently without configuration leakage.
- Current opportunity discovery, dashboard, detail, and pipeline behavior has an intentional migration or compatibility path.
- Production has tested backups, restore procedures, monitoring, SLOs, rate limits, and failure runbooks.
- Billing and AI features can be disabled independently without making core opportunity workflows unavailable.

# Optional GroqCloud / NVIDIA prototype API fallback

This integration is **disabled unless individually configured**. The app
keeps Gemini as its primary model. On a verified Gemini quota error or 503
outage, it attempts enabled community providers in this order: GroqCloud,
NVIDIA NIM, then the existing paid OpenAI API fallback if explicitly enabled.

This is not a way to use a ChatGPT subscription as API credits. Groq and
NVIDIA require separate provider accounts and keys.

## Evidence and sizing (November 18, 2026)

From GitHub Actions log timestamps, the highest observed rolling 60-second
window was 42 AI requests/min during November 17's failing scan. November
16's healthy scanner reached 24 requests/min. Neither is a guaranteed future
maximum. Successful processing rates are lower because analysis takes time.

Large requests can include 75,000 characters of opportunity content or
50,000 characters of publisher content, plus classification instructions,
JSON schemas, and response tokens. Matching and bid triage add company
profiles and structured analyses. Those are not exact token measurements.

Conservative planning scenarios (input **plus output**):

| Scenario | Estimated tokens per request | 24 RPM | 42 RPM |
| --- | ---: | ---: | ---: |
| Short classification | 2,000 | 48,000 TPM | 84,000 TPM |
| Typical classification | 4,000 | 96,000 TPM | 168,000 TPM |
| Longer evidence analysis | 8,000 | 192,000 TPM | 336,000 TPM |
| Long tender documents | 20,000+ | 480,000+ TPM | 840,000+ TPM |

Those values are **rough planning estimates**, not measured token totals.
The application now logs actual Gemini and community API token counts when
providers return them, without recording prompts or keys.

Official Groq base free limits for `openai/gpt-oss-120b` as checked:
30 RPM, 8,000 TPM, 1,000 RPD, 200,000 TPD. See
https://console.groq.com/docs/rate-limits.
The app defaults to 25 RPM, 7,000 TPM, 900 RPD, 180,000 TPD.
At 4,000 tokens per request this free plan can handle at most roughly
50 requests per day by TPD, so it **cannot cover all daily scans**.

For NVIDIA NIM, the defaults of 32 RPM and 40,000 TPM are deliberately
chosen *local safety caps*, **not verified account entitlements**.
The available prototype model defaults to `openai/gpt-oss-20b` as shown on
https://build.nvidia.com/openai/gpt-oss-20b.
Free prototyping is for testing, not a guaranteed ongoing production SLA.
Confirm the individual account's quota, model permissions, and license/terms.

## Privacy safety

By default, community provider calls are restricted to public
`SourceAnalysis` and `OpportunityAnalysis` classification.
Company profiles, matching, triage, and readiness contain private company
data and **never go to free/evaluation APIs** unless
`COMMUNITY_API_ALLOW_COMPANY_DATA=true` is explicitly chosen after checking
the provider's privacy and trial terms. Do not enable this setting without
reviewing those terms, particularly for NVIDIA's prototype service, which
may log or retain API inputs/outputs.

## Activate on GitHub Actions

In **GitHub repository > Settings > Secrets and variables > Actions**:

* Add secret `GROQ_API_KEY` from https://console.groq.com/keys
* Add optional secret `NVIDIA_API_KEY` from https://build.nvidia.com
* Set repository variable `GROQ_FALLBACK_ENABLED=true` to opt in
* Set repository variable `NVIDIA_FALLBACK_ENABLED=true` to opt in separately

The workflow will forward configured keys/variables. Without keys nothing
is sent to those providers and all prior behavior is preserved. Enabling
the flags starts transmitting eligible public notice content to the provider;
review the associated data handling terms first.

Optional variable overrides (use only with confirmed account limits):
`GROQ_FALLBACK_RPM`, `GROQ_FALLBACK_TPM`, `GROQ_FALLBACK_RPD`,
`GROQ_FALLBACK_TPD`, and equivalent `NVIDIA_FALLBACK_*` variables.
Model overrides: `GROQ_FALLBACK_MODEL`, `NVIDIA_FALLBACK_MODEL`.
These are local safety budgets, not provider-guaranteed capacity.

## Activate on Render (optional)

Set the matching secret keys and flags in the Render service's Environment
settings if the website's interactive Analyze flow should use community
fallback. The scheduled job's secrets are separate from Render's settings.

## Behavior and limits

* Each provider independently enforces rolling 60-second RPM/TPM and rolling
  24-hour RPD/TPD budgets, **within one Python process**.
* Token reservations include source text, instructions, schema, and maximum
  output tokens. They conservatively approximate 1 token per 3 characters,
  with extra completion budget. Actual usage is logged when returned.
* A too-large request is skipped; evidence is **not truncated** just to fit.
* Provider 429 errors activate a 15-minute local pause, while 503 outages
  pause for five minutes. The OpenAI backup now pauses for 15 minutes on 429,
  preventing hundreds of repeated failed calls.
* Unfinished site checkpoints remain due for a later recovery attempt;
  no unverified result is inserted into the qualified feed.
* Budgets reset when a job/process restarts, so enforce account-wide
  restrictions in provider dashboards as well; don't run overlapping jobs.
* The NVIDIA adapter does not assume the selected model supports
  JSON-schema mode; it requests JSON in instructions and validates the
  returned object against the same Pydantic schema before acceptance.
* This change does not guarantee an unlimited or permanently free service.

## Tests

`python -m pytest -q tests/test_free_api_fallback.py tests/test_openai_fallback.py`

Tests mock all provider requests and incur no token charges. For real
end-to-end verification you must supply your own provider keys/limits.


## Balanced hourly production schedule (Nov 18, 2026)

Opportunity Radar now alternates which of Groq and NVIDIA gets first priority
for each eligible **public** source/notice classification. If one provider
hits RPM/TPM/RPD/TPD, or returns 429/503, the next configured provider is
attempted. Gemini handles documents that neither free provider can analyze;
OpenAI is **last resort only**, with model ID `gpt-6-luna`.

The production GitHub Actions schedules use Africa/Kigali local time:

- Website scanning: at minute 00 of every hour, up to **16** due sites per
  run, prioritizing previously unscanned sites and rotating unfinished sites
  fairly. The separate Job in Rwanda board runs at 08:00 only.
- Website discovery: at minute 35 of every second hour, dividing the 49
  configured Brave searches into 12 deterministic groups of about 4-5
  queries. Every search is covered once per Kigali day.
- Shared GitHub Actions concurrency ensures scan and discovery do not race.
- A successful website updates its checkpoint; incomplete AI processing
  remains eligible for another hourly scan.

**Persistent budget:** the existing PostgreSQL `beta_usage` table tracks
minute request counts/tokens and UTC daily request counts/tokens per provider.
Counters persist across GitHub runs and Render web instances, and are
updated atomically with an advisory transaction lock. In-process rolling
counters add additional pacing. Reservations count maximum output tokens
conservatively, including unsuccessful requests; quota measurements returned
by providers are logged without private request text. This avoids treating
every hourly job as a fresh free allocation. It is not a guarantee that the
provider's exact rate-limit algorithm matches our conservative counters.

Groq and NVIDIA are automatically eligible in scheduled GitHub Actions
**only when their corresponding secret is present**, and their enable
variables are either true or left unset. Explicit `false` disables them.
The provider-specific rate caps remain configurable but must not exceed
the account's actual granted limits.

Changing the scheduled GitHub Actions timing does **not** bypass daily
quotas. The number of sites crawled in 24 hours is still subject to provider
capacity and the AI quality safeguards. Never expect free NVIDIA prototype
credits or a free-tier Groq allocation to cover unlimited high-volume
production.

All qualification decisions, company match thresholds, and privacy
constraints are unchanged.

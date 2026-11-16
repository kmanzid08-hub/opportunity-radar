# Optional OpenAI API backup for Gemini quota or temporary service outage

This is a *server-side API integration*, not a ChatGPT login. A paid ChatGPT
subscription does not automatically supply API credentials or API capacity to
a custom website. Some eligible Pro and Business customers can activate
linked API credits through their ChatGPT billing settings.

The backup is **OFF by default** and makes **zero OpenAI API requests**
until all required values are configured.

## Behavior

- Gemini remains the primary provider for source assessment, notice extraction,
  company matching, triage, and structured evaluation.
- An explicitly confirmed Gemini `QUOTA_EXCEEDED` response (429) or temporary
  Gemini `MODEL_UNAVAILABLE` outage (503) can trigger the OpenAI API.
  Ordinary errors, authentication failures, and generic 429 rate limits do
  **not** trigger paid fallback.
- While the backup is enabled, a process-wide circuit breaker suppresses
  repeated Gemini calls for 60 minutes after quota exhaustion or 5 minutes
  after a temporary 503 outage. Gemini is retried after the cooldown.
  The circuit resets at process restart. With fallback disabled, Gemini's
  existing bounded retry behavior is unchanged.
- The backup uses the same existing Pydantic model and validation policies.
  It also uses the existing per-workspace AI request budget for interactive users.
- OpenAI receives the relevant prompt, including the notice and any company
  profile content included in that prompt, **only when fallback runs**.
- Any OpenAI API usage is accounted for separately from ChatGPT subscriptions
  unless eligible linked API credits cover the charges. Check the OpenAI API
  platform Usage and Billing dashboards to control spending.

## GitHub Actions setup (scheduled source discovery and daily scanning)

1. Create an OpenAI API project and API key at
   https://platform.openai.com/api-keys (confirm API billing or credits first).
2. In the repository's **Settings > Secrets and variables > Actions > Secrets**,
   create the repository secret named `OPENAI_API_KEY`.
3. In **Settings > Secrets and variables > Actions > Variables**, create the
   repository variable named `OPENAI_FALLBACK_ENABLED` with the value `true`.
4. Optional: create a repository variable `OPENAI_FALLBACK_MODEL` with
   `gpt-5-mini` or another structured-output capable model available to your
   API project.

## Render setup (interactive Analyze and other website actions)

In the Render service **Environment** configuration add:

- `OPENAI_API_KEY`: your API project key, marked as a secret
- `OPENAI_FALLBACK_ENABLED`: `true`
- `OPENAI_FALLBACK_MODEL`: `gpt-5-mini` (optional)

Save the environment configuration and deploy. The API key must never be
committed to GitHub or included in logs or screenshots.

## Incomplete website recovery

After a scheduled scan, a website is considered incomplete when its most recent
scan start time is later than its last completed scan time. A dedicated recovery
run can set `SOURCE_SCAN_RECOVERY_ONLY=true` to revisit only those active,
approved websites (at most 50, started within the past 72 hours), without
rescanning job boards or deleting previous successful findings. If any AI
verification or matching still fails, that website remains eligible for another
recovery attempt. The regular daily scan remains unchanged.

## Test and troubleshooting

Run `python -m pytest -q tests/test_openai_fallback.py`. These tests
mock API calls and do not incur provider charges.

- Without `OPENAI_FALLBACK_ENABLED=true`, no backup requests occur even when
  the API key exists.
- Without `OPENAI_API_KEY`, the backup remains inactive even when enabled.
- If both providers fail, the existing verified-data safeguards remain in
  place; the app must not invent results to populate the Discover feed.
- Do not configure billing or enable the backup until you have reviewed the
  OpenAI API costs and the data being sent to the backup provider.

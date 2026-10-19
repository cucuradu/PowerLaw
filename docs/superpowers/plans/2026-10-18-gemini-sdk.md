# Gemini SDK migration

Scope: use `google-genai` and `gemini-3.8-flash` with Vertex AI ADC. The user's
latest instruction replaces API-key authentication because service-account key
creation is restricted. Configure project `project-05d7ff31-502b-4fe3-8ff` and
location `global` through the environment, following live verification below. Keep existing text, JSON,
embedding and PDF helper signatures. Do not edit the ingestion/store work being
handled in another session.

One user-authorized Vertex API-key request was blocked with HTTP 403. The user
subsequently installed gcloud and completed ADC login and quota-project setup.
Live verification on 2026-10-18 refreshed ADC successfully. An inherited
`gemini-2.5-flash-lite` setting caused an initial configuration rejection; an
explicit `gemini-3.8-flash` request returned HTTP 404 in `us-central1`, then
succeeded on `global`, replying `OK` with 4 input tokens and 1 output token.
Only the successful request generated output; retries were disabled.
The Cloud Billing API is disabled, so the billing link and trial-credit deduction
remain unverified. Do not exhaust quotas to test billing.
Safe results: `data/reports/gemini-adc-connection-check.json`.

1. Add mocked regression tests in `tests/test_api_wrapper.py` for lazy client
   creation, missing/blank project settings, missing ADC, text/JSON output, 768-dimensional
   embeddings, PDF calls, and permanent versus transient errors. Confirm failure.
2. Migrate `src/utils/api_wrapper.py`, the two model-list scripts, the extraction
   schema example, news key checks and `requirements.txt`. Preserve schema
   constraints and existing return shapes. Remove legacy SDK use.
3. Update `.env.example` and README with PowerShell installation, session and
   persistent environment setup, and a helper example. Run focused and caller
   regressions. Check imports without the legacy package, including a real SDK
   client with mocked HTTP to prove project routing and absence of API-key headers.
   Report live inference and account-specific model availability as unverified
   unless tested after ADC setup.

Rollback: revert only this migration's edits and restore the old project-local
dependency. Existing credential values and stored data remain untouched. Only
public project/location/model settings are added to the private environment;
ADC tokens remain managed by gcloud outside the repository.

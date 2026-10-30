# Gemini through Vertex AI: owner setup (Workload Identity Federation)

Written 2026-10-30 10:50 CT, lane GEMINI-VERTEX-1030. Code is on branch `revenue-1030b/gemini-vertex`, not merged.

What this does: with the repository variable `GEMINI_BACKEND` set to `vertex`, the daily collector asks Gemini
through Vertex AI (billed to the Google Cloud project, so GCP credits apply if the credit covers Vertex AI) instead
of through the AI Studio API keys. The model ids are the same two as today (`gemini-3.6-flash` for the 20-question
continuity panel, `gemini-3.5-flash-lite` for everything else). No JSON key is created: GitHub proves the workflow's
identity with an OIDC token and Google exchanges it for a short-lived token. With the variable unset, nothing
changes.

Run the commands below in Cloud Shell or any shell signed in as a project owner. Nothing here needs a secret value.
Replace the four values first.

```bash
export PROJECT_ID="your-project-id"
export PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
export REPO="mrcrtr1979-droid/namebeam-visibility-index"      # owner/name, case-sensitive
export SA_NAME="namebeam-gemini"
export POOL="github-namebeam"
export PROVIDER="github-oidc"
export SA="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
```

## 1. Enable the APIs

```bash
gcloud services enable aiplatform.googleapis.com iam.googleapis.com iamcredentials.googleapis.com \
  --project="$PROJECT_ID"
```

`aiplatform.googleapis.com` is Vertex AI. `iamcredentials.googleapis.com` is used when the workflow's federated
identity impersonates the service account.

## 2. Service account with one role

```bash
gcloud iam service-accounts create "$SA_NAME" --project="$PROJECT_ID" \
  --display-name="Namebeam collector, Gemini on Vertex AI"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA}" --role="roles/aiplatform.user"
```

`roles/aiplatform.user` is the only role granted to it. Do not give it Owner, Editor or any storage role.

## 3. Workload identity pool and GitHub OIDC provider, locked to this repo

```bash
gcloud iam workload-identity-pools create "$POOL" \
  --project="$PROJECT_ID" --location="global" \
  --display-name="Namebeam GitHub Actions"

gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
  --project="$PROJECT_ID" --location="global" \
  --workload-identity-pool="$POOL" \
  --display-name="GitHub OIDC" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
  --attribute-condition="assertion.repository == '${REPO}'"
```

The attribute condition is what restricts the provider to this one repository: a token from any other repository
is rejected before it reaches the service account. Google rejects a GitHub provider created without a condition
(`INVALID_ARGUMENT: The attribute condition must reference one of the provider's claims`). Optional tighter form,
if you want only runs from `main` (the schedule and manual runs both use `main`):
`assertion.repository == '${REPO}' && assertion.ref == 'refs/heads/main'`.

## 4. Let that repository act as the service account

```bash
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --project="$PROJECT_ID" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPO}"
```

The member uses the project NUMBER, not the project id. The `attribute.repository/owner/name` part is
case-sensitive with no trailing slash.

## 5. Read the provider name for the repo variable

```bash
gcloud iam workload-identity-pools providers describe "$PROVIDER" \
  --project="$PROJECT_ID" --location="global" --workload-identity-pool="$POOL" \
  --format="value(name)"
```

It prints `projects/<number>/locations/global/workloadIdentityPools/<pool>/providers/<provider>`. That whole string
is `WIF_PROVIDER`.

## 6. One-call check before switching (uses your own sign-in, writes nothing)

This proves the model id and location work on your project. It costs a fraction of a cent. Try `global` first,
then `us-central1` if global answers with an error.

```bash
TOKEN="$(gcloud auth print-access-token)"
for MODEL in gemini-3.6-flash gemini-3.5-flash-lite; do
  curl -sS -X POST "https://aiplatform.googleapis.com/v1/projects/${PROJECT_ID}/locations/global/publishers/google/models/${MODEL}:generateContent" \
    -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
    -d '{"contents":[{"role":"user","parts":[{"text":"Reply with the word OK."}]}]}' | head -c 400; echo
done
# regional form, if global fails:  https://us-central1-aiplatform.googleapis.com/v1/projects/${PROJECT_ID}/locations/us-central1/publishers/google/models/${MODEL}:generateContent
```

A JSON body with `candidates` means that model answers in that location. A 404 means the model id is not served
there: try the other location, and if both return 404, stop and report it before setting the variables.

## 7. Set the repository variables

GitHub: Settings, Secrets and variables, Actions, tab Variables (not Secrets). Or with the GitHub CLI:

```bash
gh variable set GCP_PROJECT     --repo "$REPO" --body "$PROJECT_ID"
gh variable set GCP_LOCATION    --repo "$REPO" --body "global"        # or us-central1, whichever step 6 answered on
gh variable set WIF_PROVIDER    --repo "$REPO" --body "<string from step 5>"
gh variable set WIF_SA          --repo "$REPO" --body "$SA"
gh variable set GEMINI_BACKEND  --repo "$REPO" --body "vertex"        # set this one last: it is the switch
```

These are names and ids, not credentials. Repository variables are not masked in workflow logs, and this repository
is public, so the project id and provider path will be readable there. The attribute condition in step 3 is what
protects the project, not secrecy of these values.

## 8. Confirm on the first run after the switch

The next scheduled run (12:10Z) or a manual run uses Vertex. In `corpus/e1/`, any Gemini row written after the
switch should carry, inside `engine_meta`: `"rail": "vertex"` and `"method": "gemini via vertex"`:

```bash
grep -l '"method": "gemini via vertex"' corpus/e1/NB-CZ-API_<date>_*_gemini.json | wc -l
```

A row answered by the API-key rails after the switch has no `method` key and `"rail": "studio_free"` or
`"studio_paid"`. If Vertex refused the call first (quota, billing or permission, HTTP 402, 403 or 429), that row also
carries `"vertex_refused_http": <status>`. A GitHub annotation `GEMINI_BACKEND=vertex but the Vertex rail is
unavailable` in the run log means the credentials did not load (check steps 3 to 5 and the variable values).

Billing: in Cloud Billing, Credits, confirm the credit's scope includes Vertex AI, and set a budget alert. I did not
verify which credit programs cover Vertex AI Gemini calls.

## 9. Switch back

Delete or blank the `GEMINI_BACKEND` variable. The next run is on the AI Studio keys again, with no code change.
The AI Studio secrets (`GEMINI_API_KEY_FREE`, `GEMINI_API_KEY`) are not removed by any step here and stay behind the
Vertex rail as a fallthrough on quota or billing refusals.

## What the code does and does not change

- Same model ids and same request body as the API-key path, plus `"role": "user"` on the content turn for Vertex.
  No tools are sent on either path (the collector switches no search or grounding tool on for Gemini), so there is
  no grounding setting to translate. If grounding is ever added, check the Vertex tool field name then; I could not
  read that part of Google's page on 2026-10-30.
- Pacing is unchanged: requests to a model stay under the old free-tier per-minute limits (5 per minute for
  `gemini-3.6-flash`, 14 for `gemini-3.5-flash-lite`). Vertex allows more; the limits can be raised in
  `GEMINI_MODEL_RPM` in `scripts/engines.py` as a separate, dated change.
- `GCP_LOCATION` accepts `global` (host `aiplatform.googleapis.com`) or a region such as `us-central1`
  (host `us-central1-aiplatform.googleapis.com`). Unset means `us-central1`.
- Only `e1-check-runner.yml` is wired. `client_check.yml` and the other workflows are unchanged and stay on the
  API keys.

## CHANGELOG line draft (not dated, not merged; goes at the top of scripts/CHANGELOG.md when merged)

```
- <MERGE DATE>: Optional Vertex AI backend for Gemini. With the repository variable GEMINI_BACKEND set to "vertex", scripts/engines.py asks Gemini through Vertex AI on Workload Identity Federation (no key) before the AI Studio keys; same two model ids (gemini-3.6-flash on the 20-question continuity panel, gemini-3.5-flash-lite otherwise), same question, no tools. Rows answered through Vertex carry engine_meta.method "gemini via vertex" and engine_meta.rail "vertex"; a row the AI Studio keys answer after a refused Vertex call carries engine_meta.vertex_refused_http. With the variable unset, nothing changes and no row gains a field. Effective for collection runs dated <FIRST VERTEX RUN DATE> and later; set that date from the first corpus row that carries the method field. Edition 1 (through 2026-10-13) is unchanged. Test: python3 scripts/test_gemini_vertex.py. Setup: docs/VERTEX_WIF_SETUP.md.
```

## Sources, all read 2026-10-30

- Authenticate to Google Cloud (google-github-actions/auth) README, pinned commit
  https://github.com/google-github-actions/auth/blob/43a59886fcb7545b65158bdc879d7b29e7f3ab05/README.md
  Used for the pool, provider and `roles/iam.workloadIdentityUser` binding commands, the `workload_identity_provider`
  and `service_account` inputs, `id-token: write`, and the export of `GOOGLE_APPLICATION_CREDENTIALS`. The commit I
  read shows `@v1` in its example and omits `--attribute-condition`; this repo's workflow pins `@v2` as the lane brief
  asked, and the condition comes from the next two sources.
- Google Developer forum thread, create-oidc INVALID_ARGUMENT for a GitHub provider without an attribute condition
  https://discuss.google.dev/t/github-and-workload-identity-pools-providers-create-oidc-invalid-argument-error/167426
- Third-party walkthrough (condition, project-number principalSet, case-sensitive repo path)
  https://computingforgeeks.com/gcp-workload-identity-federation-github-actions/
- Vertex AI locations page, regional and global endpoint URL forms; `gemini-3.6-flash` and `gemini-3.5-flash-lite`
  are listed under Global
  https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/locations
  (Google now redirects Vertex AI docs to a "Gemini Enterprise Agent Platform" path; the API host and the
  `aiplatform` role and service names used above are unchanged.)
- Fetched but not readable past the navigation, so not relied on: the Vertex AI grounding, access-control and
  generateContent reference pages.

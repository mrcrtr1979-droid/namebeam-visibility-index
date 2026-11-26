# Collector recovery job

Build from the repository root after the reliability PR is merged:
`docker build -f deploy/cloudrun/Dockerfile -t COLLECTOR_IMAGE .`
Tag the image for your private Artifact Registry repository and push it. The
runtime identity needs access to the four named Secret Manager secrets and
object creation on a private archive bucket. The scheduler identity needs
Cloud Run Invoker on this job. Use distinct runtime and scheduler identities.

1. Fill the image, private bucket, and runtime identity in job.yaml. Create
   the job using `gcloud run jobs replace deploy/cloudrun/job.yaml --region REGION`.
   The manifest attaches OPENAI_API_KEY, ANTHROPIC_API_KEY, PERPLEXITY_API_KEY
   and GEMINI_API_KEY_FREE. BRIGHTDATA_API_KEY is optional for SERP coverage;
   attach it separately if authorized. The runtime uses the attached service
   identity for upload.
2. Open Cloud Run > Jobs > namebeam-collector > Execute. Confirm one task
   completes and an execution-specific archive appears in the private bucket.
   The job collects and archives only. The existing privacy-reviewed
   publication process remains a separate step. Keep these archives private.
3. If switching off the Actions schedule, create the replacement scheduler:
   `gcloud scheduler jobs create http namebeam-collector-daily --location REGION --schedule '0 13 * * *' --time-zone Etc/UTC --uri 'https://run.googleapis.com/v2/projects/PROJECT/locations/REGION/jobs/namebeam-collector:run' --http-method POST --oauth-service-account-email SCHEDULER_SERVICE_ACCOUNT --oauth-token-scope https://www.googleapis.com/auth/cloud-platform`.

For a dependency-only startup test, run the image with `--dry-run` and set
each required environment variable to a non-secret dummy value. The same
command without those variables must exit 2. Dry-run never starts collection.

The 2026-11-24 reliability report estimates $1.73 in model calls for a full
design day, versus $1.34 observed on a partial day. Cloud Run, build, registry
and storage charges are separate and unverified. Avoid overlapping the two
schedules.

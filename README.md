# claude-vault

Automatically backs up all Claude.ai conversations and projects to GitHub as markdown files.
Runs on a schedule, auto-refreshes session when it expires, and skips creating
duplicate snapshots when content is unchanged.

Default backup target:
- `GITHUB_REPO=m3h3di/claude-vault-backups`
- `GITHUB_BRANCH=main`
- `BACKUP_FOLDER=backups`

## Deploy on Google Cloud Run

Cloud Run service is not the right fit for this repo because `scheduler.py` is a long-running loop and Cloud Run services are designed to handle HTTP requests. For Google Cloud, deploy `backup.py` as a **Cloud Run Job** and trigger it on a schedule with **Cloud Scheduler**.

### 1. Build and push the container

```bash
export PROJECT_ID="your-gcp-project"
export PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
export REGION="us-central1"
export REPO="claude-vault"
export IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/claude-vault:latest"

gcloud config set project "$PROJECT_ID"
gcloud artifacts repositories create "$REPO" \
  --repository-format=docker \
  --location="$REGION"

gcloud builds submit --tag "$IMAGE"
```

### 2. Create secrets

Store these values in Secret Manager:

- `CLAUDE_SESSION`
- `CLAUDE_EMAIL`
- `CLAUDE_PASSWORD`
- `GITHUB_TOKEN`
- `GITHUB_REPO`

```bash
printf '%s' 'your-session-key' | gcloud secrets create CLAUDE_SESSION --data-file=-
printf '%s' 'you@example.com' | gcloud secrets create CLAUDE_EMAIL --data-file=-
printf '%s' 'your-password' | gcloud secrets create CLAUDE_PASSWORD --data-file=-
printf '%s' 'ghp_xxx' | gcloud secrets create GITHUB_TOKEN --data-file=-
printf '%s' 'm3h3di/claude-vault-backups' | gcloud secrets create GITHUB_REPO --data-file=-
```

### 3. Create the Cloud Run Job

```bash
gcloud run jobs create claude-vault-backup \
  --image "$IMAGE" \
  --region "$REGION" \
  --max-retries 1 \
  --task-timeout 3600s \
  --set-env-vars GITHUB_BRANCH=main,BACKUP_FOLDER=backups \
  --set-secrets CLAUDE_SESSION=CLAUDE_SESSION:latest,CLAUDE_EMAIL=CLAUDE_EMAIL:latest,CLAUDE_PASSWORD=CLAUDE_PASSWORD:latest,GITHUB_TOKEN=GITHUB_TOKEN:latest,GITHUB_REPO=GITHUB_REPO:latest
```

Run it once manually:

```bash
gcloud run jobs execute claude-vault-backup --region "$REGION"
```

### 4. Schedule it

Create a service account for Cloud Scheduler, then grant it permission to run the job:

```bash
gcloud iam service-accounts create claude-vault-scheduler

gcloud run jobs add-iam-policy-binding claude-vault-backup \
  --region "$REGION" \
  --member="serviceAccount:claude-vault-scheduler@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/run.invoker"
```

Then create the hourly schedule:

```bash
gcloud scheduler jobs create http claude-vault-hourly \
  --location "$REGION" \
  --schedule "0 * * * *" \
  --http-method POST \
  --uri "https://run.googleapis.com/v2/projects/$PROJECT_ID/locations/$REGION/jobs/claude-vault-backup:run" \
  --oauth-service-account-email "claude-vault-scheduler@$PROJECT_ID.iam.gserviceaccount.com"
```

## Quick start (local)

```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env    # fill in your values
python scheduler.py
```

## Quick start (GitHub Actions - no local machine needed)

1. Push this repo to GitHub
2. Add secrets in Settings -> Secrets -> Actions:
   - CLAUDE_SESSION, CLAUDE_EMAIL, CLAUDE_PASSWORD
   - BACKUP_GITHUB_TOKEN, GITHUB_REPO
3. The workflow runs automatically every hour

## What gets backed up

- Standalone chats       -> backups/chats/
- Project index + docs   -> backups/projects/<name>/index.md
- Project conversations  -> backups/projects/<name>/
- Backup state metadata  -> backups/.state.json

## Auto session refresh

When sessionKey expires, the script logs back in headlessly
using CLAUDE_EMAIL + CLAUDE_PASSWORD. No manual steps needed.

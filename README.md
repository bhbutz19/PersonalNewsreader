# Personal Newsreader — Cloud Ingestion V1

Cloud-first ingestion stack:

- **Render Cron Job** — executes the collector every 30 minutes
- **Supabase PostgreSQL** — persistent cloud database
- **GitHub** — source of truth for code/config
- **Gmail API** — next ingestion adapter
- **BOOX/PWA** — eventual reading interface

## What is already implemented

- RSS/Atom ingestion
- normalized Postgres schema
- duplicate-safe item insertion
- source registry
- source health tracking
- ingestion-run audit table
- Render Blueprint
- cloud-safe environment-variable configuration

## One-time account setup

### 1. Create a Supabase project

Create a new Supabase project, then open **Connect**.

Copy the **Transaction pooler** PostgreSQL URI.

It looks roughly like:

`postgresql://postgres.PROJECT_REF:PASSWORD@...pooler.supabase.com:6543/postgres`

Do not commit this value to GitHub.

### 2. Create a GitHub repository

Suggested repository name:

`PersonalNewsreader`

Upload/commit all files in this folder to the repository root.

### 3. Deploy Render Blueprint

In Render:

1. New > Blueprint
2. Connect the `PersonalNewsreader` GitHub repository.
3. Render reads `render.yaml`.
4. When prompted for `DATABASE_URL`, paste the Supabase Transaction Pooler URI.
5. Apply the Blueprint.

The collector is configured for:

`*/30 * * * *`

Render cron schedules are UTC, but every-30-minutes does not require timezone conversion.

### 4. Trigger the first run

In Render:

`personal-newsreader-ingest > Runs > Trigger Run`

Expected log shape:

`OK popville: ...`
`OK dc_council_calendar: ...`
`...`
`DONE run=1 ...`

### 5. Inspect source health

Locally, with `DATABASE_URL` exported:

```bash
pip install -r requirements.txt
python health.py
python recent.py 50
```

You can also view tables directly in the Supabase Table Editor.

## Security

Secrets belong only in Render/Supabase environment configuration.

Never commit:
- `DATABASE_URL`
- Gmail OAuth credentials
- API keys

`.env` is gitignored.

## Database tables

### sources
Registry and health state for every input.

### items
Normalized source items from RSS now, Gmail later.

### ingestion_runs
Audit trail of every scheduled collection run.

## Current enabled RSS sources

- PoPville
- DC Council Calendar
- Green Bay Packers official news
- UWM Campus & Community
- El País España

Other planned sources are registered but disabled until their adapters/feed URLs are validated.

## Next code milestone

After the first cloud run succeeds:

1. Validate/add Brewers RSS.
2. Validate/add Formula 1 RSS.
3. Add more DC Council feeds.
4. Add UWM sports feed.
5. Add federal-politics custom GovInfo feeds.
6. Enable selected Reddit feeds after rate validation.
7. Add Gmail ingestion into the same `items` table.
8. Add Taste Guide scoring and story clustering.

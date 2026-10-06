CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    beat TEXT NOT NULL,
    source_type TEXT NOT NULL,
    url TEXT NOT NULL,
    priority TEXT,
    editorial_type TEXT,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    last_checked_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_error TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS items (
    id BIGSERIAL PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE,
    source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    source_name TEXT NOT NULL,
    beat TEXT NOT NULL,
    editorial_type TEXT,
    title TEXT NOT NULL,
    url TEXT,
    author TEXT,
    published_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    summary TEXT,
    raw_id TEXT,
    language TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_items_published_at ON items(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_fetched_at ON items(fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_beat ON items(beat);
CREATE INDEX IF NOT EXISTS idx_items_source_id ON items(source_id);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running',
    sources_attempted INTEGER NOT NULL DEFAULT 0,
    sources_succeeded INTEGER NOT NULL DEFAULT 0,
    sources_failed INTEGER NOT NULL DEFAULT 0,
    items_parsed INTEGER NOT NULL DEFAULT 0,
    items_inserted INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

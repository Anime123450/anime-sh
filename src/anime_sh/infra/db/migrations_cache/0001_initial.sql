-- Cache store (cache.db): disposable. Wiping this is always safe.

-- Merged, per-provider episode lists. Cached ~24h.
CREATE TABLE IF NOT EXISTS episode_cache (
    anilist_id  INTEGER NOT NULL,
    provider    TEXT    NOT NULL,
    number      REAL    NOT NULL,
    episode_key TEXT    NOT NULL,
    title       TEXT,
    aired_at    TEXT,
    fetched_at  TEXT    NOT NULL,
    PRIMARY KEY (anilist_id, provider, number)
);

-- Generic TTL key/value: search results, trending, seasonal, candidate lists.
-- NB: resolved Stream URLs are IP/time-bound and are NOT stored here; only the
-- candidate list is cacheable.
CREATE TABLE IF NOT EXISTS kv_cache (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_kv_expires ON kv_cache (expires_at);

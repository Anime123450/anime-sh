-- User store (anime.db): sacred, never auto-expired.
-- Numbered migrations from commit #1; applied in filename order.

-- Identity spine. Cached AniList metadata lives here too so history/favorites
-- render even when every provider and the network are down.
CREATE TABLE IF NOT EXISTS anime (
    anilist_id     INTEGER PRIMARY KEY,
    mal_id         INTEGER,
    title_romaji   TEXT,
    title_english  TEXT,
    title_native   TEXT,
    format         TEXT,
    status         TEXT,
    episodes       INTEGER,
    season         TEXT,
    year           INTEGER,
    cover_url      TEXT,
    synopsis       TEXT,
    genres_json    TEXT,
    fetched_at     TEXT
);

-- The mapping that makes cross-provider fan-out a dict lookup.
CREATE TABLE IF NOT EXISTS provider_map (
    anilist_id   INTEGER NOT NULL,
    provider     TEXT    NOT NULL,
    audio        TEXT    NOT NULL,
    anime_key    TEXT    NOT NULL,
    confidence   REAL    NOT NULL DEFAULT 1.0,
    verified_at  TEXT,
    PRIMARY KEY (anilist_id, provider, audio)
);

CREATE TABLE IF NOT EXISTS progress (
    anilist_id  INTEGER NOT NULL,
    episode     REAL    NOT NULL,
    position_s  INTEGER NOT NULL,
    duration_s  INTEGER NOT NULL,
    completed   INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT    NOT NULL,
    PRIMARY KEY (anilist_id, episode)
);

CREATE INDEX IF NOT EXISTS idx_progress_updated ON progress (updated_at DESC);

CREATE TABLE IF NOT EXISTS favorites (
    anilist_id  INTEGER PRIMARY KEY,
    added_at    TEXT    NOT NULL,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    anilist_id      INTEGER NOT NULL,
    episode         REAL    NOT NULL,
    watched_at      TEXT    NOT NULL,
    provider        TEXT,
    seconds_watched INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS downloads (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    anilist_id   INTEGER NOT NULL,
    episode      REAL    NOT NULL,
    path         TEXT,
    status       TEXT    NOT NULL,
    bytes_total  INTEGER,
    bytes_done   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL
);

-- Circuit-breaker state persists across restarts so a dead provider stays
-- deprioritised without re-paying its timeout on every launch.
CREATE TABLE IF NOT EXISTS provider_health (
    provider             TEXT PRIMARY KEY,
    state                TEXT NOT NULL DEFAULT 'closed',
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    opened_at            TEXT,
    p50_latency_ms       INTEGER,
    success_rate_7d      REAL
);

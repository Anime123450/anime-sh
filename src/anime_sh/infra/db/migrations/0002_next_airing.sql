-- Cache the airing schedule alongside the rest of the metadata.
--
-- Without this, a Continue Watching row painted from the cache has no idea when
-- the next episode lands, so a show you're caught up on renders as
-- "up next · Ep N" — telling you an unreleased episode is ready to watch — until
-- a live AniList fetch arrives to correct it (and it stays wrong when that fetch
-- fails or you're offline).

ALTER TABLE anime ADD COLUMN next_airing_episode INTEGER;
ALTER TABLE anime ADD COLUMN next_airing_at TEXT;

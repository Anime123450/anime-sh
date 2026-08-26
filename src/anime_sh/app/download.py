"""DownloadService — resolve an episode's stream and save it to disk.

Reuses the exact same resolve path as playback (so downloads benefit from the
provider fan-out and resolver fallback), then hands the stream to a Downloader.
The show's metadata is cached so ``anime downloads`` renders titles offline.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Callable

from ..domain.models import Anime, Audio, DownloadStatus
from ..domain.ports import DownloadStore, Downloader, Library
from .playback import PlaybackService


class DownloadService:
    def __init__(
        self,
        *,
        playback: PlaybackService,
        downloader: Downloader,
        store: DownloadStore,
        library: Library,
        download_dir: str = "~/Videos/anime",
        stream_proxy=None,
    ) -> None:
        self._playback = playback
        self._downloader = downloader
        self._store = store
        self._library = library
        self._dir = Path(download_dir).expanduser()
        self._stream_proxy = stream_proxy

    def available(self) -> bool:
        return self._downloader.available()

    def destination(self, anime: Anime, episode: float) -> Path:
        show = _safe(anime.title.preferred)
        return self._dir / show / f"{show} - E{episode:g}.mp4"

    def local_path(self, anime: Anime, episode: float) -> Path | None:
        """Where this episode already sits on disk, or None.

        The single authority for "do I have this one?". The download command
        skips on it and playback prefers it, and those two answering differently
        is how you get an episode that download calls finished and playback goes
        to the network for.
        """
        dest = self.destination(anime, episode)
        return dest if dest.is_file() else None

    async def download(
        self, anime: Anime, episode: float, *, audio: Audio = Audio.SUB,
        on_line: Callable[[str], None] | None = None,
    ) -> Path:
        resolved = await self._playback.resolve(anime, episode, audio=audio)
        stream = (
            self._stream_proxy.rewrite(resolved.stream)
            if self._stream_proxy else resolved.stream
        )
        await self._library.save_anime(anime)
        dest = self.destination(anime, episode)

        download_id = await self._store.add(anime.id, episode, str(dest))
        await self._store.set_status(download_id, DownloadStatus.DOWNLOADING)
        try:
            await self._downloader.download(stream, dest, on_line=on_line)
        except Exception:
            await self._store.set_status(download_id, DownloadStatus.FAILED)
            raise
        await self._store.set_status(download_id, DownloadStatus.DONE, path=str(dest))
        return dest

    async def history(self, *, limit: int = 50):
        return await self._store.list(limit=limit)


_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Names Windows refuses to create a file under, whatever the extension.
_RESERVED = {"con", "prn", "aux", "nul"} | {
    f"{stem}{i}" for stem in ("com", "lpt") for i in range(1, 10)
}

# Anime titles run long — a 96-character light-novel title already puts the
# default download path at 229 of Windows' 260-character limit, and the path is
# used twice (folder and file). A deeper downloads.dir tips it over, and the
# failure surfaces as a bare OSError mid-download.
_MAX_COMPONENT = 80


def _safe(name: str) -> str:
    """Filesystem-safe file/dir name, on every platform we target."""
    cleaned = _ILLEGAL.sub("", name).strip().rstrip(".")
    # A leading dash makes the name look like a flag to any tool we hand the
    # path to (ffmpeg reads a bare "-i" as an option, not a file).
    cleaned = cleaned.lstrip("-").strip()
    if len(cleaned) > _MAX_COMPONENT:
        # What distinguishes two seasons of a long-titled show is the *end* of
        # the title, which is precisely what truncation throws away — plain
        # truncation gave "…Season 2" and "…Season 3" the same folder AND file
        # name, so downloading one silently overwrote the other. Keep the length
        # bound, but make the result unique to the full title.
        digest = hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:6]
        head = cleaned[: _MAX_COMPONENT - len(digest) - 1].strip().rstrip(".")
        cleaned = f"{head}-{digest}"
    if cleaned.split(".")[0].lower() in _RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned or "anime"

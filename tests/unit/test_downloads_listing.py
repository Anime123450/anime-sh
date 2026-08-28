"""`anime downloads` describes the disk, not just the database.

The download table is written when an episode is fetched and never revisited, so
deleting a file to free space left the listing reporting it as `done` for ever —
exactly when you are trying to work out where your disk went. The row and the
file are two different facts and the listing has to show both.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from anime_sh.cli.main import _download_on_disk, _human_size


def _item(path):
    return SimpleNamespace(path=str(path) if path is not None else None,
                           created_at=datetime.now(timezone.utc))


def test_a_file_that_is_there_reports_its_size(tmp_path):
    f = tmp_path / "ep1.mp4"
    f.write_bytes(b"x" * 4096)
    assert _download_on_disk(_item(f)) == (True, 4096)


def test_a_deleted_file_is_reported_gone_not_done(tmp_path):
    """The regression case: the database still says this download finished."""
    assert _download_on_disk(_item(tmp_path / "never-existed.mp4")) == (False, 0)


def test_a_row_with_no_path_is_not_a_crash(tmp_path):
    """Queued and failed downloads have no path yet."""
    assert _download_on_disk(_item(None)) == (False, 0)


def test_an_unreadable_path_answers_rather_than_raising(tmp_path):
    """A disconnected drive or a permissions change must not take down the whole
    table — this is a listing, and one bad row is not worth losing the rest."""
    weird = tmp_path / "no\x00such"  # embedded NUL: stat raises ValueError/OSError
    try:
        assert _download_on_disk(_item(weird)) == (False, 0)
    except ValueError:  # pragma: no cover
        raise AssertionError("_download_on_disk let an OS error escape")


def test_sizes_read_the_way_a_person_expects():
    assert _human_size(0) == "0 B"
    assert _human_size(512) == "512 B"
    assert _human_size(4096) == "4 KB"
    assert _human_size(211_150_688) == "201.4 MB"
    assert _human_size(5 * 1024**3) == "5.0 GB"

"""AniList token persistence — a small JSON file in the config dir.

No OS keyring is bundled, so the token lives in a file we create with 0600
permissions where the platform supports it. Only the access token (and the
client id used to mint it) are stored — never a password.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ...config.paths import anilist_token_path


def load_token(path: Path | None = None) -> str | None:
    path = path or anilist_token_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    token = data.get("access_token")
    return token if isinstance(token, str) and token else None


def load_client_id(path: Path | None = None) -> str | None:
    path = path or anilist_token_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    cid = data.get("client_id")
    return str(cid) if cid else None


def save_token(token: str, *, client_id: str | None = None, path: Path | None = None) -> None:
    path = path or anilist_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"access_token": token}
    if client_id:
        payload["client_id"] = str(client_id)
    # Created 0600 *before* the token goes in, not tightened afterwards. Writing
    # first and chmod-ing second leaves a window — however brief — in which an
    # access token sits on disk readable by everyone on the machine, and the
    # whole point of the mode is that it never does.
    #
    # O_TRUNC keeps the mode an existing file already has, so the chmod below
    # still matters: it is what repairs a token file written by an older build.
    # On Windows the mode is close to meaningless either way; the file lives
    # under the user's AppData, which is what actually protects it there.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def clear_token(path: Path | None = None) -> bool:
    path = path or anilist_token_path()
    try:
        path.unlink()
        return True
    except OSError:
        return False

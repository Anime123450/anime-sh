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
    path.write_text(json.dumps(payload), encoding="utf-8")
    # Best-effort tighten permissions (POSIX); harmless/no-op on Windows.
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

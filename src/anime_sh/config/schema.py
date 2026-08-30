"""Typed config schema.

Precedence (highest first): CLI flag > env (``ANIME_SH_*``) > config file >
defaults. The CLI applies flags on top; env + defaults are handled here.
Secrets (AniList tokens) never live in this file — they go to the OS keyring.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PlayerConfig(BaseModel):
    name: str = "mpv"
    args: list[str] = Field(default_factory=list)


class PlaybackConfig(BaseModel):
    quality: str = "best"  # best | 1080p | 720p | 480p | worst
    audio: str = "sub"  # sub | dub
    auto_next: bool = True
    skip_intro: bool = True
    skip_outro: bool = False
    # Play an episode you have already downloaded from disk instead of fetching
    # it again. `anime play --stream` turns this off for one run, which is what
    # you want when the local copy is suspect.
    prefer_downloads: bool = True


class ProvidersConfig(BaseModel):
    # At least one: a zero or negative value silently sliced the provider
    # list down to nothing, so every playback attempt found no sources.
    parallel: int = Field(default=5, ge=1)
    # Provider order preference (highest first). Names not listed keep their
    # built-in priority, ordered after the preferred ones. Unknown names are
    # ignored, so this stays valid as providers come and go.
    preferred: list[str] = Field(
        default_factory=lambda: ["anizone", "anikoto"]
    )
    disabled: list[str] = Field(default_factory=list)
    timeout_s: float = Field(default=8.0, gt=0)


class ResolversConfig(BaseModel):
    disabled: list[str] = Field(default_factory=list)


class UiConfig(BaseModel):
    theme: str = "midnight"

    @field_validator("theme")
    @classmethod
    def _known_theme(cls, v: str) -> str:
        """Reject a theme name nothing will apply.

        The old code looked the value up in a dict and, on a miss, simply left
        the default theme in place — so `config set ui.theme drakula` reported
        success and changed nothing, which is indistinguishable from the setting
        not working. Validated here, the typo is caught where it is made.
        """
        from ..theme_names import ALL_THEMES

        if v not in ALL_THEMES:
            raise ValueError(
                f"unknown theme {v!r}; choose one of: " + ", ".join(ALL_THEMES)
            )
        return v


class DownloadsConfig(BaseModel):
    dir: str = "~/Videos/anime"


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ANIME_SH_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    player: PlayerConfig = Field(default_factory=PlayerConfig)
    playback: PlaybackConfig = Field(default_factory=PlaybackConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    resolvers: ResolversConfig = Field(default_factory=ResolversConfig)
    ui: UiConfig = Field(default_factory=UiConfig)
    downloads: DownloadsConfig = Field(default_factory=DownloadsConfig)

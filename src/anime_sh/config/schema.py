"""Typed config schema.

Precedence (highest first): CLI flag > env (``ANIME_SH_*``) > config file >
defaults. The CLI applies flags on top; env + defaults are handled here.
Secrets (AniList tokens) never live in this file — they go to the OS keyring.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
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


class ProvidersConfig(BaseModel):
    parallel: int = 5
    preferred: list[str] = Field(
        default_factory=lambda: ["animekai", "hianime", "animepahe", "allanime"]
    )
    disabled: list[str] = Field(default_factory=list)
    timeout_s: float = 8.0


class ResolversConfig(BaseModel):
    preferred_hosts: list[str] = Field(
        default_factory=lambda: [
            "vidstream",
            "filemoon",
            "streamwish",
            "mp4upload",
        ]
    )
    disabled: list[str] = Field(default_factory=list)


class UiConfig(BaseModel):
    theme: str = "tokyo-night"


class TrackingConfig(BaseModel):
    anilist: bool = False
    mal: bool = False
    discord_presence: bool = False


class DownloadsConfig(BaseModel):
    dir: str = "~/Videos/anime"
    concurrency: int = 2


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
    tracking: TrackingConfig = Field(default_factory=TrackingConfig)
    downloads: DownloadsConfig = Field(default_factory=DownloadsConfig)

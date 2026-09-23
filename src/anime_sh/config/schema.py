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
    #
    # Empty by default. It used to ship ["anizone", "anikoto"], which only
    # restated the built-in priorities — so the shipped order lived in two
    # places, and changing a provider's `priority` silently did nothing for
    # anyone on a default config. Worse, it pinned that order: when anikoto
    # stopped producing playable streams, demoting it left every default
    # install still trying it first. This knob is the user's; the built-in
    # priorities, maintained next to the provider code, are the default.
    preferred: list[str] = Field(default_factory=list)
    disabled: list[str] = Field(default_factory=list)
    #: How long one provider gets to list an episode's stream candidates.
    #: Separate from the match budget below because the two sit at different
    #: points: matching runs on every search, across providers in parallel,
    #: with someone waiting on it.
    timeout_s: float = Field(default=8.0, gt=0)
    #: How long one provider gets to match a title. Was hardcoded, so raising
    #: `timeout_s` on a slow connection still left matching cut off at four
    #: seconds - and matching is the step that has to succeed first.
    match_timeout_s: float = Field(default=4.0, gt=0)
    #: Override the host the hianime provider talks to. It runs behind a
    #: rotating set of mirrors and which of them answer depends on where you
    #: are, so "this domain stopped working for me today" should not need a new
    #: release. Empty means the provider's built-in default.
    hianime_base: str = ""


class ResolversConfig(BaseModel):
    disabled: list[str] = Field(default_factory=list)


class UiConfig(BaseModel):
    theme: str = "midnight"
    #: How the detail screen lays its episodes out. A grid suits a twelve-part
    #: season; a list suits someone who wants each episode's state spelled out;
    #: compact suits a series with four figures of them. None is right for
    #: everyone, which is the whole reason it is a setting.
    episodes: str = "grid"

    #: How tightly the home screen packs its sections. Every section spends
    #: four rows on chrome — the heading, the plate's padding top and bottom,
    #: and the gap to the next one — which on a 34-row laptop terminal is most
    #: of the screen before any content.
    density: str = "comfortable"

    @field_validator("density")
    @classmethod
    def _known_density(cls, v: str) -> str:
        from ..layout_names import DENSITIES

        if v not in DENSITIES:
            raise ValueError(
                f"unknown density {v!r}; choose one of: " + ", ".join(DENSITIES)
            )
        return v

    @field_validator("episodes")
    @classmethod
    def _known_layout(cls, v: str) -> str:
        from ..layout_names import EPISODE_LAYOUTS

        if v not in EPISODE_LAYOUTS:
            raise ValueError(
                f"unknown episode layout {v!r}; choose one of: "
                + ", ".join(EPISODE_LAYOUTS)
            )
        return v

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

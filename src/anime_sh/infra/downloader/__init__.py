"""Downloaders — turn a resolved Stream into a local file."""

from .ffmpeg import FfmpegDownloader, build_ffmpeg_command

__all__ = ["FfmpegDownloader", "build_ffmpeg_command"]

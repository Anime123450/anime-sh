# Security policy

## Supported versions

anime-sh ships from `master` and fixes land in the next release. Only the latest
version on PyPI is supported — upgrade with `uv tool upgrade anime-sh` before
reporting anything.

## Reporting a vulnerability

Use GitHub's **[private vulnerability
reporting](https://github.com/Anime123450/anime-sh/security/advisories/new)** —
not a public issue, and not a pull request that shows the exploit.

Include what an attacker controls, what they get, and the smallest reproduction
you have. Expect a first reply within a week; this is a one-person project, so
that is a realistic promise rather than an SLA.

## What is in scope

anime-sh is a client that runs on your machine, so the interesting boundary is
everything it accepts from the network and hands to something else:

- **Provider HTML and JavaScript.** Providers are scraped, and one of them
  serves deliberately obfuscated payloads that `resolvers/packed.py` and
  `infra/proxy/deobfuscate.py` unpack. Anything in that path that lets a
  provider reach beyond producing a stream URL is in scope.
- **Subprocess arguments.** `mpv` and `ffmpeg` are invoked with argument lists
  and never `shell=True`. A provider-controlled string that escapes an argument
  position — a leading `-` read as a flag, for instance — is in scope.
- **Generated filenames.** Download paths are built from titles that come from
  the network. Path traversal, Windows reserved device names, or a collision
  that silently overwrites a file outside the download directory are in scope.
- **The local HTTP proxy** used to de-obfuscate segmented streams: it binds
  loopback only, and anything that changes that is in scope.
- **The AniList token.** It lives in a JSON file in the config directory
  (`anime config path`), created `0600` where the platform supports it. Only an
  OAuth access token and the client id used to mint it are stored — never a
  password.

## What is not in scope

- **A provider being down, blocked, or Cloudflare-gated.** That is the normal
  operating state of this software, tracked by the nightly canary. File it as a
  `provider-broken` issue, not a vulnerability.
- **The absence of an OS keyring.** Known and documented above; a PR adding one
  behind an optional extra is welcome as a feature.
- **What the providers themselves host.** anime-sh bundles no media, mirrors
  nothing, and bypasses no DRM. Content complaints belong with the site that
  serves the content.

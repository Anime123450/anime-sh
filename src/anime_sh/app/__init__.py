"""Application services — orchestration only.

This package may import from `anime_sh.domain` but never from `anime_sh.infra`,
`providers`, or `resolvers`. It talks to the outside world exclusively through
the ports in `domain.ports`. `container.py` is the single place allowed to know
about concrete implementations, and it is wired from the composition root
(the CLI), not imported by services.
"""

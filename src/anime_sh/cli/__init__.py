"""CLI adapter + composition root.

The CLI is one interface onto the app services; it holds no domain logic. It is
also the composition root: `container.py` here is the single place allowed to
import both `app` services and `infra` concretes and wire them together.
"""

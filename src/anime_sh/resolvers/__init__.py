"""Bundled resolver plugins.

A resolver turns a :class:`StreamCandidate` (an embed/clock URL for some host)
into one or more playable :class:`Stream` objects. Resolvers know hosts, never
anime — so the same resolver serves any provider that yields that host.
"""

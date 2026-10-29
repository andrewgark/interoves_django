"""Gameplay moves independent of the HTTP request that carries them.

Checking and persisting an attempt lives here so that the send-attempt view
and, later, the offline sync endpoint (docs/offline-play-plan.md) share it.
"""

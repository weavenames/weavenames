"""Availability probes for individual registries.

Each module exposes an async function that takes a name and an httpx
AsyncClient and returns an AvailabilityResult. Orchestration lives in
`pipeline.py`.
"""

"""Tests for the interpretation layer (squatter detection, reserved names)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from weavenames.interpret import (
    looks_like_squatter_npm,
    looks_like_squatter_pypi,
    reinterpret_github_user,
    reinterpret_npm,
    reinterpret_pypi,
)
from weavenames.models import AvailabilityResult


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def test_pypi_squatter_detected_old_no_github():
    raw = {
        "name": "ghost",
        "summary": None,
        "home_page": None,
        "project_urls": {},
        "release_count": 1,
        "release_versions": ["0.0.1"],
        "latest_upload_time": _iso(datetime.now(timezone.utc) - timedelta(days=900)),
    }
    is_soft, _ = looks_like_squatter_pypi(raw)
    assert is_soft is True


def test_pypi_active_package_not_squatter():
    raw = {
        "name": "django",
        "summary": "real",
        "home_page": "https://github.com/django/django",
        "project_urls": {"Source": "https://github.com/django/django"},
        "release_count": 50,
        "release_versions": ["5.0", "5.1"],
        "latest_upload_time": _iso(datetime.now(timezone.utc) - timedelta(days=10)),
    }
    is_soft, _ = looks_like_squatter_pypi(raw)
    assert is_soft is False


def test_reinterpret_pypi_taken_to_soft_claimed():
    raw = {
        "release_count": 1,
        "home_page": None,
        "project_urls": {},
        "latest_upload_time": _iso(datetime.now(timezone.utc) - timedelta(days=900)),
    }
    r = AvailabilityResult(registry="pypi", name="ghost", status="taken", raw=raw)
    out = reinterpret_pypi(r)
    assert out.status == "soft_claimed"
    assert "squatter-like" in out.flags


def test_reinterpret_pypi_free_unchanged():
    r = AvailabilityResult(registry="pypi", name="aether", status="free")
    out = reinterpret_pypi(r)
    assert out.status == "free"


def test_npm_squatter_detected():
    raw = {
        "version_count": 1,
        "repository": None,
        "modified": _iso(datetime.now(timezone.utc) - timedelta(days=900)),
    }
    is_soft, _ = looks_like_squatter_npm(raw)
    assert is_soft is True


def test_reinterpret_npm_taken_to_soft_claimed():
    raw = {
        "version_count": 1,
        "repository": {"url": ""},
        "modified": _iso(datetime.now(timezone.utc) - timedelta(days=900)),
    }
    r = AvailabilityResult(registry="npm", name="ghost", status="taken", raw=raw)
    out = reinterpret_npm(r)
    assert out.status == "soft_claimed"


def test_reinterpret_github_user_reserved():
    r = AvailabilityResult(registry="github_user", name="admin", status="free")
    out = reinterpret_github_user(r)
    assert out.status == "reserved"
    assert any("reserved" in f.lower() for f in out.flags)


def test_reinterpret_github_user_pass():
    r = AvailabilityResult(registry="github_user", name="aether", status="free")
    out = reinterpret_github_user(r)
    assert out.status == "free"

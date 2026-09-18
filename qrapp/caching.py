"""
Caching helpers for qrapp.

Centralizes cache-key building and invalidation so views can cache expensive
aggregations (calendar, lookup dropdowns) without each one re-implementing key
naming rules. Cache backends are configured in qrproject/settings.py (Redis
when REDIS_URL is set, local memory otherwise).
"""
import hashlib
from datetime import date, timedelta

from django.conf import settings
from django.core.cache import caches

# Bump this string whenever cached-payload logic/format changes so all stale
# entries become unreachable immediately.
CACHE_VERSION = "v1"

# Cache lifetimes (seconds), tunable via environment (see settings.py).
CALENDAR_CACHE_TIMEOUT = int(getattr(settings, "CALENDAR_CACHE_TIMEOUT", 60))
LOOKUPS_CACHE_TIMEOUT = int(getattr(settings, "LOOKUPS_CACHE_TIMEOUT", 60))


def _hash(value):
    """Stable hash for cache-key parts (builtins hash() is salted per process)."""
    return hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:16]


def build_calendar_cache_key(day):
    """Key for the shared calendar payload (same for every user)."""
    return f"{CACHE_VERSION}:calendar:{day.isoformat()}"


def build_lookups_cache_key(endpoint, user_pk, params_str):
    """Key for per-user lookup AJAX payloads."""
    return f"{CACHE_VERSION}:lookups:{endpoint}:{user_pk}:{_hash(params_str)}"


def build_chart_cache_key(day):
    """Key for the dashboard 7-day attendance chart payload (shared)."""
    return f"{CACHE_VERSION}:chart:{day.isoformat()}"


def cache_get_json(key):
    """Read a JSON-able payload from the default cache; None on miss."""
    return cache.get(key)


def cache_set_json(key, value, timeout=None):
    """Store a JSON-able payload in the default cache."""
    cache.set(key, value, timeout=timeout)


def invalidate_calendar_cache():
    """
    Drop the shared calendar payload after event changes.

    The calendar is keyed by day and stored in the 'lookups' cache (same
    backend the dashboards read it from); today and tomorrow cover everything
    the dashboard renders (today's view + upcoming events).
    """
    lookups_cache = caches["lookups"]
    today = date.today()
    for day in (today, today + timedelta(days=1)):
        lookups_cache.delete(build_calendar_cache_key(day))


def invalidate_lookup_caches():
    """
    Lookup data (colleges/programs/majors/events) changed: drop the whole
    'lookups' cache bin.

    Uses delete_pattern when available (Redis backend); on LocMemCache we just
    clear that bin entirely - it only ever holds short-lived lookup payloads.
    """
    lookups_cache = caches["lookups"]
    if hasattr(lookups_cache, "delete_pattern"):
        lookups_cache.delete_pattern("*")
    else:
        lookups_cache.clear()

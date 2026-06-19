"""Database-backed reservation of Nominatim request slots."""

import time
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from api.models import GeocodingRateLimit


class DatabaseRateLimiter:
    def __init__(self, interval_seconds=None, *, clock=None, sleeper=None):
        self.interval_seconds = (
            settings.NOMINATIM_MIN_INTERVAL_SECONDS
            if interval_seconds is None
            else interval_seconds
        )
        self.clock = clock or timezone.now
        self.sleeper = sleeper or time.sleep

    def acquire(self):
        now = self.clock()
        with transaction.atomic():
            state = GeocodingRateLimit.objects.select_for_update().get(pk=1)
            reserved_at = max(now, state.next_allowed_at or now)
            state.next_allowed_at = reserved_at + timedelta(
                seconds=self.interval_seconds
            )
            state.save(update_fields=["next_allowed_at"])

        wait_seconds = (reserved_at - now).total_seconds()
        if wait_seconds > 0:
            self.sleeper(wait_seconds)

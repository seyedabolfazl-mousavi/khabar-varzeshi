"""Replace active RSS sources with the verified نماد صنعت list."""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from core.editorial.sources import NEMAD_SOURCES
from core.models import RssSource


class Command(BaseCommand):
    help = "Deactivate old feeds and upsert the 25 نماد صنعت RSS sources."

    def handle(self, *args: Any, **options: Any) -> None:
        urls = [item.url for item in NEMAD_SOURCES]
        deactivated = (
            RssSource.objects.exclude(url__in=urls).filter(is_active=True).update(is_active=False)
        )
        for item in NEMAD_SOURCES:
            RssSource.objects.update_or_create(
                url=item.url,
                defaults={
                    "name": item.name,
                    "category": item.category,
                    "priority": item.priority,
                    "is_active": True,
                },
            )
        active = RssSource.objects.filter(is_active=True).count()
        self.stdout.write(self.style.SUCCESS(
            f"Deactivated {deactivated} old source(s). Active RSS sources: {active}."
        ))

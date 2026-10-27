import time
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from workshop.ai import process_one
from workshop.models import AIJob

class Command(BaseCommand):
    help = "Process AI jobs outside web requests. SQLite: one worker only."
    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--recover-stale", action="store_true", help="Mark running jobs older than 30 minutes unknown; never retry automatically")
    def handle(self, *args, **options):
        if options["recover_stale"]:
            count = AIJob.objects.filter(status="running", started_at__lt=timezone.now()-timedelta(minutes=30)).update(
                status="unknown", error="Worker прерван; результат у провайдера неизвестен. Повтор не выполнялся.", finished_at=timezone.now())
            self.stdout.write(f"Recovered: {count}")
            return
        try:
            while True:
                processed = process_one()
                if options["once"]:
                    return
                if not processed:
                    time.sleep(2)
        except KeyboardInterrupt:
            return

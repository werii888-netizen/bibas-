from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

class Command(BaseCommand):
    help = "Create initial permission groups without creating users or passwords"
    def handle(self, *args, **options):
        roles = {
            "Приёмщик": ["view_client", "add_client", "view_order", "add_order", "change_order"],
            "Мастер": ["view_order", "change_order"],
            "Кассир": ["view_order", "record_payment"],
        }
        for name, codes in roles.items():
            group, _ = Group.objects.get_or_create(name=name)
            group.permissions.set(Permission.objects.filter(content_type__app_label="crm", codename__in=codes))
        self.stdout.write(self.style.SUCCESS("Роли настроены"))

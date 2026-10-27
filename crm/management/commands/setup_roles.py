from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db.models import Q

class Command(BaseCommand):
    help = "Create initial permission groups without creating users or passwords"
    def handle(self, *args, **options):
        roles = {
            "Приёмщик": {"crm": ["view_client", "add_client", "view_order", "add_order", "change_order"],
                        "workshop": ["approve_price", "view_design"]},
            "Мастер": {"crm": ["view_order", "change_order"], "workshop": ["view_design"]},
            "Кассир": {"crm": ["view_order", "record_payment"]},
            "Кладовщик": {"crm": ["view_order"], "workshop": ["view_metalaccount", "add_metalaccount", "view_metalmovement", "add_metalmovement"]},
            "Дизайнер": {"workshop": ["view_design", "add_design"]},
            "AI-дизайнер": {"workshop": ["view_design", "add_design", "use_design_ai"]},
        }
        for name, apps in roles.items():
            group, _ = Group.objects.get_or_create(name=name)
            query = Q(pk__in=[])
            for app, codes in apps.items():
                query |= Q(content_type__app_label=app, codename__in=codes)
            group.permissions.set(Permission.objects.filter(query))
        self.stdout.write(self.style.SUCCESS("Роли настроены"))

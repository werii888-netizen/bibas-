from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .analytics_adapter import build_order_report
from .models import Order


@login_required
@require_GET
@never_cache
def analytics_report(request):
    if not request.user.is_superuser:
        raise PermissionDenied("Нет доступа к аналитике")

    report = build_order_report(Order.objects.all())

    return render(
        request,
        "crm/analytics.html",
        {"report": report},
    )
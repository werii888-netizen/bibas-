from datetime import timedelta
from django.db.models import DateTimeField, DecimalField, Exists, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from crm.models import Event, Order
from .models import PriceApproval

def risk_orders(days=7):
    latest = Event.objects.filter(order=OuterRef("pk")).order_by("-pk").values("created_at")[:1]
    agreed = PriceApproval.objects.filter(order=OuterRef("pk"), amount=OuterRef("price"))
    active = Order.objects.exclude(status__in=["delivered", "cancelled"]).annotate(
        last_activity=Coalesce(Subquery(latest, output_field=DateTimeField()), "created_at"),
        price_agreed=Exists(agreed),
    )
    cutoff = timezone.now() - timedelta(days=days)
    return active.annotate().filter(
        Q(due_date__lt=timezone.localdate()) | Q(last_activity__lte=cutoff) |
        Q(master__isnull=True) | Q(price_agreed=False)
    ).select_related("client", "master").prefetch_related("payments").order_by("due_date", "pk"), cutoff

def risk_summary(qs, cutoff):
    # Counts are database aggregates; ledger totals use a separate query to avoid join multiplication.
    active = Order.objects.exclude(status__in=["delivered", "cancelled"])
    received = active.values_list("pk", flat=True)
    from crm.models import Payment
    income = Payment.objects.filter(order_id__in=received, kind="receipt").aggregate(total=Sum("amount"))["total"] or 0
    refunds = Payment.objects.filter(order_id__in=received, kind="refund").aggregate(total=Sum("amount"))["total"] or 0
    price = active.aggregate(total=Sum("price"))["total"] or 0
    return {
        "overdue": qs.filter(due_date__lt=timezone.localdate()).count(),
        "idle": qs.filter(last_activity__lte=cutoff).count(),
        "unassigned": qs.filter(master__isnull=True).count(),
        "unapproved": qs.filter(price_agreed=False).count(),
        "outstanding": price - income + refunds,
    }

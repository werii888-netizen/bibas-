from django.utils import timezone

from .analytics import analyze_orders


def build_order_report(queryset, *, as_of=None):
    """Построить отчёт по переданной выборке заказов."""
    if as_of is None:
        as_of = timezone.localdate()

    rows = [
        {
            "order_id": order.pk,
            "created_at": order.created_at,
            "status": order.status,
            "master_id": order.master_id,
            "due_date": order.due_date,
            "price": order.price,
        }
        for order in queryset
    ]

    return analyze_orders(rows, as_of=as_of)
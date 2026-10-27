from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from .models import Event, Order, Payment

TRANSITIONS = {
    "accepted": {"approval", "work", "cancelled"},
    "approval": {"work", "cancelled"},
    "work": {"approval", "ready", "cancelled"},
    "ready": {"work", "delivered", "cancelled"},
    "delivered": set(), "cancelled": set(),
}

@transaction.atomic
def change_status(order_id, actor, status, version):
    if not actor.has_perm("crm.change_order"):
        raise PermissionDenied
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.version != version:
        raise ValidationError("Заказ уже изменён другим сотрудником. Обновите страницу.")
    if status not in TRANSITIONS[order.status]:
        raise ValidationError("Недопустимый переход статуса.")
    if status == Order.Status.DELIVERED and order.balance != 0:
        raise ValidationError("Выдача запрещена: заказ должен быть полностью оплачен.")
    old = order.get_status_display()
    # Compare-and-swap also protects against stale writes on development SQLite.
    changed = Order.objects.filter(pk=order.pk, version=version).update(status=status, version=version + 1)
    if not changed:
        raise ValidationError("Заказ уже изменён. Обновите страницу.")
    Event.objects.create(order=order, actor=actor, message=f"{old} → {Order.Status(status).label}")

@transaction.atomic
def record_payment(order_id, actor, *, kind, amount, note, token):
    if not actor.has_perm("crm.record_payment"):
        raise PermissionDenied
    order = Order.objects.select_for_update().get(pk=order_id)
    existing = Payment.objects.filter(token=token).first()
    if existing:
        if (existing.order_id, existing.kind, existing.amount, existing.note, existing.actor_id) != (order.pk, kind, amount, note, actor.pk):
            raise ValidationError("Идентификатор операции уже использован.")
        return existing
    payment = Payment(order=order, actor=actor, kind=kind, amount=amount, note=note, token=token)
    payment.full_clean()
    if kind == "refund" and amount > order.paid:
        raise ValidationError("Возврат превышает полученную сумму.")
    if kind == "receipt" and (order.status in {"cancelled", "delivered"} or amount > order.balance):
        raise ValidationError("Оплата превышает остаток или заказ закрыт.")
    payment.save()
    Event.objects.create(order=order, actor=actor, message=f"{payment.get_kind_display()}: {amount:.2f} ₽")
    return payment

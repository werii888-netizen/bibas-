from decimal import Decimal
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from crm.models import Event, Order
from .models import MetalAccount, MetalMovement, PriceApproval

@transaction.atomic
def approve_price(order_id, actor, version, evidence):
    if not actor.has_perm("workshop.approve_price"):
        raise PermissionDenied
    evidence = evidence.strip()
    if not evidence or len(evidence) > 250:
        raise ValidationError("Укажите основание согласования (до 250 символов).")
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.version != version or order.status in {"delivered", "cancelled"}:
        raise ValidationError("Заказ изменён или закрыт. Обновите страницу.")
    approval, created = PriceApproval.objects.get_or_create(
        order=order, defaults={"amount": order.price, "actor": actor, "evidence": evidence},
    )
    if not created:
        if approval.amount != order.price:
            raise ValidationError("Согласованная сумма отличается от стоимости заказа.")
        return approval
    Event.objects.create(order=order, actor=actor, message=f"Стоимость согласована: {order.price:.2f} ₽")
    return approval

@transaction.atomic
def record_metal(actor, *, account, order, kind, grams, reason, token):
    if not actor.has_perm("workshop.add_metalmovement"):
        raise PermissionDenied
    account = MetalAccount.objects.select_for_update().get(pk=account.pk)
    previous = MetalMovement.objects.filter(token=token).first()
    if previous:
        values = (account.pk, order.pk if order else None, kind, grams, reason, actor.pk)
        if values != (previous.account_id, previous.order_id, previous.kind, previous.grams, previous.reason, previous.actor_id):
            raise ValidationError("Идентификатор движения уже использован с другими данными.")
        return previous
    move = MetalMovement(account=account, order=order, kind=kind, grams=grams, reason=reason, token=token, actor=actor)
    move.full_clean()
    if kind in {"issued", "returned", "consumed", "work_loss"} and order is None:
        raise ValidationError("Для выдачи и возврата из работы укажите заказ.")
    if kind in {"issued", "loss"} and grams > account.balance:
        raise ValidationError("Недостаточно металла на счёте.")
    if kind in {"returned", "consumed", "work_loss"}:
        work_balance = sum((m.grams if m.kind == "issued" else -m.grams
                            for m in account.movements.filter(order=order, kind__in=["issued", "returned", "consumed", "work_loss"])), Decimal("0.000"))
        if grams > work_balance:
            raise ValidationError("Операция превышает остаток металла в работе по этому заказу.")
    move.save()
    if order:
        Event.objects.create(order=order, actor=actor, message=f"Металл: {move.get_kind_display()}, {grams:.3f} г, счёт №{account.pk}")
    return move

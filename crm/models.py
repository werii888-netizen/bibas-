from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

class Client(models.Model):
    name = models.CharField("Имя клиента", max_length=160)
    phone = models.CharField("Телефон", max_length=40)
    notes = models.TextField("Комментарий", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "pk"]
        verbose_name = "Клиент"
        verbose_name_plural = "Клиенты"

    def __str__(self):
        return f"{self.name} · {self.phone}"

class Order(models.Model):
    class Status(models.TextChoices):
        ACCEPTED = "accepted", "Принят"
        APPROVAL = "approval", "Согласование"
        WORK = "work", "В работе"
        READY = "ready", "Готов"
        DELIVERED = "delivered", "Выдан"
        CANCELLED = "cancelled", "Отменён"

    client = models.ForeignKey(Client, on_delete=models.PROTECT, verbose_name="Клиент")
    kind = models.CharField("Тип", max_length=12, choices=[("repair", "Ремонт"), ("custom", "Изготовление")])
    item = models.CharField("Изделие", max_length=200)
    material = models.CharField("Металл и проба", max_length=100, blank=True)
    weight = models.DecimalField("Вес, г", max_digits=9, decimal_places=3, null=True, blank=True, validators=[MinValueValidator(Decimal("0.001"))])
    stones = models.TextField("Камни", blank=True)
    condition = models.TextField("Состояние и комплектность при приёме")
    work_description = models.TextField("Необходимые работы")
    master = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, verbose_name="Мастер", related_name="assigned_orders")
    due_date = models.DateField("Срок", db_index=True)
    price = models.DecimalField("Стоимость, ₽", max_digits=12, decimal_places=2, validators=[MinValueValidator(0)])
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACCEPTED, db_index=True)
    version = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    creation_key = models.UUIDField(unique=True, editable=False)

    class Meta:
        ordering = ["-pk"]
        permissions = [("record_payment", "Может проводить оплаты и возвраты")]
        constraints = [
            models.CheckConstraint(condition=models.Q(price__gte=0), name="order_price_nonnegative"),
            models.CheckConstraint(condition=models.Q(weight__isnull=True) | models.Q(weight__gt=0), name="order_weight_positive"),
        ]

    @property
    def paid(self):
        return sum((p.amount if p.kind == "receipt" else -p.amount for p in self.payments.all()), Decimal("0.00"))

    @property
    def balance(self):
        return self.price - self.paid

    def __str__(self):
        return f"Заказ №{self.pk}: {self.item}"

class Payment(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="payments")
    kind = models.CharField("Операция", max_length=10, choices=[("receipt", "Оплата"), ("refund", "Возврат")])
    amount = models.DecimalField("Сумма, ₽", max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    note = models.CharField("Основание / комментарий", max_length=200)
    token = models.UUIDField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="payment_positive"),
            models.CheckConstraint(condition=models.Q(kind__in=["receipt", "refund"]), name="payment_kind_valid"),
        ]

class Event(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    message = models.CharField(max_length=250)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-pk"]

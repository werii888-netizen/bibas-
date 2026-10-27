import uuid
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

COLORS = [("yellow", "Жёлтое"), ("white", "Белое")]
FINENESS = [(585, "585"), (750, "750"), (999, "999")]
KINDS = [
    ("ring", "Кольцо"), ("earrings", "Серьги"), ("pendant", "Подвеска"),
    ("chain", "Цепь"), ("bracelet", "Браслет"), ("brooch", "Брошь"),
    ("cufflinks", "Запонки"), ("other", "Другой формат"),
]

def validate_alloy(color, fineness):
    if color == "white" and fineness == 999:
        raise ValidationError("Белое золото 999 не поддерживается как стандартный сплав. Выберите белое 585/750 либо жёлтое 999; покрытие укажите отдельно.")

class PriceApproval(models.Model):
    order = models.OneToOneField("crm.Order", on_delete=models.PROTECT, related_name="price_approval")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    evidence = models.CharField("Основание согласования", max_length=250)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        permissions = [("approve_price", "Может фиксировать согласование стоимости")]

class MetalAccount(models.Model):
    name = models.CharField("Название партии / счёта", max_length=150)
    owner = models.CharField("Владелец металла", max_length=150, help_text="Мастерская либо владелец клиентского металла. Разные владельцы — разные счета.")
    color = models.CharField("Цвет золота", max_length=10, choices=COLORS)
    fineness = models.PositiveSmallIntegerField("Проба", choices=FINENESS)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(fineness__in=[585, 750, 999]), name="metal_fineness"),
            models.CheckConstraint(condition=models.Q(color__in=["yellow", "white"]), name="metal_color"),
            models.CheckConstraint(condition=~models.Q(color="white", fineness=999), name="metal_white_alloy"),
        ]

    def clean(self):
        validate_alloy(self.color, self.fineness)

    def __str__(self):
        return f"{self.name} · {self.owner} · {self.get_color_display()} {self.fineness}"

    @property
    def balance(self):
        return sum((m.grams if m.kind in {"received", "returned"} else -m.grams
                    for m in self.movements.all() if m.kind in {"received", "returned", "issued", "loss"}), Decimal("0.000"))

    @property
    def in_work(self):
        return sum((m.grams if m.kind == "issued" else -m.grams
                    for m in self.movements.all() if m.kind in {"issued", "returned", "consumed", "work_loss"}), Decimal("0.000"))

    @property
    def fine_gold(self):
        return (self.balance * Decimal(self.fineness) / 1000).quantize(Decimal("0.000001"))

class MetalMovement(models.Model):
    KINDS = [
        ("received", "Приход на склад"), ("issued", "Выдано в работу"),
        ("returned", "Возвращено из работы"), ("loss", "Списание со склада"),
        ("consumed", "В составе изделия"), ("work_loss", "Техпотери в работе"),
    ]
    account = models.ForeignKey(MetalAccount, on_delete=models.PROTECT, related_name="movements")
    order = models.ForeignKey("crm.Order", on_delete=models.PROTECT, null=True, blank=True)
    kind = models.CharField("Операция", max_length=10, choices=KINDS)
    grams = models.DecimalField("Масса сплава, г", max_digits=12, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    reason = models.CharField("Документ / основание", max_length=250)
    token = models.UUIDField(unique=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(grams__gt=0), name="metal_movement_positive"),
            models.CheckConstraint(condition=models.Q(kind__in=["received", "issued", "returned", "loss", "consumed", "work_loss"]), name="metal_movement_kind"),
        ]

class Design(models.Model):
    title = models.CharField("Название", max_length=150)
    kind = models.CharField("Формат изделия", max_length=12, choices=KINDS)
    other_kind = models.CharField("Другой формат: название", max_length=100, blank=True)
    color = models.CharField("Цвет золота", max_length=10, choices=COLORS)
    fineness = models.PositiveSmallIntegerField("Проба", choices=FINENESS)
    style = models.CharField("Стиль", max_length=150)
    dimensions = models.CharField("Размеры / посадка", max_length=250, blank=True)
    stones = models.TextField("Камни и закрепка", max_length=1500, blank=True)
    elements = models.TextField("Элементы итогового изделия", max_length=2000)
    finish = models.CharField("Финиш / покрытие", max_length=200, blank=True)
    additions = models.TextField("Что добавить", max_length=1000, blank=True)
    removals = models.TextField("Что убрать", max_length=1000, blank=True)
    upgrade = models.TextField("Апгрейд / пожелания", max_length=1500, blank=True)
    parent = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="variants")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-pk"]
        permissions = [("use_design_ai", "Может запускать платные AI-задания")]
        constraints = [
            models.CheckConstraint(condition=models.Q(fineness__in=[585, 750, 999]), name="design_fineness"),
            models.CheckConstraint(condition=models.Q(color__in=["yellow", "white"]), name="design_color"),
            models.CheckConstraint(condition=~models.Q(color="white", fineness=999), name="design_white_alloy"),
        ]

    def clean(self):
        validate_alloy(self.color, self.fineness)
        if self.kind == "other" and not self.other_kind.strip():
            raise ValidationError({"other_kind": "Укажите название формата."})

class AIJob(models.Model):
    STATUS = [
        ("pending", "В очереди"), ("running", "Выполняется"),
        ("succeeded", "Готово"), ("failed", "Ошибка"), ("unknown", "Результат неизвестен"),
    ]
    design = models.ForeignKey(Design, on_delete=models.PROTECT, related_name="jobs")
    kind = models.CharField(max_length=10, choices=[("prompt", "AI-промпт"), ("image", "Эскиз")])
    status = models.CharField(max_length=12, choices=STATUS, default="pending", db_index=True)
    token = models.UUIDField(unique=True, default=uuid.uuid4)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    input_prompt = models.TextField()
    reference = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True)
    model = models.CharField(max_length=150)
    image_model = models.CharField(max_length=150, blank=True)
    output_text = models.TextField(blank=True)
    image = models.BinaryField(null=True, editable=False)
    error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ["-pk"]

from django import forms
from .models import Design, MetalAccount, MetalMovement

class ApprovalForm(forms.Form):
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    evidence = forms.CharField(label="Основание согласования", max_length=250, help_text="Например: номер подписанной квитанции или переписки с подтверждением клиента.")
    confirmed = forms.BooleanField(label="Клиент согласовал указанную в заказе стоимость")

class MetalAccountForm(forms.ModelForm):
    class Meta:
        model = MetalAccount
        fields = ["name", "owner", "color", "fineness"]

class MetalMovementForm(forms.ModelForm):
    token = forms.UUIDField(widget=forms.HiddenInput)
    class Meta:
        model = MetalMovement
        fields = ["account", "order", "kind", "grams", "reason"]

class DesignForm(forms.ModelForm):
    class Meta:
        model = Design
        fields = ["title", "kind", "other_kind", "color", "fineness", "style", "dimensions",
                  "stones", "elements", "finish", "additions", "removals", "upgrade"]
        widgets = {name: forms.Textarea(attrs={"rows": 3}) for name in ["stones", "elements", "additions", "removals", "upgrade"]}

class JobForm(forms.Form):
    token = forms.UUIDField(widget=forms.HiddenInput)
    kind = forms.ChoiceField(label="Действие", choices=[("prompt", "Создать AI-промпт"), ("image", "Создать эскиз")])
    reference = forms.IntegerField(label="Номер исходного эскиза (необязательно)", min_value=1, required=False,
                                  help_text="Готовое изображение этого изделия или исходного варианта. Пусто — новый эскиз.")

from django import forms
from django.contrib.auth import get_user_model
from .models import Client, Order, Payment

class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ["name", "phone", "notes"]

class OrderForm(forms.ModelForm):
    creation_key = forms.UUIDField(widget=forms.HiddenInput)
    class Meta:
        model = Order
        fields = ["client", "kind", "item", "material", "weight", "stones", "condition", "work_description", "master", "due_date", "price"]
        widgets = {"due_date": forms.DateInput(attrs={"type": "date"}), "condition": forms.Textarea(attrs={"rows": 3}), "work_description": forms.Textarea(attrs={"rows": 3}), "stones": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["master"].queryset = get_user_model().objects.filter(is_active=True).order_by("username")

class StatusForm(forms.Form):
    version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    status = forms.ChoiceField(label="Новый статус", choices=Order.Status.choices)

class PaymentForm(forms.ModelForm):
    token = forms.UUIDField(widget=forms.HiddenInput)
    class Meta:
        model = Payment
        fields = ["kind", "amount", "note"]

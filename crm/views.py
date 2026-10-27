import uuid
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from .forms import ClientForm, OrderForm, PaymentForm, StatusForm
from .models import Client, Event, Order
from .services import change_status, record_payment

@login_required
@permission_required("crm.view_order", raise_exception=True)
def orders(request):
    qs = Order.objects.select_related("client", "master")
    query = request.GET.get("q", "").strip()[:160]
    if query:
        match = Q(item__icontains=query) | Q(client__name__icontains=query) | Q(client__phone__icontains=query)
        if query.isascii() and query.isdigit() and len(query) <= 18:
            match |= Q(pk=int(query))
        qs = qs.filter(match)
    status = request.GET.get("status", "")
    if status in Order.Status.values:
        qs = qs.filter(status=status)
    if request.GET.get("overdue") == "1":
        qs = qs.filter(due_date__lt=timezone.localdate()).exclude(status__in=["delivered", "cancelled"])
    return render(request, "crm/orders.html", {
        "page": Paginator(qs, 25).get_page(request.GET.get("page")), "query": query,
        "statuses": Order.Status.choices, "status": status, "today": timezone.localdate(),
    })

@login_required
@permission_required("crm.view_client", raise_exception=True)
def clients(request):
    query = request.GET.get("q", "").strip()[:160]
    qs = Client.objects.filter(Q(name__icontains=query) | Q(phone__icontains=query))
    return render(request, "crm/clients.html", {"page": Paginator(qs, 25).get_page(request.GET.get("page")), "query": query})

@login_required
@permission_required("crm.add_client", raise_exception=True)
def client_create(request):
    form = ClientForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Клиент добавлен.")
        return redirect("clients")
    return render(request, "crm/form.html", {"form": form, "title": "Новый клиент"})

@login_required
@permission_required(("crm.add_order", "crm.view_client"), raise_exception=True)
def order_create(request):
    form = OrderForm(request.POST or None, initial={"creation_key": uuid.uuid4()})
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            defaults = {key: value for key, value in form.cleaned_data.items() if key != "creation_key"}
            order, created = Order.objects.get_or_create(
                creation_key=form.cleaned_data["creation_key"],
                defaults={**defaults, "created_by": request.user},
            )
            if created:
                Event.objects.create(order=order, actor=request.user, message="Заказ принят")
            elif order.created_by_id != request.user.pk:
                form.add_error(None, "Идентификатор заказа уже использован. Откройте новую форму заказа.")
                return render(request, "crm/form.html", {"form": form, "title": "Новый заказ"}, status=409)
            elif any(getattr(order, field) != value for field, value in defaults.items()):
                form.add_error(None, "Данные отличаются от уже созданного заказа. Изменения не сохранены. Для нового заказа откройте новую форму.")
                return render(request, "crm/form.html", {"form": form, "title": "Новый заказ", "existing_order": order}, status=409)
        return redirect("order", pk=order.pk)
    return render(request, "crm/form.html", {"form": form, "title": "Новый заказ"})

@login_required
@permission_required("crm.view_order", raise_exception=True)
def order_detail(request, pk):
    order = get_object_or_404(Order.objects.select_related("client", "master").prefetch_related("payments", "events__actor"), pk=pk)
    return render(request, "crm/order.html", {
        "order": order, "status_form": StatusForm(initial={"version": order.version}),
        "payment_form": PaymentForm(initial={"token": uuid.uuid4()}),
    })

@login_required
@permission_required("crm.change_order", raise_exception=True)
@require_POST
def status_update(request, pk):
    get_object_or_404(Order, pk=pk)
    form = StatusForm(request.POST)
    if form.is_valid():
        try:
            change_status(pk, request.user, **form.cleaned_data)
            messages.success(request, "Статус обновлён.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    else:
        messages.error(request, "Проверьте новый статус.")
    return redirect("order", pk=pk)

@login_required
@permission_required("crm.record_payment", raise_exception=True)
@require_POST
def payment_create(request, pk):
    get_object_or_404(Order, pk=pk)
    form = PaymentForm(request.POST)
    if form.is_valid():
        try:
            record_payment(pk, request.user, **form.cleaned_data)
            messages.success(request, "Операция сохранена.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    else:
        messages.error(request, " ".join(str(e) for errors in form.errors.values() for e in errors))
    return redirect("order", pk=pk)

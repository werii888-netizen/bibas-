import io
import uuid
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from crm.models import Order
from .ai import available, enqueue
from .forms import ApprovalForm, DesignForm, JobForm, MetalAccountForm, MetalMovementForm
from .models import AIJob, Design, MetalAccount, MetalMovement
from .prompts import SPEC_FIELDS, build_prompt, specification
from .reports import risk_orders, risk_summary
from .services import approve_price, record_metal

@login_required
@permission_required("crm.view_order", raise_exception=True)
def risks(request):
    try:
        days = max(1, min(90, int(request.GET.get("days", "7"))))
    except ValueError:
        days = 7
    qs, cutoff = risk_orders(days)
    return render(request, "workshop/risks.html", {
        "page": Paginator(qs, 25).get_page(request.GET.get("page")), "days": days,
        "cutoff": cutoff, "today": timezone.localdate(), "summary": risk_summary(qs, cutoff),
    })

@login_required
@permission_required(("crm.view_order", "workshop.approve_price"), raise_exception=True)
@require_POST
def price_approve(request, pk):
    order = get_object_or_404(Order, pk=pk)
    form = ApprovalForm(request.POST)
    if form.is_valid():
        try:
            approve_price(order.pk, request.user, form.cleaned_data["version"], form.cleaned_data["evidence"])
            messages.success(request, "Согласование стоимости зафиксировано.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    else:
        messages.error(request, "Укажите основание и подтвердите согласие клиента.")
    return redirect("order", pk=pk)

@login_required
@permission_required(("workshop.view_metalaccount", "workshop.view_metalmovement"), raise_exception=True)
def metals(request):
    accounts = MetalAccount.objects.prefetch_related("movements")
    return render(request, "workshop/metals.html", {
        "page": Paginator(accounts, 25).get_page(request.GET.get("page")),
        "movements": MetalMovement.objects.select_related("account", "order", "actor")[:50],
    })

@login_required
@permission_required("workshop.add_metalaccount", raise_exception=True)
def metal_account_create(request):
    form = MetalAccountForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("metals")
    return render(request, "crm/form.html", {"form": form, "title": "Новый счёт металла"})

@login_required
@permission_required(("workshop.add_metalmovement", "workshop.view_metalaccount", "crm.view_order"), raise_exception=True)
def metal_movement_create(request):
    form = MetalMovementForm(request.POST if request.method == "POST" else None, initial={"token": uuid.uuid4()})
    if request.method == "POST" and form.is_valid():
        try:
            record_metal(request.user, **form.cleaned_data)
            return redirect("metals")
        except ValidationError as exc:
            form.add_error(None, " ".join(exc.messages))
    return render(request, "crm/form.html", {"form": form, "title": "Движение металла"})

@login_required
@permission_required("workshop.view_design", raise_exception=True)
def designs(request):
    query = request.GET.get("q", "").strip()[:150]
    qs = Design.objects.filter(title__icontains=query).select_related("parent", "created_by")
    return render(request, "workshop/designs.html", {
        "page": Paginator(qs, 24).get_page(request.GET.get("page")), "query": query,
    })

def _create_design(request, parent=None):
    initial = specification(parent) if parent else {}
    if parent:
        initial.update(title=parent.title + " · вариант", additions="", removals="", upgrade="")
    form = DesignForm(request.POST if request.method == "POST" else None, initial=initial)
    if request.method == "POST" and form.is_valid():
        design = form.save(commit=False)
        design.parent = parent
        design.created_by = request.user
        design.save()
        return redirect("design", pk=design.pk)
    return render(request, "crm/form.html", {"form": form, "title": "Вариант изделия" if parent else "Новое изделие"})

@login_required
@permission_required(("workshop.add_design", "workshop.view_design"), raise_exception=True)
def design_create(request):
    return _create_design(request)

@login_required
@permission_required(("workshop.add_design", "workshop.view_design"), raise_exception=True)
def design_variant(request, pk):
    return _create_design(request, parent=get_object_or_404(Design, pk=pk))

@login_required
@permission_required("workshop.view_design", raise_exception=True)
def design_detail(request, pk):
    design = get_object_or_404(Design.objects.select_related("parent", "created_by"), pk=pk)
    jobs = design.jobs.defer("image").select_related("actor")[:30]
    reference_jobs = AIJob.objects.filter(design_id__in=[design.pk, design.parent_id], kind="image", status="succeeded").defer("image")[:20]
    return render(request, "workshop/design.html", {
        "design": design, "prompt": build_prompt(design), "jobs": jobs, "reference_jobs": reference_jobs,
        "variants": design.variants.all()[:30],
        "job_form": JobForm(initial={"token": uuid.uuid4()}),
        "text_ready": available("prompt"), "image_ready": available("image"),
    })

@login_required
@permission_required("workshop.view_design", raise_exception=True)
def design_export(request, pk):
    design = get_object_or_404(Design, pk=pk)
    if request.GET.get("format") == "prompt":
        response = HttpResponse(build_prompt(design), content_type="text/plain; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="design-{pk}-prompt.txt"'
    else:
        response = JsonResponse({"id": design.pk, "parent_id": design.parent_id, "specification": specification(design)}, json_dumps_params={"ensure_ascii": False})
        response["Content-Disposition"] = f'attachment; filename="design-{pk}.json"'
    return response

@login_required
@permission_required(("workshop.view_design", "workshop.use_design_ai"), raise_exception=True)
@require_POST
def job_create(request, pk):
    design = get_object_or_404(Design, pk=pk)
    form = JobForm(request.POST)
    if form.is_valid():
        try:
            enqueue(design, request.user, form.cleaned_data["kind"], form.cleaned_data["token"], form.cleaned_data["reference"])
            messages.success(request, "AI-задание поставлено в очередь. Обновите карточку после выполнения.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    else:
        messages.error(request, "Проверьте параметры AI-задания.")
    return redirect("design", pk=pk)

@login_required
@permission_required("workshop.view_design", raise_exception=True)
def job_image(request, pk):
    job = get_object_or_404(AIJob.objects.only("image", "status", "kind"), pk=pk, status="succeeded", kind="image")
    if not job.image:
        return HttpResponse(status=404)
    response = FileResponse(io.BytesIO(bytes(job.image)), content_type="image/png", filename=f"design-{job.pk}.png")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response

@login_required
def start(request):
    for perm, target in [("crm.view_order", "orders"), ("workshop.view_design", "designs"), ("workshop.view_metalaccount", "metals")]:
        if request.user.has_perm(perm):
            return redirect(target)
    return render(request, "403.html", status=403)

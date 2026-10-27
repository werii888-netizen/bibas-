import base64
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from crm.models import Event, Order
from crm.tests import Fixtures
from .ai import ProviderError, call_provider, enqueue, process_one
from .forms import DesignForm, MetalAccountForm
from .models import AIJob, Design, MetalAccount, MetalMovement, PriceApproval
from .prompts import build_prompt, specification
from .reports import risk_orders, risk_summary
from .services import approve_price, record_metal

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=")

class WorkshopTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_records()
        self.client.force_login(self.user)
        self.account = MetalAccount.objects.create(name="Gold", owner="Workshop", color="yellow", fineness=585)
        self.design = Design.objects.create(
            title="Ring", kind="ring", color="yellow", fineness=585,
            style="Classic", elements="Band", created_by=self.user,
        )

    def move(self, kind, amount, order=None, token=None):
        return record_metal(self.user, account=self.account, order=order, kind=kind,
                            grams=Decimal(amount), reason="Document", token=token or uuid.uuid4())

    def test_all_new_pages_render(self):
        for url in ["/risks/", "/metals/", "/metals/new/", "/metals/movement/",
                    "/designs/", "/designs/new/", f"/designs/{self.design.pk}/",
                    f"/designs/{self.design.pk}/variant/", f"/orders/{self.order.pk}/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_risk_flags_and_real_outstanding(self):
        qs, cutoff = risk_orders()
        self.assertEqual(list(qs.values_list("pk", flat=True)), [self.order.pk])
        summary = risk_summary(qs, cutoff)
        self.assertEqual(summary["overdue"], 1)
        self.assertEqual(summary["unassigned"], 1)
        self.assertEqual(summary["unapproved"], 1)
        self.assertEqual(summary["outstanding"], Decimal("1000.10"))
        approve_price(self.order.pk, self.user, 0, "Client confirmation")
        self.order.master = self.user
        self.order.due_date = timezone.localdate()
        self.order.save()
        qs, _ = risk_orders()
        self.assertFalse(qs.exists())

    def test_inactivity_and_closed_order_exclusion(self):
        self.order.master = self.user
        self.order.due_date = timezone.localdate() + timedelta(days=2)
        self.order.save()
        approve_price(self.order.pk, self.user, 0, "Confirmation")
        old = timezone.now() - timedelta(days=8)
        Event.objects.filter(order=self.order).update(created_at=old)
        qs, cutoff = risk_orders()
        self.assertEqual(risk_summary(qs, cutoff)["idle"], 1)
        Event.objects.create(order=self.order, actor=self.user, message="Contact")
        self.assertFalse(risk_orders()[0].exists())
        self.order.status = "cancelled"
        self.order.save()
        self.assertFalse(risk_orders()[0].exists())

    def test_quote_records_amount_evidence_and_does_not_duplicate_history(self):
        first = approve_price(self.order.pk, self.user, 0, "Signed receipt 12")
        second = approve_price(self.order.pk, self.user, 0, "Repeated confirmation")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.amount, self.order.price)
        self.assertEqual(Event.objects.filter(order=self.order).count(), 1)
        with self.assertRaises(ValidationError):
            approve_price(self.order.pk, self.user, 1, "Stale")
        self.order.status = "cancelled"
        self.order.save()
        with self.assertRaises(ValidationError):
            approve_price(self.order.pk, self.user, 0, "Closed")

    def test_quote_requires_explicit_confirmation(self):
        response = self.client.post(f"/orders/{self.order.pk}/approve-price/",
                                    {"version": 0, "evidence": "Receipt"}, follow=True)
        self.assertFalse(PriceApproval.objects.exists())
        self.assertContains(response, "подтвердите согласие")
        self.client.post(f"/orders/{self.order.pk}/approve-price/",
                         {"version": 0, "evidence": "Receipt", "confirmed": "on"})
        self.assertTrue(PriceApproval.objects.exists())

    def test_metal_warehouse_work_and_pure_mass(self):
        self.move("received", "10.000")
        self.assertEqual(self.account.fine_gold, Decimal("5.850000"))
        self.move("issued", "4.000", self.order)
        self.move("returned", "1.000", self.order)
        self.move("work_loss", "0.100", self.order)
        self.move("consumed", "2.900", self.order)
        self.assertEqual(self.account.balance, Decimal("7.000"))
        self.assertEqual(self.account.in_work, Decimal("0.000"))
        self.assertEqual(self.account.fine_gold, Decimal("4.095000"))
        self.move("loss", "0.001")
        self.assertEqual(self.account.balance, Decimal("6.999"))

    def test_metal_rejects_negative_overdraft_and_unbacked_return(self):
        for kind, amount, order in [("received", "-1", None), ("issued", "1", self.order),
                                   ("loss", "1", None), ("returned", "1", self.order),
                                   ("issued", "1", None)]:
            with self.subTest(kind=kind, amount=amount, order=order):
                with self.assertRaises(ValidationError):
                    self.move(kind, amount, order)
        self.assertEqual(MetalMovement.objects.count(), 0)

    def test_metal_token_and_owner_isolation(self):
        token = uuid.uuid4()
        first = self.move("received", "1.001", token=token)
        repeat = self.move("received", "1.001", token=token)
        self.assertEqual(first.pk, repeat.pk)
        with self.assertRaises(ValidationError):
            self.move("received", "1.002", token=token)
        other = MetalAccount.objects.create(name="Customer", owner="Customer", color="yellow", fineness=585)
        self.assertEqual(other.balance, Decimal("0.000"))
        self.assertEqual(self.account.balance, Decimal("1.001"))

    def test_invalid_alloys_rejected_by_forms(self):
        account = MetalAccountForm({"name": "Invalid", "owner": "Workshop", "color": "white", "fineness": 999})
        self.assertFalse(account.is_valid())
        data = specification(self.design)
        data.update(color="white", fineness=999)
        self.assertFalse(DesignForm(data).is_valid())
        data.update(color="white", fineness=750)
        self.assertTrue(DesignForm(data).is_valid())
        data.update(kind="other", other_kind="")
        self.assertFalse(DesignForm(data).is_valid())

    def test_variant_preserves_original_and_exports_specification(self):
        before = specification(self.design)
        data = {**before, "title": "Upgraded", "color": "white", "fineness": 750,
                "additions": "Halo", "removals": "Engraving", "upgrade": "Modern silhouette"}
        response = self.client.post(f"/designs/{self.design.pk}/variant/", data)
        self.assertEqual(response.status_code, 302)
        variant = Design.objects.get(title="Upgraded")
        self.assertEqual(variant.parent_id, self.design.pk)
        self.design.refresh_from_db()
        self.assertEqual(specification(self.design), before)
        prompt = build_prompt(variant)
        for expected in ["Halo", "Engraving", "750", "white", "Modern silhouette"]:
            self.assertIn(expected, prompt)
        exported = self.client.get(f"/designs/{variant.pk}/export/").json()
        self.assertEqual(exported["specification"]["fineness"], 750)
        self.assertEqual(self.client.get(f"/designs/{variant.pk}/export/?format=prompt").status_code, 200)

    def test_new_roles_and_no_accidental_financial_access(self):
        call_command("setup_roles", verbosity=0)
        designer = get_user_model().objects.create_user("designer", password="test")
        designer.groups.add(Group.objects.get(name="Дизайнер"))
        self.client.force_login(designer)
        self.assertRedirects(self.client.get("/start/"), "/designs/")
        self.assertEqual(self.client.get("/metals/").status_code, 403)
        self.assertEqual(self.client.get("/risks/").status_code, 403)
        self.assertEqual(self.client.post(f"/designs/{self.design.pk}/jobs/").status_code, 403)
        with self.assertRaises(PermissionDenied):
            approve_price(self.order.pk, designer, 0, "Fake")
        with self.assertRaises(PermissionDenied):
            record_metal(designer, account=self.account, order=None, kind="received", grams=Decimal("1"),
                         reason="Test", token=uuid.uuid4())

    @override_settings(OPENAI_API_KEY="")
    def test_missing_ai_configuration_is_explicit(self):
        with self.assertRaisesMessage(ValidationError, "AI не подключён"):
            enqueue(self.design, self.user, "image", uuid.uuid4())
        self.assertEqual(AIJob.objects.count(), 0)
        self.assertContains(self.client.get(f"/designs/{self.design.pk}/"), "не подключена")

    @override_settings(OPENAI_API_KEY="fake-test-only", OPENAI_TEXT_MODEL="test-text", OPENAI_IMAGE_MODEL="test-image")
    def test_ai_queue_idempotency_quota_and_completion(self):
        token = uuid.uuid4()
        job = enqueue(self.design, self.user, "prompt", token)
        self.assertEqual(enqueue(self.design, self.user, "prompt", token).pk, job.pk)
        with self.assertRaises(ValidationError):
            enqueue(self.design, self.user, "image", token)
        enqueue(self.design, self.user, "prompt", uuid.uuid4())
        enqueue(self.design, self.user, "image", uuid.uuid4())
        with self.assertRaisesMessage(ValidationError, "три незавершённых"):
            enqueue(self.design, self.user, "image", uuid.uuid4())
        with patch("workshop.ai.call_provider", return_value=("AI concept", None)) as provider:
            self.assertTrue(process_one())
            provider.assert_called_once()
        job.refresh_from_db()
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(job.output_text, "AI concept")

    @override_settings(OPENAI_API_KEY="fake", OPENAI_TEXT_MODEL="test", AI_DAILY_LIMIT=1)
    def test_ai_daily_limit_includes_completed_jobs(self):
        job = enqueue(self.design, self.user, "prompt", uuid.uuid4())
        AIJob.objects.filter(pk=job.pk).update(status="succeeded")
        with self.assertRaisesMessage(ValidationError, "суточный"):
            enqueue(self.design, self.user, "prompt", uuid.uuid4())

    @override_settings(OPENAI_API_KEY="fake", OPENAI_TEXT_MODEL="test")
    def test_provider_failure_is_not_retried(self):
        job = enqueue(self.design, self.user, "prompt", uuid.uuid4())
        with patch("workshop.ai.call_provider", side_effect=ProviderError("Timeout")) as provider:
            self.assertTrue(process_one())
            self.assertFalse(process_one())
            provider.assert_called_once()
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")

    def test_text_adapter_handles_real_response_shape_and_incomplete_output(self):
        job = AIJob(kind="prompt", model="test", input_prompt="Spec")
        with patch("workshop.ai._post", return_value={"status": "completed", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "Concept"}]}]}):
            self.assertEqual(call_provider(job), ("Concept", None))
        with patch("workshop.ai._post", return_value={"status": "incomplete", "output": []}):
            with self.assertRaises(ProviderError):
                call_provider(job)

    @override_settings(OPENAI_API_KEY="fake", OPENAI_IMAGE_MODEL="test-image")
    def test_image_edit_uses_parent_reference_and_authenticates_image(self):
        original = AIJob.objects.create(design=self.design, actor=self.user, kind="image", status="succeeded",
                                        input_prompt="Original", model="test-image", image=PNG)
        child = Design.objects.create(**{**specification(self.design), "title": "Child"}, parent=self.design, created_by=self.user)
        job = enqueue(child, self.user, "image", uuid.uuid4(), original.pk)
        with patch("workshop.ai._post", return_value={"data": [{"b64_json": base64.b64encode(PNG).decode()}]}) as provider:
            text, image = call_provider(job)
            self.assertEqual(image, PNG)
            self.assertEqual(provider.call_args.args[0], "images/edits")
            self.assertIn(PNG, provider.call_args.args[1])
        AIJob.objects.filter(pk=job.pk).update(status="succeeded", image=PNG)
        self.assertEqual(self.client.get(f"/design-jobs/{job.pk}/image/").status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(f"/design-jobs/{job.pk}/image/").status_code, 302)

    @override_settings(OPENAI_API_KEY="fake", OPENAI_IMAGE_MODEL="test")
    def test_unrelated_reference_rejected_and_invalid_images_rejected(self):
        other = Design.objects.create(**{**specification(self.design), "title": "Other"}, created_by=self.user)
        reference = AIJob.objects.create(design=other, actor=self.user, kind="image", status="succeeded", input_prompt="Other", model="test", image=PNG)
        with self.assertRaises(ValidationError):
            enqueue(self.design, self.user, "image", uuid.uuid4(), reference.pk)
        job = AIJob(kind="image", model="test", input_prompt="Spec")
        with patch("workshop.ai._post", return_value={"data": [{"b64_json": "invalid"}]}):
            with self.assertRaises(ProviderError):
                call_provider(job)


    @override_settings(OPENAI_API_KEY="fake", OPENAI_TEXT_MODEL="test")
    def test_worker_honors_revoked_access_and_stale_recovery(self):
        job = enqueue(self.design, self.user, "prompt", uuid.uuid4())
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        with patch("workshop.ai.call_provider") as provider:
            process_one()
            provider.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        AIJob.objects.filter(pk=job.pk).update(status="running", started_at=timezone.now()-timedelta(minutes=31))
        call_command("run_ai_jobs", recover_stale=True, verbosity=0)
        job.refresh_from_db()
        self.assertEqual(job.status, "unknown")

    def test_provider_transport_errors_hide_sensitive_details(self):
        from urllib.error import HTTPError, URLError
        from .ai import _post
        with patch("workshop.ai.urlopen", side_effect=HTTPError("url", 401, "secret-api-key", {}, None)):
            with self.assertRaises(ProviderError) as error:
                _post("responses", {})
            self.assertNotIn("secret-api-key", str(error.exception))
        with patch("workshop.ai.urlopen", side_effect=URLError("secret-api-key")):
            with self.assertRaisesMessage(ProviderError, "автоматический повтор отключён"):
                _post("responses", {})

@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks")
class ConcurrentMetalTests(Fixtures, TransactionTestCase):
    def setUp(self):
        self.setup_records()
        self.account = MetalAccount.objects.create(name="Gold", owner="Workshop", color="yellow", fineness=750)
        record_metal(self.user, account=self.account, order=None, kind="received", grams=Decimal("10"),
                     reason="Receipt", token=uuid.uuid4())

    def test_two_masters_cannot_overdraw_metal(self):
        def attempt(_):
            connections.close_all()
            try:
                actor = get_user_model().objects.get(pk=self.user.pk)
                record_metal(actor, account=self.account, order=self.order, kind="issued", grams=Decimal("7"),
                             reason="Concurrent", token=uuid.uuid4())
                return True
            except ValidationError:
                return False
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.account.balance, Decimal("3"))

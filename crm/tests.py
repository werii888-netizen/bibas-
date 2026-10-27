import uuid
from datetime import timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import close_old_connections, connection, connections
from django.test import Client as Browser, TestCase, TransactionTestCase
from django.utils import timezone
from .models import Client, Event, Order, Payment
from .services import change_status, record_payment

class Fixtures:
    def setup_records(self):
        self.user = get_user_model().objects.create_superuser("owner", password="testing-only-strong-password")
        self.customer = Client.objects.create(name="Тестовый клиент", phone="+70000000000")
        self.order = Order.objects.create(
            client=self.customer, kind="repair", item="Кольцо", condition="Царапина",
            work_description="Полировка", due_date=timezone.localdate() - timedelta(days=1),
            price=Decimal("1000.10"), created_by=self.user, creation_key=uuid.uuid4(),
        )

    def pay(self, amount, kind="receipt", token=None):
        return record_payment(self.order.pk, self.user, kind=kind, amount=Decimal(amount), note="Тест", token=token or uuid.uuid4())

class WorkflowTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_records()
        self.client.force_login(self.user)

    def test_pages_render_and_search(self):
        for url in ["/", "/clients/", "/clients/new/", "/orders/new/", f"/orders/{self.order.pk}/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertContains(self.client.get("/?q=Кольцо"), "Кольцо")
        self.assertNotContains(self.client.get("/?q=несуществующее"), "Кольцо")
        self.assertContains(self.client.get("/?q=" + "9"*100), "Заказов пока нет")
        self.assertContains(self.client.get("/?overdue=1"), "Кольцо")

    def test_authentication_and_csrf(self):
        anonymous = Browser()
        self.assertEqual(anonymous.get("/").status_code, 302)
        csrf = Browser(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        self.assertEqual(csrf.post(f"/orders/{self.order.pk}/status/", {"status": "work", "version": 0}).status_code, 403)
        self.assertEqual(self.client.get(f"/orders/{self.order.pk}/status/").status_code, 405)
        self.assertEqual(self.client.get("/accounts/logout/").status_code, 405)

    def test_permissions_and_roles(self):
        call_command("setup_roles", verbosity=0)
        master = get_user_model().objects.create_user("master", password="test")
        master.groups.add(Group.objects.get(name="Мастер"))
        self.client.force_login(master)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/clients/").status_code, 403)
        self.assertEqual(self.client.get("/orders/new/").status_code, 403)
        self.assertEqual(self.client.post(f"/orders/{self.order.pk}/payments/").status_code, 403)
        with self.assertRaises(PermissionDenied):
            record_payment(self.order.pk, master, kind="receipt", amount=Decimal("1"), note="x", token=uuid.uuid4())

    def test_status_flow_and_stale_write(self):
        change_status(self.order.pk, self.user, "work", 0)
        with self.assertRaises(ValidationError):
            change_status(self.order.pk, self.user, "cancelled", 0)
        change_status(self.order.pk, self.user, "ready", 1)
        self.pay("1000.10")
        change_status(self.order.pk, self.user, "delivered", 2)
        with self.assertRaises(ValidationError):
            change_status(self.order.pk, self.user, "work", 3)
        self.assertEqual(Event.objects.count(), 4)

    def test_money_and_duplicate_submission(self):
        token = uuid.uuid4()
        first = self.pay("100.10", token=token)
        self.assertEqual(self.pay("100.10", token=token).pk, first.pk)
        self.pay("0.10", "refund")
        self.order.refresh_from_db()
        self.assertEqual(self.order.paid, Decimal("100.00"))
        self.assertEqual(self.order.balance, Decimal("900.10"))
        self.assertEqual(Payment.objects.count(), 2)
        self.assertEqual(Event.objects.count(), 2)
        with self.assertRaises(ValidationError):
            self.pay("2", token=token)

    def test_reject_invalid_financial_operations(self):
        for amount in ["0", "-1", "1000.11"]:
            with self.assertRaises(ValidationError):
                self.pay(amount)
        with self.assertRaises(ValidationError):
            self.pay("0.01", "refund")
        self.pay("10")
        with self.assertRaises(ValidationError):
            self.pay("10.01", "refund")
        self.order.status = "cancelled"
        self.order.save()
        with self.assertRaises(ValidationError):
            self.pay("1")
        self.pay("10", "refund")

    def test_payment_post_replay_is_idempotent(self):
        data = {"kind": "receipt", "amount": "10.10", "note": "Аванс", "token": str(uuid.uuid4())}
        url = f"/orders/{self.order.pk}/payments/"
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(Payment.objects.count(), 1)

    def test_order_creation_and_retry(self):
        data = {
            "client": self.customer.pk, "kind": "repair", "item": "Браслет",
            "condition": "Без застёжки", "work_description": "Замена застёжки",
            "due_date": timezone.localdate().isoformat(), "price": "20.10",
            "creation_key": str(uuid.uuid4()),
        }
        self.assertEqual(self.client.post("/orders/new/", data).status_code, 302)
        self.assertEqual(self.client.post("/orders/new/", data).status_code, 302)
        self.assertEqual(Order.objects.filter(item="Браслет").count(), 1)
        self.assertEqual(Event.objects.filter(message="Заказ принят").count(), 1)

    def test_client_creation_and_escaping(self):
        self.client.post("/clients/new/", {"name": "<script>alert(1)</script>", "phone": "123", "notes": ""})
        self.assertContains(self.client.get("/clients/"), "&lt;script&gt;")
        self.assertNotContains(self.client.get("/clients/"), "<script>")

    def test_protected_client_and_no_record_admin(self):
        from django.db.models.deletion import ProtectedError
        with self.assertRaises(ProtectedError):
            self.customer.delete()
        self.assertEqual(self.client.get("/admin/crm/order/").status_code, 404)

@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locking")
class ConcurrentPaymentTests(Fixtures, TransactionTestCase):
    def setUp(self):
        self.setup_records()

    def test_two_cashiers_cannot_overcharge(self):
        def attempt():
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.user.pk)
                record_payment(self.order.pk, actor, kind="receipt", amount=Decimal("700"),
                               note="Concurrent test", token=uuid.uuid4())
                return True
            except ValidationError:
                return False
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(Payment.objects.count(), 1)

class RegressionTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_records()
        self.client.force_login(self.user)
        change_status(self.order.pk, self.user, "work", 0)
        change_status(self.order.pk, self.user, "ready", 1)

    def test_delivery_requires_full_payment_without_side_effects(self):
        for amount in ["0", "1000.09"]:
            with self.subTest(paid=amount):
                if amount != "0":
                    self.pay(amount)
                before = Event.objects.count()
                with self.assertRaisesMessage(ValidationError, "Выдача запрещена"):
                    change_status(self.order.pk, self.user, "delivered", 2)
                self.order.refresh_from_db()
                self.assertEqual(self.order.status, "ready")
                self.assertEqual(self.order.version, 2)
                self.assertEqual(Event.objects.count(), before)
        self.pay("0.01")
        change_status(self.order.pk, self.user, "delivered", 2)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "delivered")
        self.assertEqual(self.order.balance, Decimal("0.00"))

    def test_refund_before_delivery_restores_debt(self):
        self.pay("1000.10")
        self.pay("0.01", "refund")
        with self.assertRaisesMessage(ValidationError, "Выдача запрещена"):
            change_status(self.order.pk, self.user, "delivered", 2)

    def test_zero_price_order_can_be_delivered(self):
        self.order.price = Decimal("0.00")
        self.order.save(update_fields=["price"])
        change_status(self.order.pk, self.user, "delivered", 2)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "delivered")

    def test_direct_post_cannot_deliver_with_debt(self):
        response = self.client.post(
            f"/orders/{self.order.pk}/status/",
            {"status": "delivered", "version": 2}, follow=True,
        )
        self.assertContains(response, "Выдача запрещена")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "ready")

    def creation_data(self):
        return {
            "client": self.customer.pk, "kind": "repair", "item": "Original",
            "material": "Gold 585", "weight": "1.200", "stones": "None",
            "condition": "Used", "work_description": "Repair", "master": self.user.pk,
            "due_date": timezone.localdate().isoformat(), "price": "20.10",
            "creation_key": str(uuid.uuid4()),
        }

    def test_reused_key_rejects_each_changed_field_and_preserves_form(self):
        other = Client.objects.create(name="Other", phone="456")
        changes = {
            "client": other.pk, "kind": "custom", "item": "Changed item",
            "material": "Silver", "weight": "1.201", "stones": "Ruby",
            "condition": "Changed condition", "work_description": "New work",
            "master": "", "due_date": (timezone.localdate() + timedelta(days=1)).isoformat(),
            "price": "20.11",
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                data = self.creation_data()
                first = self.client.post("/orders/new/", data)
                self.assertEqual(first.status_code, 302)
                before = (Order.objects.count(), Event.objects.count())
                changed = {**data, field: value}
                response = self.client.post("/orders/new/", changed)
                self.assertEqual(response.status_code, 409)
                self.assertContains(response, "Данные отличаются", status_code=409)
                self.assertEqual(str(response.context["form"][field].value()), str(value))
                self.assertEqual((Order.objects.count(), Event.objects.count()), before)
                original = Order.objects.get(creation_key=data["creation_key"])
                self.assertEqual(original.item, "Original")
                self.assertEqual(original.price, Decimal("20.10"))

    def test_reused_key_checks_actor_and_compares_normalized_values(self):
        data = self.creation_data()
        first = self.client.post("/orders/new/", data)
        equivalent = {**data, "price": "20.1", "weight": "1.2"}
        repeat = self.client.post("/orders/new/", equivalent)
        self.assertEqual(repeat.status_code, 302)
        self.assertEqual(first["Location"], repeat["Location"])
        other = get_user_model().objects.create_superuser("other", password="test")
        self.client.force_login(other)
        conflict = self.client.post("/orders/new/", data)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(Order.objects.filter(creation_key=data["creation_key"]).count(), 1)

    def test_changed_order_can_be_created_with_a_new_key(self):
        data = self.creation_data()
        first = self.client.post("/orders/new/", data)
        changed = {**data, "creation_key": str(uuid.uuid4()), "item": "New item"}
        second = self.client.post("/orders/new/", changed)
        self.assertEqual(second.status_code, 302)
        self.assertNotEqual(first["Location"], second["Location"])
        self.assertEqual(Order.objects.filter(item="New item").count(), 1)

import json
import uuid
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from .analytics_adapter import build_order_report
from .models import Client, Event, Order, Payment


class AnalyticsIntegrationTests(TestCase):
    def setUp(self):
        self.as_of = date(2026, 10, 28)
        self.user = get_user_model().objects.create_user(
            username="analytics-test",
        )
        self.customer = Client.objects.create(
            name="Тестовый клиент",
            phone="+70000000000",
        )

    def make_order(self, **changes):
        data = {
            "client": self.customer,
            "kind": "repair",
            "item": "Тестовое кольцо",
            "condition": "Учебный пример",
            "work_description": "Полировка",
            "due_date": self.as_of,
            "price": Decimal("1000.10"),
            "status": Order.Status.ACCEPTED,
            "master": self.user,
            "created_by": self.user,
            "creation_key": uuid.uuid4(),
        }
        data.update(changes)
        return Order.objects.create(**data)

    def snapshot(self):
        return {
            "clients": list(Client.objects.order_by("pk").values()),
            "orders": list(Order.objects.order_by("pk").values()),
            "payments": list(Payment.objects.order_by("pk").values()),
            "events": list(Event.objects.order_by("pk").values()),
        }

    def test_database_orders_reach_analytics(self):
        self.make_order(
            price=Decimal("0.10"),
            due_date=self.as_of - timedelta(days=1),
        )
        self.make_order(
            price=Decimal("0.20"),
            status=Order.Status.WORK,
            master=None,
        )

        result = build_order_report(
            Order.objects.all(),
            as_of=self.as_of,
        )

        self.assertTrue(result["metrics_allowed"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["metrics"]["order_count"], 2)
        self.assertEqual(
            result["metrics"]["total_order_value"],
            "0.30",
        )
        self.assertEqual(
            result["metrics"]["orders_by_status"]["accepted"],
            1,
        )
        self.assertEqual(
            result["metrics"]["orders_by_status"]["work"],
            1,
        )
        self.assertEqual(
            result["metrics"]["overdue_active_count"],
            1,
        )
        self.assertEqual(
            result["metrics"]["unassigned_active_count"],
            1,
        )
        self.assertEqual(
            result["warnings"][0]["code"],
            "unassigned_active_order",
        )

    def test_queryset_filter_is_preserved(self):
        included = self.make_order(price=Decimal("10.00"))
        self.make_order(price=Decimal("900.00"))

        result = build_order_report(
            Order.objects.filter(pk=included.pk),
            as_of=self.as_of,
        )

        self.assertTrue(result["metrics_allowed"])
        self.assertEqual(result["metrics"]["order_count"], 1)
        self.assertEqual(
            result["metrics"]["total_order_value"],
            "10.00",
        )

    def test_empty_database_selection(self):
        self.make_order()

        result = build_order_report(
            Order.objects.none(),
            as_of=self.as_of,
        )

        self.assertTrue(result["is_empty"])
        self.assertFalse(result["metrics_allowed"])
        self.assertIsNone(result["metrics"])

    def test_invalid_database_status_blocks_metrics(self):
        order = self.make_order()
        Order.objects.filter(pk=order.pk).update(status="unknown")

        result = build_order_report(
            Order.objects.all(),
            as_of=self.as_of,
        )

        self.assertFalse(result["metrics_allowed"])
        self.assertIsNone(result["metrics"])
        self.assertTrue(any(
            error["code"] == "invalid_status"
            and error["order_id"] == order.pk
            for error in result["errors"]
        ))

    def test_report_does_not_change_database_records(self):
        order = self.make_order()
        Payment.objects.create(
            order=order,
            kind="receipt",
            amount=Decimal("10.00"),
            note="Тестовая оплата",
            token=uuid.uuid4(),
            actor=self.user,
        )
        Event.objects.create(
            order=order,
            actor=self.user,
            message="Тестовое событие",
        )
        before = self.snapshot()

        result = build_order_report(
            Order.objects.all(),
            as_of=self.as_of,
        )

        self.assertTrue(result["metrics_allowed"])
        self.assertEqual(self.snapshot(), before)

    def test_database_report_is_json_serializable(self):
        self.make_order()

        result = build_order_report(
            Order.objects.all(),
            as_of=self.as_of,
        )
        restored = json.loads(
            json.dumps(result, ensure_ascii=False)
        )

        self.assertEqual(restored, result)
        self.assertEqual(
            restored["metrics"]["total_order_value"],
            "1000.10",
        )
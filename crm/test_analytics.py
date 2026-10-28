from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from django.test import SimpleTestCase

from .analytics import analyze_orders


class AnalyticsTests(SimpleTestCase):
    def setUp(self):
        self.as_of = date(2026, 10, 28)

    def make_row(self, **changes):
        row = {
            "order_id": 1,
            "created_at": datetime(2026, 10, 20, tzinfo=timezone.utc),
            "status": "accepted",
            "master_id": 10,
            "due_date": self.as_of,
            "price": Decimal("1000.10"),
        }
        row.update(changes)
        return row

    def assert_blocked(self, rows, expected_code):
        result = analyze_orders(rows, as_of=self.as_of)
        self.assertFalse(result["metrics_allowed"])
        self.assertIsNone(result["metrics"])
        self.assertIn(
            expected_code,
            [error["code"] for error in result["errors"]],
        )
        return result

    def test_valid_rows(self):
        rows = [
            self.make_row(price=Decimal("0.10")),
            self.make_row(
                order_id=2,
                status="work",
                price=Decimal("0.20"),
            ),
        ]
        result = analyze_orders(rows, as_of=self.as_of)

        self.assertTrue(result["metrics_allowed"])
        self.assertFalse(result["is_empty"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["metrics"], {
            "order_count": 2,
            "orders_by_status": {
                "accepted": 1,
                "approval": 0,
                "work": 1,
                "ready": 0,
                "delivered": 0,
                "cancelled": 0,
            },
            "total_order_value": "0.30",
            "unassigned_active_count": 0,
            "overdue_active_count": 0,
        })

    def test_empty_rows(self):
        result = analyze_orders([], as_of=self.as_of)

        self.assertTrue(result["is_empty"])
        self.assertFalse(result["metrics_allowed"])
        self.assertIsNone(result["metrics"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(
            result["message"],
            "Нет заказов по выбранным условиям",
        )

    def test_each_required_field_is_checked(self):
        for field in self.make_row():
            with self.subTest(field=field):
                row = self.make_row()
                del row[field]
                self.assert_blocked(
                    [row],
                    f"missing_fields:{field}",
                )

    def test_duplicate_id_blocks_all_metrics(self):
        result = self.assert_blocked(
            [self.make_row(), self.make_row()],
            "duplicate_order_id",
        )
        self.assertEqual(result["errors"][0]["row"], 2)
        self.assertEqual(result["errors"][0]["order_id"], 1)

    def test_invalid_order_ids(self):
        for value in [None, 0, -1, True, "1", []]:
            with self.subTest(value=value):
                self.assert_blocked(
                    [self.make_row(order_id=value)],
                    "invalid_order_id",
                )

    def test_unknown_or_invalid_status(self):
        for value in ["unknown", None, []]:
            with self.subTest(value=value):
                self.assert_blocked(
                    [self.make_row(status=value)],
                    "invalid_status",
                )

    def test_invalid_price(self):
        values = [
            None,
            "100.00",
            100.0,
            Decimal("-0.01"),
            Decimal("NaN"),
            Decimal("Infinity"),
            Decimal("-Infinity"),
        ]
        for value in values:
            with self.subTest(value=str(value)):
                self.assert_blocked(
                    [self.make_row(price=value)],
                    "invalid_price",
                )

    def test_price_precision_and_range(self):
        for value, code in [
            (Decimal("1.001"), "invalid_price_precision"),
            (Decimal("10000000000.00"), "price_out_of_range"),
        ]:
            with self.subTest(value=value):
                self.assert_blocked(
                    [self.make_row(price=value)],
                    code,
                )

    def test_zero_price_is_valid(self):
        result = analyze_orders(
            [self.make_row(price=Decimal("0.00"))],
            as_of=self.as_of,
        )
        self.assertTrue(result["metrics_allowed"])
        self.assertEqual(
            result["metrics"]["total_order_value"],
            "0.00",
        )

    def test_missing_master_warns_but_preserves_order(self):
        result = analyze_orders(
            [self.make_row(master_id=None)],
            as_of=self.as_of,
        )
        self.assertTrue(result["metrics_allowed"])
        self.assertEqual(result["metrics"]["order_count"], 1)
        self.assertEqual(
            result["metrics"]["unassigned_active_count"],
            1,
        )
        self.assertEqual(
            result["warnings"][0]["code"],
            "unassigned_active_order",
        )

    def test_invalid_master_ids(self):
        for value in [0, -1, True, "10"]:
            with self.subTest(value=value):
                self.assert_blocked(
                    [self.make_row(master_id=value)],
                    "invalid_master_id",
                )

    def test_overdue_boundary_for_each_status(self):
        active = {"accepted", "approval", "work"}
        statuses = (
            "accepted", "approval", "work",
            "ready", "delivered", "cancelled",
        )
        for status in statuses:
            for offset in [-1, 0, 1]:
                with self.subTest(status=status, offset=offset):
                    result = analyze_orders(
                        [self.make_row(
                            status=status,
                            due_date=self.as_of + timedelta(days=offset),
                        )],
                        as_of=self.as_of,
                    )
                    self.assertTrue(result["metrics_allowed"])
                    expected = int(status in active and offset < 0)
                    self.assertEqual(
                        result["metrics"]["overdue_active_count"],
                        expected,
                    )

    def test_invalid_due_date(self):
        for value in [
            None,
            "2026-10-28",
            datetime(2026, 10, 28, tzinfo=timezone.utc),
        ]:
            with self.subTest(value=value):
                self.assert_blocked(
                    [self.make_row(due_date=value)],
                    "invalid_due_date",
                )

    def test_created_at_requires_aware_datetime(self):
        for value in [
            None,
            self.as_of,
            "2026-10-20",
            datetime(2026, 10, 20),
        ]:
            with self.subTest(value=value):
                self.assert_blocked(
                    [self.make_row(created_at=value)],
                    "invalid_created_at",
                )

    def test_non_dictionary_row(self):
        self.assert_blocked([None], "invalid_row")

    def test_input_is_not_changed(self):
        rows = [self.make_row(), self.make_row(order_id=2)]
        original = deepcopy(rows)

        analyze_orders(rows, as_of=self.as_of)

        self.assertEqual(rows, original)

    def test_as_of_requires_date_not_datetime(self):
        for value in [
            None,
            "2026-10-28",
            datetime(2026, 10, 28, tzinfo=timezone.utc),
        ]:
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    analyze_orders([], as_of=value)
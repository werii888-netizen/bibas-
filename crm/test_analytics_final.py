from datetime import timedelta
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .test_analytics_integration import AnalyticsIntegrationTests


class AnalyticsFinalTests(TestCase):
    setUp = AnalyticsIntegrationTests.setUp
    make_order = AnalyticsIntegrationTests.make_order

    def test_report_with_real_database_order(self):
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save(update_fields=["is_superuser", "is_staff"])
        self.client.force_login(self.user)
        self.make_order(
            price=Decimal("1500.00"),
            status="accepted",
            master=None,
            due_date=timezone.localdate() - timedelta(days=1),
        )
        response = self.client.get(reverse("analytics-report"))
        self.assertEqual(response.status_code, 200)
        report = response.context["report"]
        self.assertTrue(report["metrics_allowed"])
        self.assertEqual(report["errors"], [])
        metrics = report["metrics"]
        self.assertEqual(metrics["order_count"], 1)
        self.assertEqual(metrics["total_order_value"], "1500.00")
        self.assertEqual(metrics["unassigned_active_count"], 1)
        self.assertEqual(metrics["overdue_active_count"], 1)
        self.assertEqual(metrics["orders_by_status"]["accepted"], 1)
        self.assertTrue(any(
            issue["code"] == "unassigned_active_order"
            for issue in report["warnings"]
        ))
        self.assertContains(response, "1500.00")
        self.assertContains(response, reverse("analytics-report"))
        self.assertContains(response, "\u0430\u043a\u0442\u0438\u0432\u043d\u044b\u0439 \u0437\u0430\u043a\u0430\u0437 \u0431\u0435\u0437 \u043c\u0430\u0441\u0442\u0435\u0440\u0430")
        self.assertTemplateUsed(response, "crm/analytics.html")

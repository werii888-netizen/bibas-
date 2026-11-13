import json
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from django.urls import reverse


class AgentHealthTests(SimpleTestCase):
    def test_manifest_endpoint_returns_public_liveness_without_database_access(self):
        manifest = json.loads(
            (Path(settings.BASE_DIR) / "mcp-tools" / "tools.json").read_text(encoding="utf-8")
        )
        tool = next(tool for tool in manifest["tools"] if tool["name"] == "health_check")
        self.assertEqual(tool["endpoint"], reverse("agent-health"))
        response = self.client.get(tool["endpoint"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "status": "ok",
            "services": {},
            "metrics": {},
            "message": "Health check stub: HTTP handler is available; services are not checked.",
        })

    def test_verbose_reports_unchecked_services(self):
        response = self.client.get(reverse("agent-health"), {"verbose": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["services"], {
            "database": "not_checked", "task_queue": "not_checked",
        })
        response = self.client.get(reverse("agent-health"), {"verbose": "false"})
        self.assertEqual(response.json()["services"], {})

    def test_invalid_verbose_is_rejected(self):
        response = self.client.get(reverse("agent-health"), {"verbose": "invalid"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("message", response.json())

    def test_write_methods_are_rejected(self):
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(reverse("agent-health"))
                self.assertEqual(response.status_code, 405)

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class AnalyticsPageTests(TestCase):
    def setUp(self):
        self.url = reverse("analytics-report")
        self.owner = get_user_model().objects.create_superuser(
            username="analytics-owner",
            password="test-only-password",
        )
        self.reader = get_user_model().objects.create_user(
            username="analytics-reader",
        )

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_regular_user_forbidden(self):
        self.client.force_login(self.reader)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_owner_sees_empty_report(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "crm/analytics.html")
        self.assertContains(response, "<h1>\u0410\u043d\u0430\u043b\u0438\u0442\u0438\u043a\u0430 \u0437\u0430\u043a\u0430\u0437\u043e\u0432</h1>", html=True)
        self.assertContains(response, "\u041d\u0435\u0442 \u0437\u0430\u043a\u0430\u0437\u043e\u0432 \u043f\u043e \u0432\u044b\u0431\u0440\u0430\u043d\u043d\u044b\u043c \u0443\u0441\u043b\u043e\u0432\u0438\u044f\u043c")
        self.assertIsNone(response.context["report"]["metrics"])
        self.assertIn("no-store", response["Cache-Control"])

    def test_post_not_allowed(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 405)

    def test_navigation_visibility(self):
        from django.template.loader import render_to_string
        from django.test import RequestFactory
        request = RequestFactory().get("/")
        for user, visible in ((self.owner, True), (self.reader, False)):
            with self.subTest(username=user.username):
                request.user = user
                html = render_to_string(
                    "base.html", {"user": user}, request=request
                )
                link = 'href="' + self.url + '"'
                self.assertEqual(link in html, visible)

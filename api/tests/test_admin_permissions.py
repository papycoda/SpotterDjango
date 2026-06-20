from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase


class OperationsAdminPermissionTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.regular_user = user_model.objects.create_user(
            username="operator",
            password="test-password",
        )
        cls.staff_user = user_model.objects.create_user(
            username="admin-operator",
            password="test-password",
            is_staff=True,
        )

    def operation_requests(self):
        return (
            ("post", reverse("admin-geocode-stations"), {}),
            ("get", reverse("admin-geocode-status"), None),
        )

    def make_request(self, method, url, data):
        return getattr(self.client, method)(url, data=data, format="json")

    def test_anonymous_users_cannot_access_operations_endpoints(self):
        for method, url, data in self.operation_requests():
            with self.subTest(method=method, url=url):
                response = self.make_request(method, url, data)
                self.assertIn(
                    response.status_code,
                    (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
                )

    def test_authenticated_non_staff_users_cannot_access_operations_endpoints(self):
        self.client.force_authenticate(self.regular_user)

        for method, url, data in self.operation_requests():
            with self.subTest(method=method, url=url):
                response = self.make_request(method, url, data)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_users_pass_the_operations_permission_boundary(self):
        self.client.force_authenticate(self.staff_user)

        for method, url, data in self.operation_requests():
            with self.subTest(method=method, url=url):
                response = self.make_request(method, url, data)
                self.assertNotIn(
                    response.status_code,
                    (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
                )

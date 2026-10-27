from django.urls import path
from . import views
urlpatterns = [
    path("", views.orders, name="orders"),
    path("clients/", views.clients, name="clients"),
    path("clients/new/", views.client_create, name="client-create"),
    path("orders/new/", views.order_create, name="order-create"),
    path("orders/<int:pk>/", views.order_detail, name="order"),
    path("orders/<int:pk>/status/", views.status_update, name="status-update"),
    path("orders/<int:pk>/payments/", views.payment_create, name="payment-create"),
]

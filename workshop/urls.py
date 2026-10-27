from django.urls import path
from . import views

urlpatterns = [
    path("start/", views.start, name="start"),
    path("risks/", views.risks, name="risks"),
    path("orders/<int:pk>/approve-price/", views.price_approve, name="price-approve"),
    path("metals/", views.metals, name="metals"),
    path("metals/new/", views.metal_account_create, name="metal-account-create"),
    path("metals/movement/", views.metal_movement_create, name="metal-movement-create"),
    path("designs/", views.designs, name="designs"),
    path("designs/new/", views.design_create, name="design-create"),
    path("designs/<int:pk>/", views.design_detail, name="design"),
    path("designs/<int:pk>/variant/", views.design_variant, name="design-variant"),
    path("designs/<int:pk>/export/", views.design_export, name="design-export"),
    path("designs/<int:pk>/jobs/", views.job_create, name="job-create"),
    path("design-jobs/<int:pk>/image/", views.job_image, name="job-image"),
]

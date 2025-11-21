from django.urls import path
from . import views

urlpatterns = [
    path("", views.raffle_detail, name="raffle_detail"),
    path("api/check/", views.check_number, name="check_number"),
    path("api/grid/", views.grid_page, name="grid_page"),

    # Export CSV (solo staff)
    path("admin/export/raffle/<int:raffle_id>/tickets.csv", views.export_tickets_csv, name="export_tickets_csv"),
    path("admin/export/raffle/<int:raffle_id>/payments.csv", views.export_payments_csv, name="export_payments_csv"),

    # MP
    path("mp/create_preference/", views.create_preference, name="create_preference"),
    path("webhook/mercadopago/", views.mp_webhook, name="mp_webhook"),
    path("transfer/reserve/", views.transfer_reserve, name="transfer_reserve")
]


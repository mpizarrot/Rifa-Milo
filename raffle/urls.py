from django.urls import path
from . import views

urlpatterns = [
    path("", views.raffle_detail, name="raffle_detail"),
    path("api/check/", views.check_number, name="check_number"),
    path("api/grid/", views.grid_page, name="grid_page"),

    # Export CSV (solo staff)
    path("export/raffle/<int:raffle_id>/tickets.csv", views.export_tickets_csv, name="export_tickets_csv"),
    path("export/raffle/<int:raffle_id>/payments.csv", views.export_payments_csv, name="export_payments_csv"),

    path("transfer/reserve/", views.transfer_reserve, name="transfer_reserve"),
    path("donar/", views.donation_page, name="donation_page"),
    path("premios/", views.prizes_page, name="prizes_page"),
    path("pago-exitoso/", views.payment_success, name="payment_success"),
]


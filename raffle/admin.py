from django.contrib import admin
from .models import Raffle, Ticket, Payment

@admin.register(Raffle)
class RaffleAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "price_clp", "numbers_total", "is_active", "starts_at", "ends_at")
    list_filter = ("is_active",)
    search_fields = ("title",)

@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("id", "raffle", "number", "buyer_name", "buyer_email", "created_at")
    list_filter = ("raffle",)
    search_fields = ("buyer_name", "buyer_email")
    ordering = ("raffle", "number")

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "raffle", "gateway", "gateway_payment_id", "status",
                    "amount_clp", "buyer_name", "buyer_email", "created_at", "paid_at")
    list_filter = ("raffle", "status", "gateway")
    search_fields = ("buyer_name", "buyer_email", "gateway_payment_id")
    readonly_fields = ("metadata",)


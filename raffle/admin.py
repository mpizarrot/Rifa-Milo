from django.contrib import admin, messages

from .models import Raffle, Payment, Ticket
from .views import _confirm_tickets_from_payment_id


@admin.action(description="Marcar como pagados y crear tickets")
def mark_as_paid_and_create_tickets(modeladmin, request, queryset):
    """
    Admin action para:
    - Marcar el Payment como paid
    - Crear los Tickets a partir de metadata['chosen_numbers'].
    Pensado especialmente para pagos por transferencia.
    """
    count = 0
    for p in queryset:
        ok = _confirm_tickets_from_payment_id(p.gateway_payment_id)
        if ok:
            count += 1
        else:
            messages.warning(request, f"No se pudieron confirmar tickets para {p.gateway_payment_id}")
    messages.success(request, f"Se marcaron {count} pagos como pagados y se crearon los tickets.")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "raffle",
        "gateway",
        "status",
        "amount_clp",
        "buyer_name",
        "buyer_email",
        "created_at",
        "expires_at",
        "reserved_numbers",
    )
    list_filter = ("gateway", "status", "raffle")
    search_fields = ("buyer_name", "buyer_email", "gateway_payment_id")
    actions = [mark_as_paid_and_create_tickets]

    def reserved_numbers(self, obj):
        meta = obj.metadata or {}
        nums = meta.get("chosen_numbers", [])
        if not nums:
            if getattr(obj, "chosen_number", None):
                return str(obj.chosen_number)
            return "-"
        return ", ".join(str(n) for n in nums)
    reserved_numbers.short_description = "Números reservados"


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("id", "raffle", "number", "buyer_name", "buyer_email", "created_at")
    list_filter = ("raffle",)
    search_fields = ("buyer_name", "buyer_email", "number")


@admin.register(Raffle)
class RaffleAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "price_clp", "numbers_total", "is_active")
    list_filter = ("is_active",)
    search_fields = ("title",)
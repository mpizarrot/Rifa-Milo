from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html

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
    count_ok = 0

    for p in queryset:
        try:
            ok = _confirm_tickets_from_payment_id(p.gateway_payment_id)
        except Exception as e:
            messages.error(
                request,
                (
                    f"Error al confirmar tickets para payment ID={p.id} "
                    f"(gateway_payment_id={p.gateway_payment_id}): {e!r}"
                ),
            )
            continue

        if not ok:
            messages.warning(
                request,
                f"No se encontró el payment {p.gateway_payment_id} para confirmar.",
            )
            continue

        # Revisar metadata por conflictos
        meta = p.metadata or {}
        conflict = meta.get("conflict_numbers") or []
        paid_nums = meta.get("paid_numbers") or meta.get("chosen_numbers") or []

        if conflict:
            messages.warning(
                request,
                (
                    f"Payment {p.id} marcado como pagado. "
                    f"Se emitieron tickets para: {', '.join(str(n) for n in paid_nums)}. "
                    f"Los siguientes números ya estaban vendidos: "
                    f"{', '.join(str(n) for n in conflict)}. "
                    f"Contacta a la persona para ofrecer otros números o devolver esa parte."
                ),
            )
        else:
            messages.success(
                request,
                (
                    f"Payment {p.id} marcado como pagado y tickets creados para: "
                    f"{', '.join(str(n) for n in paid_nums)}."
                ),
            )

        count_ok += 1

    if count_ok > 1:
        messages.info(request, f"Se procesaron {count_ok} pagos.")


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
    list_display = (
        "id",
        "title",
        "price_clp",
        "numbers_total",
        "is_active",
        "export_links",
    )
    list_filter = ("is_active",)
    search_fields = ("title",)

    def export_links(self, obj):
        url_tickets = reverse("export_tickets_csv", args=[obj.id])
        url_payments = reverse("export_payments_csv", args=[obj.id])
        return format_html(
            '<a href="{}">Tickets CSV</a> | <a href="{}">Pagos CSV</a>',
            url_tickets,
            url_payments,
        )

    export_links.short_description = "Exportar"
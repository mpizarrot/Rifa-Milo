import json, csv
from datetime import timedelta
from uuid import uuid4

from math import ceil
from django.conf import settings
from django.shortcuts import render
from django.http import JsonResponse, HttpResponse, HttpResponseBadRequest
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import ensure_csrf_cookie
from django_ratelimit.decorators import ratelimit
from django.db import transaction, IntegrityError
from django.utils import timezone
from django.urls import reverse
from django.template.loader import render_to_string
from django.contrib.admin.views.decorators import staff_member_required

from .models import Raffle, Ticket, Payment


# ========= Utilidades comunes =========

ZERO_DECIMAL_CURRENCIES = {"CLP", "JPY", "PYG"}

def _get_active_raffle():
    return Raffle.objects.filter(is_active=True).order_by("id").first()

def _get_taken_numbers_for_raffle(raffle: Raffle) -> set[int]:
    """
    Devuelve un set con todos los números que deben aparecer como 'tomados':
    - Tickets ya emitidos (pagos confirmados).
    - Reservas por transferencia pendientes y no vencidas.
    """
    taken = set(
        Ticket.objects.filter(raffle=raffle).values_list("number", flat=True)
    )

    now = timezone.now()
    pending_transfers = Payment.objects.filter(
        raffle=raffle,
        gateway="transfer",
        status="pending",
        expires_at__gt=now,
    )

    for p in pending_transfers:
        meta = p.metadata or {}
        if isinstance(meta, dict):
            nums = meta.get("chosen_numbers", [])
            for n in nums:
                try:
                    taken.add(int(n))
                except (TypeError, ValueError):
                    continue

    return taken

# ========= Vistas HTML =========

PAGE_SIZE = 100  # 10 x 10

@ensure_csrf_cookie
@require_GET
def raffle_detail(request):
    raffle = _get_active_raffle()
    if not raffle:
        return render(request, "raffle/detail.html", {
            "raffle": None,
        })

    total = raffle.numbers_total
    page_count = ceil(total / PAGE_SIZE)
    current_page = 1
    start = (current_page - 1) * PAGE_SIZE + 1
    end = min(start + PAGE_SIZE - 1, total)

    taken = list(_get_taken_numbers_for_raffle(raffle))
    first_page_numbers = range(start, end + 1)

    return render(request, "raffle/detail.html", {
        "raffle": raffle,
        "taken": taken,
        "page_count": page_count,
        "current_page": current_page,
        "page_size": PAGE_SIZE,
        "first_page_numbers": first_page_numbers,
    })


@require_GET
def grid_page(request):
    raffle = _get_active_raffle()
    if not raffle:
        return HttpResponseBadRequest("No hay rifa")

    try:
        page = int(request.GET.get("page", "1"))
    except ValueError:
        page = 1
    page = max(1, page)

    total = raffle.numbers_total
    page_count = ceil(total / PAGE_SIZE)
    page = min(page, page_count)

    start = (page - 1) * PAGE_SIZE + 1
    end = min(start + PAGE_SIZE - 1, total)

    taken = _get_taken_numbers_for_raffle(raffle)
    numbers = range(start, end + 1)

    html = render_to_string("raffle/_grid.html", {
        "numbers": numbers,
        "taken": taken,
        "current_page": page,
        "page_count": page_count,
    })
    return HttpResponse(html)


@require_GET
def check_number(request):
    raffle = _get_active_raffle()
    if not raffle:
        return HttpResponse("No hay rifa activa", status=400)
    try:
        n = int(request.GET.get("number", ""))
    except ValueError:
        return HttpResponseBadRequest("Número inválido")

    if n < 1 or n > raffle.numbers_total:
        return HttpResponseBadRequest("Fuera de rango")

    exists = Ticket.objects.filter(raffle=raffle, number=n).exists()
    if exists:
        return HttpResponse('<span class="text-red-600">No disponible</span>')
    return HttpResponse('<span class="text-green-600">Disponible</span>')


# ========= Confirmación de pago → creación de tickets =========

def _confirm_tickets_from_payment_id(gateway_payment_id: str):
    """
    Marca Payment como paid (idempotente) y crea Tickets para cada número
    en metadata['chosen_numbers'] (o 'chosen_number' legacy).

    - Si algunos números ya están vendidos (por otro Payment),
      crea tickets solo para los disponibles.
    - Guarda en metadata:
      * 'chosen_numbers': lo que se intentó comprar
      * 'paid_numbers': números para los que SÍ hay ticket asociado a este Payment
      * 'conflict_numbers': números que ya tenían ticket de otra persona
    """
    with transaction.atomic():
        try:
            p = Payment.objects.select_for_update().get(
                gateway_payment_id=gateway_payment_id
            )
        except Payment.DoesNotExist:
            return False

        # Números originales que se intentaron comprar
        chosen_numbers: list[int] = []
        if isinstance(p.metadata, dict) and "chosen_numbers" in p.metadata:
            try:
                chosen_numbers = [int(x) for x in p.metadata.get("chosen_numbers", [])]
            except (TypeError, ValueError):
                chosen_numbers = []
        elif getattr(p, "chosen_number", None):
            try:
                chosen_numbers = [int(p.chosen_number)]
            except (TypeError, ValueError):
                chosen_numbers = []

        if not chosen_numbers:
            # No hay números, solo marcamos como pagado
            if p.status != "paid":
                p.status = "paid"
                p.paid_at = timezone.now()
                p.save()
            return True

        paid_numbers: list[int] = []
        conflict_numbers: list[int] = []

        # Intentar crear/obtener ticket para cada número
        for n in chosen_numbers:
            ticket, created = Ticket.objects.get_or_create(
                raffle=p.raffle,
                number=n,
                defaults={
                    "payment": p,
                    "buyer_name": p.buyer_name,
                    "buyer_email": p.buyer_email,
                    "buyer_phone": p.buyer_phone,
                },
            )
            if created:
                # Ticket nuevo asignado a este Payment
                paid_numbers.append(n)
            else:
                # Ticket ya existía: conflicto (otro pago se quedó con ese número)
                # Solo lo consideramos conflicto si NO está ya asociado a este mismo Payment
                if ticket.payment_id != p.id:
                    conflict_numbers.append(n)
                else:
                    # Ya existía un ticket de este mismo Payment (idempotencia)
                    paid_numbers.append(n)

        # Actualizar metadata con el resultado
        meta = p.metadata or {}
        if not isinstance(meta, dict):
            meta = {}

        meta.setdefault("chosen_numbers", chosen_numbers)
        meta["paid_numbers"] = sorted(set(paid_numbers))
        meta["conflict_numbers"] = sorted(set(conflict_numbers))

        p.metadata = meta

        # 4) Marcar como pagado si no lo estaba
        if p.status != "paid":
            p.status = "paid"
            p.paid_at = timezone.now()

        p.save()

    return True

# ========= Reservar Transferencia 12 horas =========

@require_POST
@ratelimit(key="ip", rate="10/m", block=True)
def transfer_reserve(request):
    """
    Reserva números para pago por transferencia por 12 horas.
    No crea tickets; solo un Payment 'pending' con gateway='transfer'.
    Cuando confirmes manualmente la transferencia, podrás marcarlo como 'paid'
    y usar _confirm_tickets_from_payment_id para generar los Tickets.
    """
    raffle = _get_active_raffle()
    if not raffle:
        return JsonResponse({"error": "No hay rifa activa"}, status=400)

    try:
        data = json.loads(request.body.decode())
    except Exception:
        return JsonResponse({"error": "JSON inválido"}, status=400)

    chosen_numbers = data.get("chosen_numbers") or []
    buyer = data.get("buyer") or {}

    # Validaciones básicas
    if not chosen_numbers:
        return JsonResponse({"error": "Debes seleccionar al menos un número"}, status=400)

    # Normalizar a int
    try:
        chosen_numbers = [int(n) for n in chosen_numbers]
    except (TypeError, ValueError):
        return JsonResponse({"error": "Números inválidos"}, status=400)

    # Filtrar por rango válido
    for n in chosen_numbers:
        if n < 1 or n > raffle.numbers_total:
            return JsonResponse({"error": f"Número fuera de rango: {n}"}, status=400)

    name = (buyer.get("name") or "").strip()
    email = (buyer.get("email") or "").strip()
    phone = (buyer.get("phone") or "").strip()

    if not name or not email or "@" not in email:
        return JsonResponse(
            {"error": "Debes ingresar nombre y un correo válido"},
            status=400,
        )
    
    # límite máximo de números por reserva
    if len(chosen_numbers) > 50:
        return JsonResponse({"error": "No puedes reservar más de 50 números por transferencia"}, status=400)

    # opcional: limitar reservas por email en ventana de tiempo
    recent_pending = Payment.objects.filter(
        raffle=raffle,
        gateway="transfer",
        status="pending",
        buyer_email=email,
        created_at__gte=timezone.now() - timedelta(hours=24),
    ).count()
    if recent_pending >= 5:
        return JsonResponse(
            {"error": "Has realizado demasiadas reservas por transferencia en las últimas 24 horas."},
            status=429,
        )

    client_ip = request.META.get("REMOTE_ADDR")
    user_agent = request.META.get("HTTP_USER_AGENT", "")

    now = timezone.now()
    expires_at = now + timedelta(hours=12)

    with transaction.atomic():
        # Recalcular taken dentro de la transacción para evitar carreras
        taken = _get_taken_numbers_for_raffle(raffle)
        conflict = taken.intersection(chosen_numbers)
        if conflict:
            return JsonResponse(
                {
                    "error": "Algunos números ya no están disponibles. Recarga la página para verlos.",
                    "conflict_numbers": sorted(conflict),
                },
                status=409,
            )

        total = int(raffle.price_clp) * len(chosen_numbers)
        gateway_payment_id = f"transfer-{raffle.id}-{uuid4()}"

        Payment.objects.create(
            raffle=raffle,
            amount_clp=total,
            gateway="transfer",
            gateway_payment_id=gateway_payment_id,
            status="pending",
            buyer_name=name,
            buyer_email=email,
            buyer_phone=phone,
            expires_at=expires_at,
            metadata={
                "chosen_numbers": chosen_numbers,
                "payment_method": "transfer",
                "client_ip": client_ip,
                "user_agent": user_agent,
            },
        )

    success_url = reverse("payment_success") + "?kind=transfer"

    return JsonResponse(
        {
            "ok": True,
            "reserved_until": expires_at.isoformat(),
            "count": len(chosen_numbers),
            "redirect_url": success_url,
        }
    )

@require_POST
@ratelimit(key="ip", rate="10/m", block=True)
def reserve_from_failed_payment(request):
    """
    Toma el external_reference de un intento fallido de Mercado Pago,
    obtiene los números que el usuario intentó comprar y los reserva
    como pago por transferencia (Payment gateway='transfer').
    """
    try:
        body = json.loads(request.body.decode() or "{}")
    except Exception:
        return JsonResponse({"error": "JSON inválido"}, status=400)

    external_ref = (body.get("external_reference") or "").strip()
    if not external_ref:
        return JsonResponse({"error": "Falta external_reference"}, status=400)

    try:
        # Payment creado cuando se generó la preferencia de MP
        p_mp = Payment.objects.get(
            gateway="mercadopago",
            gateway_payment_id=external_ref,
        )
    except Payment.DoesNotExist:
        return JsonResponse(
            {"error": "No encontramos tu intento de pago. Vuelve a elegir tus números."},
            status=404,
        )

    raffle = p_mp.raffle

    # Números que intentó comprar
    chosen_numbers = []
    if isinstance(p_mp.metadata, dict) and "chosen_numbers" in p_mp.metadata:
        try:
            chosen_numbers = [int(n) for n in p_mp.metadata.get("chosen_numbers", [])]
        except (TypeError, ValueError):
            chosen_numbers = []

    if not chosen_numbers:
        return JsonResponse(
            {"error": "No hay números asociados a este intento de pago."},
            status=400,
        )

    now = timezone.now()
    expires_at = now + timedelta(hours=12)

    with transaction.atomic():
        # Verificar que sigan disponibles
        taken = _get_taken_numbers_for_raffle(raffle)
        conflict = taken.intersection(chosen_numbers)
        if conflict:
            return JsonResponse(
                {
                    "error": "Algunos números ya no están disponibles. Recarga la página para verlos.",
                    "conflict_numbers": sorted(conflict),
                },
                status=409,
            )

        total = int(raffle.price_clp) * len(chosen_numbers)

        # Crear Payment de transferencia
        transfer_payment = Payment.objects.create(
            raffle=raffle,
            amount_clp=total,
            gateway="transfer",
            gateway_payment_id=f"transfer-{raffle.id}-{uuid4()}",
            status="pending",
            buyer_name=p_mp.buyer_name,
            buyer_email=p_mp.buyer_email,
            buyer_phone=p_mp.buyer_phone,
            metadata={
                "chosen_numbers": chosen_numbers,
                "from_external_reference": external_ref,
            },
            expires_at=expires_at,
        )

        # Marcar el intento de MP como failed (por si no lo estaba)
        if p_mp.status != "failed":
            p_mp.status = "failed"
            p_mp.save(update_fields=["status"])

    return JsonResponse(
        {
            "ok": True,
            "count": len(chosen_numbers),
            "chosen_numbers": chosen_numbers,
            "reserved_until": expires_at.isoformat(),
            "payment_id": transfer_payment.id,
        }
    )

# ============== exportación csv ======================== #

@staff_member_required
def export_tickets_csv(request, raffle_id: int):
    raffle = Raffle.objects.filter(id=raffle_id).first()
    if not raffle:
        return HttpResponseBadRequest("Rifa no existe")

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="tickets_raffle_{raffle_id}.csv"'
    writer = csv.writer(resp)
    writer.writerow(["raffle_id", "number", "buyer_name", "buyer_email", "buyer_phone", "created_at", "payment_id"])

    qs = Ticket.objects.filter(raffle_id=raffle_id).select_related("payment").order_by("number")
    for t in qs:
        writer.writerow([t.raffle_id, t.number, t.buyer_name, t.buyer_email, t.buyer_phone, t.created_at, t.payment_id])
    return resp

@staff_member_required
def export_payments_csv(request, raffle_id: int):
    raffle = Raffle.objects.filter(id=raffle_id).first()
    if not raffle:
        return HttpResponseBadRequest("Rifa no existe")

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="payments_raffle_{raffle_id}.csv"'
    writer = csv.writer(resp)
    writer.writerow(["raffle_id","status","amount_clp","gateway","gateway_payment_id",
                     "buyer_name","buyer_email","buyer_phone","created_at","paid_at"])

    qs = Payment.objects.filter(raffle_id=raffle_id).order_by("-created_at")
    for p in qs:
        writer.writerow([p.raffle_id, p.status, p.amount_clp, p.gateway, p.gateway_payment_id,
                         p.buyer_name, p.buyer_email, p.buyer_phone, p.created_at, p.paid_at])
    return resp

# ============== donaciones ======================== #

@ensure_csrf_cookie
@require_GET
def donation_page(request):
    """
    Página simple para recibir donaciones (sin elegir números).
    """
    raffle = _get_active_raffle()
    return render(request, "raffle/donate.html", {
        "raffle": raffle,
    })

# ============== premios ======================== #

@require_GET
def prizes_page(request):
    """
    Página con el listado completo de premios de la rifa.
    """
    raffle = _get_active_raffle()

    prizes = [
        {
            "name": "Pase diario a Lollapalooza Chile 2026 (viernes 13 de marzo)",
            "image": "img/prizes/lollapalooza.jpg",
            "description": "Un pase diario para vivir Lollapalooza Chile 2026 el día viernes 13 de marzo."
        },
        {
            "name": "$50.000 CLP",
            "image": "img/prizes/50000clp.png",
            "description": "Premio en dinero por un valor de $50.000 CLP."
        },
        {
            "name": "Plancha de pelo",
            "image": "img/prizes/plancha_pelo.jpg",
            "description": "Plancha de pelo para lucir un look increíble."
        },
        {
            "name": "Sesión de limpieza facial",
            "image": "img/prizes/limpieza_facial.jpg",
            "description": "Una sesión de limpieza facial para cuidar tu piel."
        },
        {
            "name": "Tabla de picar (grande)",
            "image": "img/prizes/tabla_picar_grande.png",
            "description": "Hermosa tabla de picar artesanal hecha con madera nativa."
        },
        {
            "name": "Tabla de picar (pequeña)",
            "image": "img/prizes/tabla_picar_pequena.png",
            "description": "Versión pequeña de la tabla de picar artesanal, perfecta para el uso diario."
        },
        {
            "name": "Tabla de picoteo",
            "image": "img/prizes/tabla_picoteo.png",
            "description": "Tabla de picoteo artesanal ideal para compartir."
        },
        {
            "name": "Vaporizador facial",
            "image": "img/prizes/vaporizador_facial.jpg",
            "description": "Vaporizador facial ideal para rutinas de skincare."
        },
        {
            "name": "Vino Carmenere Gran Reserva",
            "image": "img/prizes/vino_carmenere.png",
            "description": "Botella de vino Carmenere Gran Reserva."
        },
        {
            "name": "Vino Cabernet Sauvignon",
            "image": "img/prizes/vino_cabernet.png",
            "description": "Botella de vino Cabernet Sauvignon para compartir."
        },
        {
            "name": "Torta 3 leches para 20 personas",
            "image": "img/prizes/torta.png",
            "description": "Deliciosa torta casera para celebrar con hasta 20 personas."
        },
        {
            "name": "Pan de Pascua",
            "image": "img/prizes/pan_pascua.jpg",
            "description": "Pan de pascua casero con frutos secos."
        }
    ]

    return render(request, "raffle/prizes.html", {
        "raffle": raffle,
        "prizes": prizes,
    })

@require_GET
def payment_success(request):
    """
    Página de confirmación genérica para reservas o pagos exitosos.
    """
    kind = request.GET.get("kind", "raffle")  # 'raffle', 'donation' o 'transfer'

    context = {
        "kind": kind,
        "is_donation": (kind == "donation"),
        "is_transfer": (kind == "transfer"),
    }
    return render(request, "raffle/payment_success.html", context)

@require_GET
def payment_failure(request):
    """
    Página limpia para cuando un intento de pago falla.
    """
    kind = request.GET.get("kind", "raffle")  # 'raffle' o 'donation'
    external_ref = request.GET.get("external_reference") or ""

    context = {
        "kind": kind,
        "is_donation": (kind == "donation"),
        "is_raffle": (kind == "raffle"),
        "external_reference": external_ref,
    }
    return render(request, "raffle/payment_failure.html", context)

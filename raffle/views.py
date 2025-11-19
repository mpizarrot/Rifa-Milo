import json, requests, csv, hmac, hashlib, urllib.parse

from math import ceil
from django.conf import settings
from django.shortcuts import render
from django.http import JsonResponse, HttpResponse, HttpResponseBadRequest
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt
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

def _mp_headers(idempotency_key: str | None = None):
    headers = {
        "Authorization": f"Bearer {settings.MP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    if idempotency_key:
        headers["X-Idempotency-Key"] = idempotency_key
    return headers


# ========= Vistas HTML =========

PAGE_SIZE = 100  # 10 x 10

@require_GET
def raffle_detail(request):
    raffle = _get_active_raffle()
    if not raffle:
        return render(request, "raffle/detail.html", {
            "raffle": None,
            "MP_PUBLIC_KEY": getattr(settings, "MP_PUBLIC_KEY", ""),
        })

    total = raffle.numbers_total
    page_count = ceil(total / PAGE_SIZE)
    current_page = 1
    start = (current_page - 1) * PAGE_SIZE + 1
    end = min(start + PAGE_SIZE - 1, total)

    taken = list(Ticket.objects.filter(raffle=raffle).values_list("number", flat=True))
    first_page_numbers = range(start, end + 1)

    return render(request, "raffle/detail.html", {
        "raffle": raffle,
        "taken": taken,
        "page_count": page_count,
        "current_page": current_page,
        "page_size": PAGE_SIZE,
        "first_page_numbers": first_page_numbers,
        "MP_PUBLIC_KEY": getattr(settings, "MP_PUBLIC_KEY", ""),
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

    taken = set(Ticket.objects.filter(raffle=raffle).values_list("number", flat=True))
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
    Requiere que Ticket.payment sea ForeignKey (no OneToOne).
    """
    with transaction.atomic():
        try:
            p = Payment.objects.select_for_update().get(gateway_payment_id=gateway_payment_id)
        except Payment.DoesNotExist:
            return False

        # Marcar pagado si aún no
        if p.status != "paid":
            p.status = "paid"
            p.paid_at = timezone.now()
            p.save()

        # Números a emitir
        chosen_numbers = []
        if isinstance(p.metadata, dict) and "chosen_numbers" in p.metadata:
            chosen_numbers = [int(x) for x in p.metadata.get("chosen_numbers", [])]
        elif getattr(p, "chosen_number", None):
            chosen_numbers = [int(p.chosen_number)]

        # Crear tickets por cada número (respetando UNIQUE(raffle, number))
        for n in chosen_numbers:
            try:
                Ticket.objects.create(
                    raffle=p.raffle,
                    number=n,
                    payment=p,
                    buyer_name=p.buyer_name,
                    buyer_email=p.buyer_email,
                    buyer_phone=p.buyer_phone,
                )
            except IntegrityError:
                # Choque por número ya tomado (concurrencia). Aquí podrías:
                # - Registrar incidencia para reembolso/cambio
                # - Guardar en p.metadata['conflicts'] = [...]
                pass

    return True

# ========= Crear pedido Mercado Pago =========

@csrf_exempt
@require_POST
def create_preference(request):
    raffle = _get_active_raffle()
    if not raffle:
        return JsonResponse({"error": "No hay rifa activa"}, status=400)

    try:
        data = json.loads(request.body.decode())
    except Exception:
        return JsonResponse({"error": "JSON inválido"}, status=400)

    chosen_numbers = data.get("chosen_numbers", [])
    buyer = data.get("buyer", {}) or {}

    if not chosen_numbers:
        return JsonResponse({"error": "Debes seleccionar números"}, status=400)

    quantity = len(chosen_numbers)
    total = int(raffle.price_clp) * quantity
    external_reference = f"raffle-{raffle.id}-{int(timezone.now().timestamp())}"

    # Base pública (ngrok) – NO dependemos del host de la request
    base_url = getattr(settings, "MP_PUBLIC_BASE_URL", request.build_absolute_uri("/").rstrip("/"))

    success_url = f"{base_url}/?ok=1"
    failure_url = f"{base_url}/?fail=1"
    pending_url = f"{base_url}/?pending=1"
    notification_url = f"{base_url}{reverse('mp_webhook')}"

    print("MP success_url:", success_url)
    print("MP notification_url:", notification_url)

    pref_body = {
        "items": [
            {
                "id": f"raffle-{raffle.id}",
                "title": raffle.title,
                "quantity": quantity,
                "unit_price": float(raffle.price_clp),
                "currency_id": "CLP",
            }
        ],
        "back_urls": {
            "success": success_url,
            "failure": failure_url,
            "pending": pending_url,
        },
        # Reintentamos auto_return SOLO si estamos en https (ngrok / prod)
        "external_reference": external_reference,
        "metadata": {
            "chosen_numbers": chosen_numbers,
            "raffle_id": raffle.id,
            "buyer": buyer,
        },
        "notification_url": notification_url,
    }

    # si quieres probar auto_return de nuevo:
    if base_url.startswith("https://"):
        pref_body["auto_return"] = "approved"

    resp = requests.post(
        "https://api.mercadopago.com/checkout/preferences",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.MP_ACCESS_TOKEN}",
        },
        data=json.dumps(pref_body),
        timeout=20,
    )

    try:
        body = resp.json()
    except Exception:
        return JsonResponse(
            {"error": "Respuesta inválida de Mercado Pago", "detail": resp.text},
            status=400,
        )

    if resp.status_code >= 300 or "id" not in body:
        print("MP create_pref error:", body)
        return JsonResponse(
            {"error": "Error al crear preferencia", "detail": body},
            status=400,
        )

    preference_id = body["id"]

    Payment.objects.update_or_create(
        gateway_payment_id=preference_id,
        defaults=dict(
            raffle=raffle,
            amount_clp=total,
            status="pending",
            buyer_name=buyer.get("name", "S/N"),
            buyer_email=buyer.get("email", ""),
            buyer_phone=buyer.get("phone", ""),
            chosen_number=0,
            metadata={"chosen_numbers": chosen_numbers},
            gateway="mercadopago",
        ),
    )

    return JsonResponse({"preference_id": preference_id})

# ========= Webhook Mercado Pago =========

@csrf_exempt
@require_POST
def mp_webhook(request):
    """
    Webhook de notificaciones de Mercado Pago con validación HMAC.
    - Validación estricta solo para notificaciones de tipo 'payment' con data.id.
    - Cuando el pago queda approved+accredited, confirma Payment y crea Tickets
      usando el preference_id (que guardamos como gateway_payment_id).
    """
    # 1) Body solo para logging y para sacar 'type'
    try:
        payload = json.loads(request.body.decode() or "{}")
    except Exception:
        payload = {}

    print(">>> MP WEBHOOK REAL")
    print("   Method:", request.method)
    print("   Payload:", payload)

    # 2) Headers relevantes
    xSignature = request.headers.get("x-signature") or request.META.get("HTTP_X_SIGNATURE", "")
    xRequestId = request.headers.get("x-request-id") or request.META.get("HTTP_X_REQUEST_ID", "")

    print("   x-signature:", xSignature)
    print("   x-request-id:", xRequestId)

    if not xSignature:
        print("   >> Falta x-signature, ignorando notificación (pero respondo 200)")
        return HttpResponse("missing signature", status=200)

    # 3) Query params (equivalente a request.url.query)
    query_string = request.META.get("QUERY_STRING", "")
    queryParams = urllib.parse.parse_qs(query_string)

    # data.id primero (payments), si no, id (merchant_order / otros)
    dataID = queryParams.get("data.id", [""])[0] or queryParams.get("id", [""])[0]
    print("   data.id (url):", dataID)

    # Tipo de notificación: payment / merchant_order / etc
    notif_type = str(
        queryParams.get("type", [""])[0]
        or payload.get("type")
        or payload.get("topic")
        or ""
    ).lower()

    print("   notif_type:", notif_type, "dataID:", dataID)

    # Solo exigimos HMAC estricto cuando es payment con data.id
    require_strict_hmac = (notif_type == "payment" and bool(dataID))

    # 4) Separar ts y v1 del x-signature
    ts = None
    hash_v1 = None
    parts = xSignature.split(",")
    for part in parts:
        keyValue = part.split("=", 1)
        if len(keyValue) == 2:
            key = keyValue[0].strip()
            value = keyValue[1].strip()
            if key == "ts":
                ts = value
            elif key == "v1":
                hash_v1 = value

    print("   ts:", ts)
    print("   v1 (header hash):", hash_v1)

    if not hash_v1:
        print("   >> No se encontró v1 en x-signature, ignorando notificación")
        return HttpResponse("invalid signature format", status=200)

    # 5) Construir manifest EXACTAMENTE como indica MP
    manifest_parts = []
    if dataID:
        manifest_parts.append(f"id:{dataID}")
    if xRequestId:
        manifest_parts.append(f"request-id:{xRequestId}")
    if ts:
        manifest_parts.append(f"ts:{ts}")
    manifest = ";".join(manifest_parts) + ";"

    print("   manifest:", manifest)

    # 6) Obtener secret desde settings
    secret = getattr(settings, "MP_WEBHOOK_SECRET", "")
    print("   SECRET LEN:", len(secret))

    if not secret:
        print("   >> MP_WEBHOOK_SECRET no configurado, NO se valida firma (solo dev)")
        # En dev puedes dejar pasar todo, en prod deberías dejar esto en False
        return HttpResponse("secret not configured", status=200)

    # 7) Crear HMAC SHA256(manifest, secret)
    hmac_obj = hmac.new(secret.encode(), msg=manifest.encode(), digestmod=hashlib.sha256)
    sha = hmac_obj.hexdigest()

    print("   computed HMAC:", sha)
    print("   header v1:", hash_v1)

    if require_strict_hmac and sha != hash_v1:
        print("   >> HMAC verification FAILED para payment, ignorando notificación")
        return HttpResponse("invalid signature", status=200)

    if require_strict_hmac:
        print("   >> HMAC verification PASSED para payment")
    else:
        # Para merchant_order / otros, solo logueamos; pueden no calzar con este manifest
        if sha != hash_v1:
            print("   >> HMAC mismatch en tópico no crítico (p.ej. merchant_order). Solo log.")
        else:
            print("   >> HMAC match en tópico no crítico.")

    # === A partir de aquí, si es payment+data.id y HMAC es válido, confiamos en el evento ===

    # Caso payment → consultamos /v1/payments/{id}
    if notif_type == "payment" and dataID:
        pr = requests.get(
            f"https://api.mercadopago.com/v1/payments/{dataID}",
            headers=_mp_headers(),
            timeout=20,
        )
        print("   /v1/payments status:", pr.status_code)
        if pr.status_code >= 300:
            return HttpResponse("fetch error", status=200)

        pdata = pr.json()
        pstatus = (pdata.get("status") or "").lower()          # approved / rejected / pending...
        pdetail = (pdata.get("status_detail") or "").lower()   # accredited / ...
        preference_id = str(pdata.get("preference_id") or "")
        external_ref  = str(pdata.get("external_reference") or "")

        print("   payment status:", pstatus, "detail:", pdetail)
        print("   preference_id:", preference_id, "external_reference:", external_ref)

        # Confirmar pago y crear tickets cuando esté approved+accredited
        if pstatus == "approved" and pdetail == "accredited" and preference_id:
            # Reutilizamos tu helper: gateway_payment_id = preference_id
            print("   >> Pago aprobado, confirmando tickets para preference_id:", preference_id)
            _confirm_tickets_from_payment_id(preference_id)
        elif pstatus in ("rejected", "cancelled") and preference_id:
            print("   >> Pago rechazado/cancelado, marcando Payment como failed:", preference_id)
            Payment.objects.filter(gateway_payment_id=preference_id).update(status="failed")

        return HttpResponse("ok", status=200)

    # Caso merchant_order (solo log por ahora)
    if notif_type == "merchant_order" and dataID:
        print("   merchant_order id:", dataID)
        return HttpResponse("ok", status=200)

    # Otros tópicos → responder 200 para que MP no reintente eternamente
    return HttpResponse("ignored", status=200)

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

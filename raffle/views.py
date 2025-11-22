import json, requests, csv, hmac, hashlib, urllib.parse
from datetime import timedelta
from uuid import uuid4

from math import ceil
from django.conf import settings
from django.shortcuts import render
from django.http import JsonResponse, HttpResponse, HttpResponseBadRequest
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
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

@ensure_csrf_cookie
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

    taken = list(_get_taken_numbers_for_raffle(raffle))
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

@require_POST
@ratelimit(key="ip", rate="10/m", block=True)
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

    # límite razonable por request para evitar payloads enormes
    if len(chosen_numbers) > 100:
        return JsonResponse({"error": "Demasiados números en una sola compra"}, status=400)
    
    try:
        chosen_numbers = [int(n) for n in chosen_numbers]
    except (TypeError, ValueError):
        return JsonResponse({"error": "Números inválidos"}, status=400)
    
    for n in chosen_numbers:
        if n < 1 or n > raffle.numbers_total:
            return JsonResponse({"error": f"Número fuera de rango: {n}"}, status=400)
    
    name = (buyer.get("name") or "").strip()
    email = (buyer.get("email") or "").strip()
    phone = (buyer.get("phone") or "").strip()

    if not name or not email or "@" not in email:
        return JsonResponse({"error": "Nombre y correo válidos son obligatorios"}, status=400)
    
    quantity = len(chosen_numbers)
    total = int(raffle.price_clp) * quantity
    external_reference = f"raffle-{raffle.id}-{int(timezone.now().timestamp())}"

    base_url = getattr(settings, "MP_PUBLIC_BASE_URL", "").rstrip("/")
    if not base_url:
        if not settings.DEBUG:
            return JsonResponse({"error": "MP_PUBLIC_BASE_URL no configurado en el servidor"}, status=500)
        base_url = request.build_absolute_uri("/").rstrip("/")

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
                "category_id": "services",
                "description": f"Participación en la rifa '{raffle.title}' con {quantity} número(s)",
            }
        ],
        "back_urls": {
            "success": success_url,
            "failure": failure_url,
            "pending": pending_url,
        },
        "external_reference": external_reference,
        "metadata": {
            "chosen_numbers": chosen_numbers,
            "raffle_id": raffle.id,
            "buyer": buyer,
        },
        "notification_url": notification_url,
    }

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
    external_reference = pref_body["external_reference"]

    # Guarda Payment "pendiente" asociado al external_reference
    client_ip = request.META.get("REMOTE_ADDR")
    user_agent = request.META.get("HTTP_USER_AGENT", "")

    Payment.objects.update_or_create(
        gateway_payment_id=external_reference,
        defaults=dict(
            raffle=raffle,
            amount_clp=total,
            status="pending",
            buyer_name=name,
            buyer_email=email,
            buyer_phone=phone,
            chosen_number=0,
            metadata={
                "chosen_numbers": chosen_numbers,
                "mp_preference_id": preference_id,
                "client_ip": client_ip,
                "user_agent": user_agent,
            },
            gateway="mercadopago",
        ),
    )


    return JsonResponse({"preference_id": preference_id})

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
                    "error": "Algunos números ya no están disponibles",
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

    return JsonResponse(
        {
            "ok": True,
            "reserved_until": expires_at.isoformat(),
            "count": len(chosen_numbers),
        }
    )

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
        if pstatus == "approved" and pdetail == "accredited" and external_ref:
            print("   >> Pago aprobado, confirmando tickets para external_ref:", external_ref)
            _confirm_tickets_from_payment_id(external_ref)
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
        "MP_PUBLIC_KEY": getattr(settings, "MP_PUBLIC_KEY", ""),
    })


@require_POST
@ratelimit(key="ip", rate="10/m", block=True)
def create_donation_preference(request):
    """
    Crea una preferencia de MercadoPago para una DONACIÓN (monto libre).
    Ahora el donante puede ser anónimo: solo se exige el monto.
    """
    raffle = _get_active_raffle()
    if not raffle:
        return JsonResponse({"error": "No hay rifa activa"}, status=400)

    try:
        data = json.loads(request.body.decode())
    except Exception:
        return JsonResponse({"error": "JSON inválido"}, status=400)

    amount_clp = int(data.get("amount_clp") or 0)
    buyer = data.get("buyer") or {}

    if amount_clp <= 0:
        return JsonResponse({"error": "Debes ingresar un monto válido"}, status=400)

    # Datos opcionales (pueden venir vacíos)
    name_raw = (buyer.get("name") or "").strip()
    email_raw = (buyer.get("email") or "").strip()
    phone = ""

    # Para el modelo Payment, estos campos no aceptan null, así que usamos valores seguros
    buyer_name = name_raw or "Anónimo"
    if email_raw and "@" in email_raw:
        buyer_email = email_raw
        is_anonymous = False
    else:
        # Email dummy solo para cumplir la validación del modelo
        buyer_email = f"anon_{uuid4().hex[:10]}@example.com"
        is_anonymous = True

    external_reference = f"donation-{raffle.id}-{int(timezone.now().timestamp())}"

    base_url = getattr(
        settings,
        "MP_PUBLIC_BASE_URL",
        request.build_absolute_uri("/").rstrip("/")
    )

    success_url = f"{base_url}/donar/?ok=1"
    failure_url = f"{base_url}/donar/?fail=1"
    pending_url = f"{base_url}/donar/?pending=1"
    notification_url = f"{base_url}{reverse('mp_webhook')}"

    pref_body = {
        "items": [
            {
                "id": f"donation-{raffle.id}",
                "title": f"Donación para {raffle.title}",
                "quantity": 1,
                "unit_price": float(amount_clp),
                "currency_id": "CLP",
                "category_id": "donations",
                "description": f"Donación voluntaria para la rifa '{raffle.title}'",
            }
        ],
        "back_urls": {
            "success": success_url,
            "failure": failure_url,
            "pending": pending_url,
        },
        "external_reference": external_reference,
        "metadata": {
            "is_donation": True,
            "raffle_id": raffle.id,
            "buyer": {
                "name": name_raw,
                "email": email_raw,
                "phone": phone,
            },
            "amount_clp": amount_clp,
            "is_anonymous": is_anonymous,
        },
        "notification_url": notification_url,
    }

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
        print("MP create_donation_pref error:", body)
        return JsonResponse(
            {"error": "Error al crear preferencia", "detail": body},
            status=400,
        )

    preference_id = body["id"]

    Payment.objects.update_or_create(
        gateway_payment_id=external_reference,
        defaults=dict(
            raffle=raffle,
            amount_clp=amount_clp,
            status="pending",
            buyer_name=buyer_name,
            buyer_email=buyer_email,
            buyer_phone=phone,
            chosen_number=0,
            metadata={
                "is_donation": True,
                "mp_preference_id": preference_id,
                "amount_clp": amount_clp,
                "buyer_raw": buyer,
                "is_anonymous": is_anonymous,
            },
            gateway="mercadopago",
        ),
    )

    return JsonResponse({"preference_id": preference_id})

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
from django.db import models

class Raffle(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    price_clp = models.PositiveIntegerField(default=2000)
    numbers_total = models.PositiveIntegerField(default=500)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.title
    
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_active:
            Raffle.objects.exclude(pk=self.pk).update(is_active=False)


class Payment(models.Model):
    STATUS_CHOICES = [
        ("pending", "pending"),
        ("paid", "paid"),
        ("failed", "failed"),
        ("expired", "expired"),
    ]
    raffle = models.ForeignKey(Raffle, on_delete=models.CASCADE, related_name="payments")
    amount_clp = models.PositiveIntegerField()
    gateway = models.CharField(max_length=50, default="mock")
    gateway_payment_id = models.CharField(max_length=100, unique=True)   # idempotencia
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    buyer_name = models.CharField(max_length=150)
    buyer_email = models.EmailField()
    buyer_phone = models.CharField(max_length=30, blank=True)
    chosen_number = models.PositiveIntegerField(null=True, blank=True, default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.gateway}:{self.gateway_payment_id} ({self.status})"


class Ticket(models.Model):
    raffle = models.ForeignKey(Raffle, on_delete=models.CASCADE, related_name="tickets")
    number = models.PositiveIntegerField()
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="tickets")

    buyer_name = models.CharField(max_length=150)
    buyer_email = models.EmailField()
    buyer_phone = models.CharField(max_length=30, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["raffle", "number"], name="uniq_raffle_number")
        ]

    def __str__(self):
        return f"{self.raffle_id} - #{self.number}"

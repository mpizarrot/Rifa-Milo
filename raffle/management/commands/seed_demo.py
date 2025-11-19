from django.core.management.base import BaseCommand
from raffle.models import Raffle

class Command(BaseCommand):
    help = "Crea rifa demo"

    def handle(self, *args, **kwargs):
        if not Raffle.objects.exists():
            Raffle.objects.create(
                title="Rifa Milo",
                description="Elige un número y paga para participar.",
                price_clp=2000, numbers_total=500, is_active=True
            )
            self.stdout.write(self.style.SUCCESS("Rifa demo creada"))
        else:
            self.stdout.write("Ya existe una rifa")

from django.apps import AppConfig

class RaffleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "raffle"
    verbose_name = "Rifas"

    # Si en el futuro agregas señales, descomenta:
    # def ready(self):
    #     import raffle.signals  # noqa

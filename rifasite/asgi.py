"""
ASGI config for rifasite project.

Expone la variable 'application' para servidores ASGI (uvicorn, daphne).
"""
import os
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "rifasite.settings")
application = get_asgi_application()

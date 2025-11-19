"""
WSGI config for rifasite project.

Expone la variable 'application' para servidores WSGI (gunicorn, uWSGI, etc.).
"""
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "rifasite.settings")
application = get_wsgi_application()

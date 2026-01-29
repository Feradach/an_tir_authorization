import os
import sys

# Ensure the project root is on sys.path (assumes this file sits next to manage.py)
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Load environment variables from .env if present (python-dotenv is in requirements)
try:
    from dotenv import load_dotenv
    # Load root-level .env
    load_dotenv(os.path.join(PROJECT_ROOT, '.env'))
    # Also load optional sql_details.env if provided either at project root
    load_dotenv(os.path.join(PROJECT_ROOT, 'sql_details.env'))
    # Or inside the Django package directory
    load_dotenv(os.path.join(PROJECT_ROOT, 'An_Tir_Authorization', 'sql_details.env'))
except Exception:
    pass

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'An_Tir_Authorization.settings')

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()

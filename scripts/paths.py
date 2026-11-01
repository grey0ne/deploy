"""Paths of the host project layout (no environment needed, safe to import first)."""
import os

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEPLOY_DIR = os.path.join(PROJECT_DIR, 'deploy')
ENV_DIR = os.path.join(PROJECT_DIR, 'environment')
# Component directories (see deploy_config.py); FastAPI images are built from the project
# root with fastapi.packages as the only paths sent to the build.
DJANGO_DIR = os.path.join(PROJECT_DIR, 'django_backend')
FASTAPI_BUILD_CONTEXT = PROJECT_DIR
NEXTJS_DIR = os.path.join(PROJECT_DIR, 'nextjs_frontend')
# Django runtime data (media etc.) on the host; kept as backend_data for existing deployments.
DJANGO_DATA_DIR = os.path.join(PROJECT_DIR, 'backend_data')
COMPOSE_DIR = os.path.join(DEPLOY_DIR, 'compose')
GENERATED_COMPOSE_DIR = os.path.join(COMPOSE_DIR, 'generated')
BACKUP_DIR = os.path.join(PROJECT_DIR, 'backup')
VERSION_FILE = os.path.join(ENV_DIR, 'version')
BASE_ENV_FILE = os.path.join(ENV_DIR, 'env.base')
PROD_ENV_FILE = os.path.join(ENV_DIR, 'env.prod')
DEV_ENV_FILE = os.path.join(ENV_DIR, 'env.dev')
# Optional local overrides, applied last in both environments.
LOCAL_ENV_FILE = os.path.join(PROJECT_DIR, '.env')

import os

from scripts.compose.renderer import render_prod_compose
from scripts.constants import COMPOSE_DIR
from scripts.deploy_config import get_deploy_config
from scripts.printing import print_status


def render_production_compose_file(
    django_image: str = '',
    nextjs_image: str = '',
    fastapi_image: str = '',
    django_worker_count: int | None = None,
    fastapi_worker_count: int | None = None,
) -> None:
    config = get_deploy_config()
    if django_worker_count is not None:
        config.django_worker_count = django_worker_count
    if fastapi_worker_count is not None:
        config.fastapi_worker_count = fastapi_worker_count
    print_status('Rendering production compose file for deploy')
    render_prod_compose(
        django_image=django_image,
        nextjs_image=nextjs_image,
        fastapi_image=fastapi_image,
        config=config,
    )
    generated = os.path.join(COMPOSE_DIR, 'generated', 'prod.yml')
    prod_path = os.path.join(COMPOSE_DIR, 'prod.yml')
    with open(generated, encoding='utf-8') as src:
        content = src.read()
    with open(prod_path, 'w', encoding='utf-8') as dst:
        dst.write(content)

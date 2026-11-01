from scripts.compose.fastapi_image import write_fastapi_dockerfiles
from scripts.compose.renderer import (
    render_dev_balancer_compose,
    render_dev_compose,
)
from scripts.deploy_config import get_deploy_config
from scripts.printing import print_status


def generate_dev_compose_files() -> None:
    config = get_deploy_config()
    write_fastapi_dockerfiles(config)
    render_dev_compose(config)
    render_dev_balancer_compose()
    print_status('Generated dev compose files in deploy/compose/generated/')


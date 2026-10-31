from scripts.compose.renderer import (
    render_dev_balancer_compose,
    render_dev_compose,
    render_prod_balancer_compose,
)
from scripts.deploy_config import get_deploy_config
from scripts.printing import print_status


def generate_dev_compose_files() -> None:
    config = get_deploy_config()
    render_dev_compose(config)
    render_dev_balancer_compose()
    print_status('Generated dev compose files in deploy/compose/generated/')


def generate_all_compose_files() -> None:
    generate_dev_compose_files()
    render_prod_balancer_compose()
    print_status('Generated prod_balancer.yml')

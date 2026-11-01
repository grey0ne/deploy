"""Readable progress of a `docker stack deploy`.

`docker stack deploy --detach=false` redraws its progress bars with terminal escapes; over
`ssh ... bash -s` every redraw becomes a new line. Instead the stack is deployed detached and
its services are polled: one line per service whenever its state changes, task errors once.
"""
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from scripts.printing import print_error, print_status

FIELD_SEPARATOR = '\x1f'
# Swarm update states after which the deploy of a service will not converge by itself.
FAILED_UPDATE_STATES = ('paused', 'rollback_started', 'rollback_paused', 'rollback_completed')
UPDATING_STATES = ('updating', 'rollback_started')
FAILED_TASK_STATES = ('failed', 'rejected')
# Like `docker stack deploy --detach=false`: a service counts as converged once it stayed so.
STABLE_SECONDS = 5
POLL_INTERVAL = 2


def snapshot_script(stack_name: str) -> str:
    sep = FIELD_SEPARATOR
    service_format = sep.join([
        'S', '{{.Spec.Name}}', '{{.UpdatedAt}}',
        '{{if .Spec.Mode.Replicated}}{{.Spec.Mode.Replicated.Replicas}}{{end}}',
        '{{if .UpdateStatus}}{{.UpdateStatus.State}}{{end}}',
        '{{.Spec.TaskTemplate.ContainerSpec.Image}}',
    ])
    task_format = sep.join(['T', '{{.ID}}', '{{.Name}}', '{{.Image}}', '{{.DesiredState}}', '{{.CurrentState}}', '{{.Error}}'])
    return (
        f"for SERVICE in $(docker stack services -q {stack_name}); do\n"
        f"    docker service inspect --format '{service_format}' $SERVICE\n"
        f"    docker service ps --no-trunc --format '{task_format}' $SERVICE\n"
        "done\n"
    )


@dataclass
class Task:
    id: str
    name: str
    image: str
    desired_state: str
    state: str
    error: str


@dataclass
class Service:
    name: str
    updated_at: str
    replicas: int | None
    update_state: str
    image: str
    tasks: list[Task] = field(default_factory=list)


def image_digest(image: str) -> str:
    return image.split('@', 1)[1] if '@' in image else image


def parse_snapshot(text: str) -> dict[str, Service]:
    services: dict[str, Service] = {}
    current: Service | None = None
    for line in text.splitlines():
        parts = line.split(FIELD_SEPARATOR)
        if parts[0] == 'S' and len(parts) == 6:
            _, name, updated_at, replicas, update_state, image = parts
            current = Service(
                name=name, updated_at=updated_at, replicas=int(replicas) if replicas else None,
                update_state=update_state, image=image,
            )
            services[name] = current
        elif parts[0] == 'T' and len(parts) == 7 and current is not None:
            _, task_id, name, image, desired_state, current_state, error = parts
            state = current_state.split(' ', 1)[0].lower()
            current.tasks.append(Task(task_id, name, image, desired_state.lower(), state, error.strip('"')))
    return services


def is_new_task(service: Service, task: Task) -> bool:
    return image_digest(task.image) == image_digest(service.image)


def active_tasks(service: Service) -> list[Task]:
    return [task for task in service.tasks if task.desired_state == 'running']


def desired_count(service: Service) -> int:
    return service.replicas if service.replicas is not None else len(active_tasks(service))


def running_new_count(service: Service) -> int:
    return sum(1 for task in active_tasks(service) if task.state == 'running' and is_new_task(service, task))


def is_converged(service: Service) -> bool:
    active = active_tasks(service)
    return (
        service.update_state not in UPDATING_STATES
        and service.update_state not in FAILED_UPDATE_STATES
        and running_new_count(service) == desired_count(service)
        and len(active) == desired_count(service)
    )


def is_failed(service: Service) -> bool:
    return service.update_state in FAILED_UPDATE_STATES


def describe(service: Service) -> str:
    parts = [f"{running_new_count(service)}/{desired_count(service)} running"]
    pending: dict[str, int] = {}
    for task in active_tasks(service):
        if task.state != 'running' or not is_new_task(service, task):
            state = task.state if is_new_task(service, task) else f'old {task.state}'
            pending[state] = pending.get(state, 0) + 1
    parts.extend(f"{count} {state}" for state, count in sorted(pending.items()))
    if service.update_state:
        parts.append(f"update {service.update_state}")
    return ', '.join(parts)


def changed_services(before: dict[str, Service], after: dict[str, Service]) -> list[str]:
    return sorted(
        name for name, service in after.items()
        if name not in before or before[name].updated_at != service.updated_at
    )


def wait_for_stack(
    before: dict[str, Service],
    poll: Callable[[], dict[str, Service]],
    timeout: int,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Print the progress of services changed since `before`; True once all of them converged."""
    started = clock()
    last_lines: dict[str, str] = {}
    converged_since: dict[str, float] = {}
    reported_tasks: set[str] = set()
    while True:
        now = clock()
        elapsed = int(now - started)
        services = poll()
        names = changed_services(before, services)
        if not names and elapsed >= STABLE_SECONDS:
            print_status("No services changed")
            return True
        done = bool(names)
        for name in names:
            service = services[name]
            for task in service.tasks:
                if task.id not in reported_tasks and task.state in FAILED_TASK_STATES and is_new_task(service, task):
                    reported_tasks.add(task.id)
                    print_error(f"[{elapsed:4d}s] {task.name} {task.state}: {task.error or 'no error message'}")
            if is_failed(service):
                print_error(f"[{elapsed:4d}s] {name}: {describe(service)}")
                return False
            if is_converged(service):
                converged_since.setdefault(name, now)
            else:
                converged_since.pop(name, None)
            stable = name in converged_since and now - converged_since[name] >= STABLE_SECONDS
            line = 'converged' if stable else describe(service)
            if last_lines.get(name) != line:
                last_lines[name] = line
                print(f"[{elapsed:4d}s] {name}: {line}", flush=True)
            done = done and stable
        if done:
            return True
        if elapsed >= timeout:
            return False
        sleep(POLL_INTERVAL)

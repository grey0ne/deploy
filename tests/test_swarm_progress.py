"""Progress of `docker stack deploy`. Run from the project root: python3.15 -m unittest discover deploy/tests"""
import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import swarm_progress  # noqa: E402
from scripts.swarm_progress import FIELD_SEPARATOR, parse_snapshot  # noqa: E402

OLD = 'registry/demo-django@sha256:old'
NEW = 'registry/demo-django@sha256:new'


def service_line(name: str, updated_at: str, image: str, update_state: str = '', replicas: str = '1') -> str:
    return FIELD_SEPARATOR.join(['S', name, updated_at, replicas, update_state, image])


def task_line(task_id: str, image: str, desired: str, current: str, error: str = '') -> str:
    return FIELD_SEPARATOR.join(['T', task_id, 'demo_django.1', image, desired, current, error])


def snapshot(*lines: str):
    return parse_snapshot('\n'.join(lines))


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def run_wait(before, polls, timeout=60):
    clock = FakeClock()
    remaining = list(polls)

    def poll():
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        result = swarm_progress.wait_for_stack(before, poll, timeout, clock=clock, sleep=clock.sleep)
    return result, out.getvalue(), err.getvalue()


class ParseSnapshotTest(unittest.TestCase):
    def test_services_and_tasks(self) -> None:
        services = snapshot(
            service_line('demo_django', 't2', NEW, 'updating'),
            task_line('a', f'{NEW}', 'Running', 'Starting 2 seconds ago'),
            task_line('b', OLD, 'Shutdown', 'Running 3 hours ago'),
            service_line('demo_nextjs', 't1', 'registry/demo-nextjs@sha256:x', replicas=''),
        )
        django = services['demo_django']
        self.assertEqual(django.update_state, 'updating')
        self.assertEqual([task.state for task in django.tasks], ['starting', 'running'])
        self.assertEqual(swarm_progress.describe(django), '0/1 running, 1 starting, update updating')
        self.assertIsNone(services['demo_nextjs'].replicas)

    def test_converged_needs_new_image(self) -> None:
        services = snapshot(
            service_line('demo_django', 't2', NEW, 'completed'),
            task_line('a', OLD, 'Running', 'Running 3 hours ago'),
        )
        self.assertFalse(swarm_progress.is_converged(services['demo_django']))


class WaitForStackTest(unittest.TestCase):
    before = snapshot(service_line('demo_django', 't1', OLD, 'completed'), task_line('a', OLD, 'Running', 'Running 1 hour ago'))

    def test_prints_changes_once_and_converges(self) -> None:
        starting = snapshot(
            service_line('demo_django', 't2', NEW, 'updating'),
            task_line('b', NEW, 'Running', 'Starting 1 second ago'),
            task_line('a', OLD, 'Shutdown', 'Running 1 hour ago'),
        )
        running = snapshot(
            service_line('demo_django', 't2', NEW, 'completed'),
            task_line('b', NEW, 'Running', 'Running 3 seconds ago'),
        )
        result, out, _ = run_wait(self.before, [starting, starting, starting, running])
        self.assertTrue(result)
        self.assertEqual(out.splitlines(), [
            '[   0s] demo_django: 0/1 running, 1 starting, update updating',
            '[   6s] demo_django: 1/1 running, update completed',
            '[  12s] demo_django: converged',
        ])

    def test_task_errors_and_rollback_fail(self) -> None:
        failing = snapshot(
            service_line('demo_django', 't2', NEW, 'updating'),
            task_line('b', NEW, 'Shutdown', 'Failed 1 second ago', '"task: non-zero exit (1)"'),
        )
        rolled_back = snapshot(
            service_line('demo_django', 't3', OLD, 'rollback_completed'),
            task_line('a', OLD, 'Running', 'Running 1 hour ago'),
            task_line('b', NEW, 'Shutdown', 'Failed 5 seconds ago', 'task: non-zero exit (1)'),
        )
        result, _, err = run_wait(self.before, [failing, failing, rolled_back])
        self.assertFalse(result)
        self.assertEqual(err.count('demo_django.1 failed: task: non-zero exit (1)'), 1)
        self.assertIn('update rollback_completed', err)

    def test_unchanged_stack(self) -> None:
        result, out, _ = run_wait(self.before, [self.before])
        self.assertTrue(result)
        self.assertIn('No services changed', out)

    def test_timeout(self) -> None:
        stuck = snapshot(
            service_line('demo_django', 't2', NEW, 'updating'),
            task_line('b', NEW, 'Running', 'Preparing 1 second ago'),
        )
        result, out, _ = run_wait(self.before, [stuck], timeout=10)
        self.assertFalse(result)
        self.assertEqual(out.splitlines(), ['[   0s] demo_django: 0/1 running, 1 preparing, update updating'])


if __name__ == '__main__':
    unittest.main()

"""Commands that run without a project environment (`env=None`): never import scripts.constants here."""
import subprocess


def updatesubmodules(_args: list[str]) -> None:
    subprocess.run(['git', 'submodule', 'status'], check=True)
    subprocess.run(['git', 'submodule', 'update', '--init', '--recursive', '--remote'], check=True)

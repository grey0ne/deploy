"""Versions of the shared balancer (proxy) image.

The balancer is shared by every project of a server (dev: of the machine), and each project
brings the proxy version of its deploy submodule. A project with an older submodule must not
replace a newer running proxy: the configs of the other projects may rely on its features.
"""
from collections.abc import Sequence

Version = tuple[int, ...]


def image_version(image: str) -> Version | None:
    """`<repository>:<x.y.z>` → (x, y, z); None for another tag or an image without a tag."""
    repository, separator, tag = image.rpartition(':')
    # "registry:5000/namespace/image" has no tag: the part after the colon contains a slash
    if not separator or not repository or '/' in tag:
        return None
    parts: Sequence[str] = tag.split('.')
    if not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def image_repository(image: str) -> str:
    repository, separator, tag = image.rpartition(':')
    if not separator or '/' in tag:
        return image
    return repository


def is_newer(running: str | None, current: str) -> bool:
    """The running image is a newer version of the same image than the current one."""
    if running is None or image_repository(running) != image_repository(current):
        return False
    running_version, current_version = image_version(running), image_version(current)
    if running_version is None or current_version is None:
        return False
    return running_version > current_version


def needs_update(running: str | None, current: str) -> bool:
    """Replace the running proxy: it is missing, older or not comparable (another repository or tag)."""
    if running == current:
        return False
    return not is_newer(running, current)


def check_image(running: str | None, current: str) -> str:
    """The image to validate the configs with: the one that will read them."""
    if running is not None and is_newer(running, current):
        return running
    return current

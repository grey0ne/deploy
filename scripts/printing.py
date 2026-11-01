import sys

# Flushed right away: `dc` interleaves its messages with the output of the commands it runs.


def print_status(msg: str):
    print(f"\033[0;32m{msg}\033[0m", flush=True)

def print_warning(msg: str):
    print(f"\033[0;33m{msg}\033[0m", flush=True)

def print_error(msg: str):
    print(f"\033[0;31m{msg}\033[0m", file=sys.stderr, flush=True)

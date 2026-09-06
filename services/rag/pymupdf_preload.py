"""Pytest plugin working around two PyMuPDF crashes on this platform.

1. Importing the native extension partway through a pytest run faults with an
   access violation. Plugins named with -p are initialised before conftest
   files and before collection, which is early enough to avoid it.

2. The extension also faults during interpreter shutdown once other native
   modules are loaded alongside it, turning a fully passing run into exit code
   139. By that point every test has finished and the result is already known,
   so the session ends with an immediate process exit that skips interpreter
   finalisation.

The exit status is taken from pytest itself, so pass/fail reporting is
unaffected. The hook runs last, leaving other plugins (coverage included) their
chance to write output first.

Wired up through addopts in pyproject.toml so a bare `pytest` works.
"""

from __future__ import annotations

import os
import sys

import pymupdf  # noqa: F401  must load before collection begins
import pytest

_exit_status = 0


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    global _exit_status
    _exit_status = int(exitstatus)


def pytest_unconfigure(config: pytest.Config) -> None:
    """Exit here rather than in pytest_sessionfinish.

    The terminal reporter prints the failure tracebacks and summary from a
    hookwrapper around sessionfinish, whose post-yield half runs after every
    plain implementation. Exiting from sessionfinish therefore killed the
    process before the failure output was written -- tests reported as failed
    with no visible reason. unconfigure runs after all reporting is done.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_exit_status)

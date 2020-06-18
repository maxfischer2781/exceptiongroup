"""Top-level package for exceptiongroup."""

from ._version import __version__

__all__ = ["ExceptionGroup", "split", "catch"]

from ._exception_group import ExceptionGroup
from . import _monkeypatch
from ._tools import split, catch

"""The door is tested against the real schedule store, so it borrows its fixture."""

from tests.scheduler.conftest import store

__all__ = ["store"]

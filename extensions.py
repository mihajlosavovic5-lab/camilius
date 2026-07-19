"""Shared Flask extension instances that both app.py and api.py need - split out to
avoid a circular import (api.py's blueprint is imported by app.py, so api.py can't
import `limiter` back from app.py directly)."""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(get_remote_address, default_limits=[])

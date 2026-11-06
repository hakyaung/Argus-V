"""Argus-V v2 scenarios (TASK Scenario/TASK.md). Operator-only JSON -> per-cell engine sessions.

Importing this package never changes v1 behaviour: v1 profiles, Session and run_scripted are untouched.
"""
from .materialize import CELLS, cells, materialize
from .registry import list_ids, load_spec, runnable_ids
from .session import SessionV2, open_session

__all__ = ["CELLS", "SessionV2", "cells", "list_ids", "load_spec", "materialize", "open_session", "runnable_ids"]

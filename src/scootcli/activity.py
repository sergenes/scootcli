"""Shared 'activity' context manager: spinner + ESC-interrupt around a blocking call.

Used by slash-commands (``/init``, ``/compact``) that call the model outside the agent loop. Yields a
``cancel_event`` the caller passes to ``client.chat`` so ESC terminates it immediately.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

from .keys import InterruptibleSection
from .status import Status


@contextmanager
def activity(message: str):
    cancel = threading.Event()
    status = Status()
    status.start(message)
    try:
        with InterruptibleSection(cancel):
            yield cancel
    finally:
        status.stop()


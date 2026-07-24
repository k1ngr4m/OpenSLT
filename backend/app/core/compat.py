"""Compatibility helpers for the supported Python 3.8 runtime."""

from __future__ import annotations

import asyncio
import contextvars
from functools import partial
from typing import Any, Callable


async def to_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a callable in a worker thread while preserving context variables."""
    loop = asyncio.get_running_loop()
    context = contextvars.copy_context()
    call = partial(context.run, func, *args, **kwargs)
    return await loop.run_in_executor(None, call)

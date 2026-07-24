from __future__ import annotations

import typing
import asyncio
from collections import defaultdict
from typing import Any


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: typing.Dict[int, typing.Set[asyncio.Queue[typing.Dict[str, Any]]]] = defaultdict(set)

    async def subscribe(self, run_id: int) -> asyncio.Queue[typing.Dict[str, Any]]:
        queue: asyncio.Queue[typing.Dict[str, Any]] = asyncio.Queue(maxsize=500)
        self._subscribers[run_id].add(queue)
        return queue

    def unsubscribe(self, run_id: int, queue: asyncio.Queue[typing.Dict[str, Any]]) -> None:
        self._subscribers[run_id].discard(queue)
        if not self._subscribers[run_id]:
            self._subscribers.pop(run_id, None)

    def publish(self, run_id: int, event: typing.Dict[str, Any]) -> None:
        for queue in list(self._subscribers.get(run_id, set())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass


broker = EventBroker()


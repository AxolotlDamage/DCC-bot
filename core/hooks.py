from __future__ import annotations
import asyncio
from collections import defaultdict
from typing import Awaitable, Callable, Dict, List, Optional

Listener = Callable[..., Awaitable[None]]

class HookRegistry:
    def __init__(self):
        self._listeners: Dict[str, List[Listener]] = defaultdict(list)

    def on(self, event: str, listener: Listener) -> None:
        self._listeners[event].append(listener)

    def once(self, event: str, listener: Listener) -> None:
        async def wrapper(*args, **kwargs):
            try:
                await listener(*args, **kwargs)
            finally:
                self.off(event, wrapper)
        self.on(event, wrapper)

    def off(self, event: str, listener: Optional[Listener] = None) -> None:
        if listener is None:
            self._listeners.pop(event, None)
        else:
            lst = self._listeners.get(event)
            if not lst:
                return
            try:
                lst.remove(listener)
            except ValueError:
                pass

    async def emit(self, event: str, *args, **kwargs) -> None:
        # Copy listeners to avoid modification during iteration
        for listener in list(self._listeners.get(event, [])):
            try:
                await listener(*args, **kwargs)
            except Exception:
                # Swallow to avoid breaking emit chain; real consumers can add logging
                pass


# Global registry instance
HOOKS = HookRegistry()

def hook(event: str):
    """Decorator for registering an async listener function to an event."""
    def deco(fn: Listener) -> Listener:
        HOOKS.on(event, fn)
        return fn
    return deco

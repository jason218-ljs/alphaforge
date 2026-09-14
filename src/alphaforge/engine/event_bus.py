"""事件总线：bar / order / fill 事件的发布订阅。"""

from __future__ import annotations

from typing import Any, Callable, Dict, List


class EventBus:
    """简易事件总线。"""

    def __init__(self) -> None:
        self._handlers: Dict[str, List[Callable[[Any], None]]] = {}

    def subscribe(self, event_type: str, handler: Callable[[Any], None]) -> None:
        """订阅事件。"""
        self._handlers.setdefault(event_type, []).append(handler)

    def publish(self, event_type: str, payload: Any) -> None:
        """发布事件。"""
        for handler in self._handlers.get(event_type, []):
            try:
                handler(payload)
            except Exception:
                # 单个 handler 异常不阻断其他
                pass

    def clear(self) -> None:
        self._handlers.clear()
"""In-memory event broker for the web chat widget (Server-Sent Events).

One instance of the API is enough for the MVP. To run several instances, replace this
with Redis pub/sub; the interface (publish_message, events_after) stays the same.

The reply is complete before it is published (guardrail checks need the full text), so the
stream sends it in word groups to give the typing effect the widget animates.
"""
import re
import threading
import uuid
from collections import defaultdict


class WebChatBroker:
    def __init__(self) -> None:
        self._events: dict[str, list[dict]] = defaultdict(list)
        self._lock = threading.Lock()

    def publish_message(self, conversation_id: uuid.UUID, message_id: uuid.UUID, text: str, group: int = 3) -> None:
        words = re.findall(r"\S+\s*", text)
        chunks = ["".join(words[i : i + group]) for i in range(0, len(words), group)] or [""]
        key = str(conversation_id)
        with self._lock:
            events = self._events[key]
            for c in chunks:
                events.append({"id": len(events) + 1, "event": "chunk", "data": {"message_id": str(message_id), "text": c}})
            events.append({"id": len(events) + 1, "event": "done", "data": {"message_id": str(message_id)}})

    def last_id(self, conversation_id: uuid.UUID) -> int:
        with self._lock:
            return len(self._events[str(conversation_id)])

    def events_after(self, conversation_id: uuid.UUID, after: int) -> list[dict]:
        with self._lock:
            return [e for e in self._events[str(conversation_id)] if e["id"] > after]

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


broker = WebChatBroker()

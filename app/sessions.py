import time
import uuid
from dataclasses import dataclass, field


@dataclass
class Session:
    turns: list[dict] = field(default_factory=list)
    updated_at: float = field(default_factory=time.monotonic)


class MemorySessions:
    def __init__(self, ttl_seconds: float, turn_limit: int) -> None:
        self.ttl_seconds = ttl_seconds
        self.turn_limit = turn_limit
        self._sessions: dict[str, Session] = {}

    def get_or_create(self, session_id: str | None) -> tuple[str, str]:
        now = time.monotonic()
        expired = [key for key, session in self._sessions.items() if now - session.updated_at > self.ttl_seconds]
        for key in expired:
            del self._sessions[key]
        if session_id and session_id in self._sessions:
            session = self._sessions[session_id]
            session.updated_at = now
            return session_id, "continued"
        new_id = str(uuid.uuid4())
        self._sessions[new_id] = Session(updated_at=now)
        return new_id, "created" if session_id is None else "new"

    def add_turn(self, session_id: str, user: str, assistant: str) -> None:
        session = self._sessions[session_id]
        session.turns.append({"user": user, "assistant": assistant})
        if len(session.turns) > self.turn_limit:
            del session.turns[:-self.turn_limit]
        session.updated_at = time.monotonic()

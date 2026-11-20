import hashlib
from dataclasses import dataclass


@dataclass(slots=True)
class AdminSession:
    search_query: str = ""


class AdminSessions:
    def __init__(self) -> None:
        self._sessions: dict[int, AdminSession] = {}
        self._client_refs: dict[str, str] = {}

    def for_user(self, user_id: int) -> AdminSession:
        return self._sessions.setdefault(user_id, AdminSession())

    def client_token(self, email: str) -> str:
        token = hashlib.blake2s(email.encode("utf-8"), digest_size=6).hexdigest()
        self._client_refs[token] = email
        return token

    def resolve_client(self, token: str) -> str | None:
        return self._client_refs.get(token)

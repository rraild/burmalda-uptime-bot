import os
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Environment variable {name} is required")
    return value


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be an integer") from exc


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be a number") from exc


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _admin_ids() -> frozenset[int]:
    raw = os.getenv("ADMIN_IDS", "").strip()
    if not raw:
        return frozenset()
    try:
        return frozenset(
            int(value.strip()) for value in raw.split(",") if value.strip()
        )
    except ValueError as exc:
        raise RuntimeError("ADMIN_IDS must contain comma-separated integers") from exc


def _panel_api_url(nodes_api_url: str) -> str:
    configured = os.getenv("PANEL_API_URL", "").strip()
    if configured:
        return configured.rstrip("/")

    parsed = urlsplit(nodes_api_url)
    suffix = "/nodes/list"
    path = parsed.path.rstrip("/")
    if not path.endswith(suffix):
        raise RuntimeError(
            "PANEL_API_URL is required when NODES_API_URL does not end with "
            "/nodes/list"
        )
    api_path = path[: -len(suffix)]
    return urlunsplit((parsed.scheme, parsed.netloc, api_path, "", "")).rstrip("/")


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    chat_id: int
    message_id: int
    nodes_api_url: str
    panel_api_url: str
    api_key: str
    admin_ids: frozenset[int]
    admin_page_size: int
    state_dir: str
    check_interval_seconds: float
    confirmation_delay_seconds: float
    http_timeout_seconds: float
    check_host_result_delay_seconds: float
    max_concurrent_checks: int
    nodes_api_verify_ssl: bool

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        nodes_api_url = _required("NODES_API_URL")
        # Compatibility with the duplicated prefix from the old template.
        if nodes_api_url.startswith("NODES_API_URL="):
            nodes_api_url = nodes_api_url.removeprefix("NODES_API_URL=").strip()

        settings = cls(
            bot_token=_required("BOT_TOKEN"),
            chat_id=_int("CHAT_ID", 0),
            message_id=_int("MESSAGE_ID", 0),
            nodes_api_url=nodes_api_url,
            panel_api_url=_panel_api_url(nodes_api_url),
            api_key=_required("API_KEY"),
            admin_ids=_admin_ids(),
            admin_page_size=_int("ADMIN_PAGE_SIZE", 8),
            state_dir=os.getenv("STATE_DIR", "data"),
            check_interval_seconds=_float("CHECK_INTERVAL_SECONDS", 60),
            confirmation_delay_seconds=_float("CONFIRMATION_DELAY_SECONDS", 7),
            http_timeout_seconds=_float("HTTP_TIMEOUT_SECONDS", 10),
            check_host_result_delay_seconds=_float(
                "CHECK_HOST_RESULT_DELAY_SECONDS", 8
            ),
            max_concurrent_checks=_int("MAX_CONCURRENT_CHECKS", 4),
            nodes_api_verify_ssl=_bool("NODES_API_VERIFY_SSL", False),
        )
        if settings.chat_id == 0 or settings.message_id <= 0:
            raise RuntimeError(
                "CHAT_ID and MESSAGE_ID must identify the status message"
            )
        if settings.max_concurrent_checks < 1:
            raise RuntimeError("MAX_CONCURRENT_CHECKS must be at least 1")
        if not 1 <= settings.admin_page_size <= 20:
            raise RuntimeError("ADMIN_PAGE_SIZE must be between 1 and 20")
        positive_delays = {
            "CHECK_INTERVAL_SECONDS": settings.check_interval_seconds,
            "HTTP_TIMEOUT_SECONDS": settings.http_timeout_seconds,
            "CHECK_HOST_RESULT_DELAY_SECONDS": settings.check_host_result_delay_seconds,
        }
        for name, value in positive_delays.items():
            if value <= 0:
                raise RuntimeError(f"{name} must be greater than zero")
        if settings.confirmation_delay_seconds < 0:
            raise RuntimeError("CONFIRMATION_DELAY_SECONDS cannot be negative")
        return settings

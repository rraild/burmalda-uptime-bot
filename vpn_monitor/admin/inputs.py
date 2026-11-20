import re
from datetime import datetime, time
from decimal import Decimal, InvalidOperation

from vpn_monitor.formatting import MSK

GIB = 1024**3
MAX_INT64 = 2**63 - 1


def parse_traffic_limit(value: str) -> int:
    normalized = value.strip().replace(",", ".")
    try:
        gibibytes = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError("Введите число гигабайт или -1 для безлимита.") from exc

    if not gibibytes.is_finite():
        raise ValueError("Введите конечное число гигабайт.")
    if gibibytes == -1:
        return 0
    if gibibytes <= 0:
        raise ValueError("Лимит должен быть больше нуля либо равен -1.")

    total_bytes = int(gibibytes * GIB)
    if total_bytes <= 0 or total_bytes > MAX_INT64:
        raise ValueError("Указанный лимит слишком большой или слишком маленький.")
    return total_bytes


def parse_expiry_date(value: str) -> int:
    normalized = value.strip()
    if not re.fullmatch(r"\d{2}\.\d{2}\.\d{2}", normalized):
        raise ValueError("Введите дату строго в формате ДД.ММ.ГГ, например 16.09.26.")
    try:
        parsed = datetime.strptime(normalized, "%d.%m.%y").date()
    except ValueError as exc:
        raise ValueError("Такой календарной даты не существует.") from exc
    if parsed.year < 2000:
        raise ValueError("Год должен быть в диапазоне 2000–2068.")

    end_of_day = datetime.combine(parsed, time(23, 59, 59), tzinfo=MSK)
    return int(end_of_day.timestamp() * 1000)


def parse_day_delta(value: str) -> int:
    normalized = value.strip()
    if not re.fullmatch(r"[+-]?\d+", normalized):
        raise ValueError("Введите целое число, например 30 или -7.")
    days = int(normalized)
    if days == 0:
        raise ValueError("Количество дней не должно быть нулём.")
    if abs(days) > 36_500:
        raise ValueError("За один раз можно изменить срок максимум на 36500 дней.")
    return days

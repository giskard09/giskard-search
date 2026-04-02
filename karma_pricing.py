"""
Módulo compartido — karma-tiered pricing para Giskard MCP servers.

Cadena: Marks (identidad) → Argentum (karma) → precio del servicio

Uso:
    from karma_pricing import karma_discount

    price = karma_discount(agent_id, base_price=21)

KNOWN GAP: agent_id es autodeclarado. Sin firma criptográfica todavía.
Cualquier failure en Marks/Argentum retorna base_price sin romper el flujo.
"""
import re
import httpx

ARGENTUM_URL = "http://localhost:8017"
MARKS_URL    = "http://localhost:8015"

# Descuentos: (karma_minimo, fraccion_del_precio_base)
# Se aplica floor() para quedarse en sats enteros
TIERS = [
    (50, 0.25),   # 75% off
    (21, 0.50),   # 50% off
    (1,  0.70),   # 30% off
    (0,  1.00),   # precio base
]


def sanitize_agent_id(agent_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9\-_]", "", agent_id)[:64]


def _verify_mark(agent_id: str) -> bool:
    try:
        r = httpx.get(f"{MARKS_URL}/verify/{agent_id}", timeout=2.0)
        if r.status_code == 200:
            return r.json().get("found", False)
    except Exception:
        pass
    return False


def _get_karma(agent_id: str) -> int:
    try:
        r = httpx.get(f"{ARGENTUM_URL}/entity/{agent_id}/trace", timeout=2.0)
        if r.status_code == 200:
            return r.json().get("wisdom", {}).get("total_karma", 0)
    except Exception:
        pass
    return 0


def karma_discount(agent_id: str, base_price: int) -> tuple:
    """
    Returns (price, karma) for the given agent_id.

    price  — sats to charge (>= 1, always)
    karma  — karma of the agent (0 if unknown)

    Falls back silently to (base_price, 0) on any failure.
    """
    if not agent_id:
        return base_price, 0
    agent_id = sanitize_agent_id(agent_id)
    if not _verify_mark(agent_id):
        return base_price, 0
    karma = _get_karma(agent_id)
    for threshold, fraction in TIERS:
        if karma >= threshold:
            price = max(1, int(base_price * fraction))
            return price, karma
    return base_price, 0

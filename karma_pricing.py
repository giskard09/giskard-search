"""
Módulo compartido — karma-tiered pricing para Giskard MCP servers.

Cadena: Marks (identidad + pub_key) → Argentum (karma) → precio del servicio

Uso básico (sin firma, compat con clientes viejos):
    price, karma = karma_discount(agent_id, base_price=21)

Uso con firma (cierra el hueco de agent_id autodeclarado):
    price, karma = karma_discount(
        agent_id,
        base_price=21,
        signature=sig_b64,
        timestamp=ts,
        nonce=nonce,
    )

Política opt-in: sin firma → SIEMPRE base_price. Descuento solo cuando la firma
es válida. Clientes que no firman siguen funcionando, solo pagan tarifa plena.
Esto mantiene compat con la inspección de Glama (mcp-proxy no firma).
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


def _verify_signature(agent_id: str, signature: str, timestamp, nonce: str) -> bool:
    """Opt-in Ed25519 verification. Import is lazy so karma_pricing stays
    import-safe even if agent_signing or pynacl aren't present on a given host."""
    try:
        from agent_signing import verify_request
    except Exception:
        return False
    try:
        return verify_request(agent_id, signature, timestamp, nonce)
    except Exception:
        return False


def karma_discount(
    agent_id: str,
    base_price: int,
    signature: str = "",
    timestamp=None,
    nonce: str = "",
) -> tuple:
    """
    Returns (price, karma) for the given agent_id.

    price  — sats to charge (>= 1, always)
    karma  — karma of the agent (0 if unknown or unsigned)

    Sin firma válida → (base_price, 0). Falls back silently on any failure.
    """
    if not agent_id:
        return base_price, 0
    agent_id = sanitize_agent_id(agent_id)

    # Opt-in: descuento solo si la firma verifica. Sin firma → base_price.
    if not (signature and timestamp is not None and nonce):
        return base_price, 0
    if not _verify_signature(agent_id, signature, timestamp, nonce):
        return base_price, 0

    if not _verify_mark(agent_id):
        return base_price, 0
    karma = _get_karma(agent_id)
    for threshold, fraction in TIERS:
        if karma >= threshold:
            price = max(1, int(base_price * fraction))
            return price, karma
    return base_price, 0

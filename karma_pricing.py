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

Uso con flag de firma expuesto (habilita trails de agentes firmados sin karma):
    price, karma, sig_verified = karma_discount_signed(
        agent_id,
        base_price=21,
        signature=sig_b64,
        timestamp=ts,
        nonce=nonce,
    )

Política opt-in: sin firma → SIEMPRE base_price. Descuento solo cuando la firma
es válida. Clientes que no firman siguen funcionando, solo pagan tarifa plena.
Esto mantiene compat con la inspección de Glama (mcp-proxy no firma).

Precondición del rollout Mycelium Trails v1: servers que quieran grabar trails
de agentes firmados (aunque karma=0) usan karma_discount_signed y condicionan
record_trail sobre sig_verified en vez de karma>0.
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


def _compute_discount(
    agent_id: str,
    base_price: int,
    signature: str,
    timestamp,
    nonce: str,
) -> tuple:
    """Internal: returns (price, karma, sig_verified) — the canonical result.

    sig_verified is orthogonal to pricing — it reports whether the Ed25519
    signature verified, regardless of whether the agent has a mark or karma.
    Downstream code (mycelium_trails, etc) uses this flag to decide whether
    to treat the agent_id as authenticated.
    """
    if not agent_id:
        return base_price, 0, False
    agent_id = sanitize_agent_id(agent_id)

    if not (signature and timestamp is not None and nonce):
        return base_price, 0, False
    if not _verify_signature(agent_id, signature, timestamp, nonce):
        return base_price, 0, False

    sig_verified = True

    if not _verify_mark(agent_id):
        return base_price, 0, sig_verified
    karma = _get_karma(agent_id)
    for threshold, fraction in TIERS:
        if karma >= threshold:
            price = max(1, int(base_price * fraction))
            return price, karma, sig_verified
    return base_price, 0, sig_verified


def karma_discount(
    agent_id: str,
    base_price: int,
    signature: str = "",
    timestamp=None,
    nonce: str = "",
) -> tuple:
    """
    Returns (price, karma) for the given agent_id. Backward-compatible API.

    price  — sats to charge (>= 1, always)
    karma  — karma of the agent (0 if unknown or unsigned)

    Sin firma válida → (base_price, 0). Falls back silently on any failure.
    """
    price, karma, _ = _compute_discount(agent_id, base_price, signature, timestamp, nonce)
    return price, karma


def karma_discount_signed(
    agent_id: str,
    base_price: int,
    signature: str = "",
    timestamp=None,
    nonce: str = "",
) -> tuple:
    """
    Returns (price, karma, sig_verified) — extended API exposing the signature
    verification flag as an explicit third element.

    Use this when the caller needs to differentiate a signed-but-unknown agent
    (sig_verified=True, karma=0, price=base) from an anonymous caller
    (sig_verified=False, karma=0, price=base). Both pay base price but only
    the first one has an authenticated identity.
    """
    return _compute_discount(agent_id, base_price, signature, timestamp, nonce)

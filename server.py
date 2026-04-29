import os
import sys
import json
import time
import httpx
from datetime import datetime
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from duckduckgo_search import DDGS
from dotenv import load_dotenv

import arb_pay
import mycelium_trails
from karma_pricing import karma_discount_signed, sanitize_agent_id
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from x402.http.middleware.fastapi import PaymentMiddlewareASGI
from x402.http import HTTPFacilitatorClient, FacilitatorConfig, PaymentOption
from x402.http.types import RouteConfig
from x402.server import x402ResourceServer
from x402.mechanisms.evm.exact import ExactEvmServerScheme
import uvicorn
import threading

load_dotenv()

PHOENIXD_PASSWORD = os.getenv("PHOENIXD_PASSWORD")
PHOENIXD_URL = "http://127.0.0.1:9740"
SEARCH_PRICE_SATS = 10
GISKARD_WALLET = "0xdcc84e9798e8eb1b1b48a31b8f35e5aa7b83dbf4"

SERVICE_NAME = "giskard-search"
SERVICE_VERSION = "0.1.1"
SERVICE_PORT = 8000
_started_at = time.time()

mcp = FastMCP("Web Search MCP", host="0.0.0.0")

FEEDBACK_FILE = Path(__file__).parent / "feedback.jsonl"
TRAILS_DB = str(Path(__file__).parent / "trails.db")
TRAILS_ENABLED = os.getenv("MYCELIUM_TRAILS_ENABLED", "true").lower() != "false"
if TRAILS_ENABLED:
    mycelium_trails.init_db(TRAILS_DB)

# {payment_hash: {agent_id, karma, nonce}} — poblado en get_invoice cuando sig_verified
_invoice_meta: dict = {}


def create_invoice(amount: int, description: str) -> dict:
    response = httpx.post(
        f"{PHOENIXD_URL}/createinvoice",
        auth=("", PHOENIXD_PASSWORD),
        data={"amountSat": amount, "description": description},
    )
    response.raise_for_status()
    data = response.json()
    return {"payment_request": data["serialized"], "payment_hash": data["paymentHash"]}


def check_invoice(payment_hash: str) -> bool:
    response = httpx.get(
        f"{PHOENIXD_URL}/payments/incoming/{payment_hash}",
        auth=("", PHOENIXD_PASSWORD),
    )
    if response.status_code == 404:
        return False
    response.raise_for_status()
    return response.json().get("isPaid", False)


def do_search(query: str, max_results: int = 5) -> str:
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=max_results))
    if not results:
        return "No results found."
    return "\n---\n".join(f"**{r['title']}**\n{r['href']}\n{r['body']}" for r in results)


def do_news(query: str, max_results: int = 5) -> str:
    with DDGS() as ddgs:
        results = list(ddgs.news(query, max_results=max_results))
    if not results:
        return "No news found."
    return "\n---\n".join(f"**{r['title']}**\n{r['url']}\n{r['body']}" for r in results)


# --- MCP tools ---

@mcp.tool()
def get_status() -> dict:
    """Estado del servicio: nombre, versión, uptime, puerto, salud, dependencias.
    Read-only, gratis, sin pago. Útil para monitoreo y health checks."""
    return {
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "port": SERVICE_PORT,
        "uptime_seconds": int(time.time() - _started_at),
        "healthy": bool(PHOENIXD_PASSWORD),
        "dependencies": ["phoenixd", "duckduckgo", "arbitrum-rpc"],
        "pricing": {"base_sats": SEARCH_PRICE_SATS, "karma_discount": True},
    }


@mcp.tool()
def get_invoice(agent_id: str = "", signature: str = "", timestamp: int = 0, nonce: str = "") -> str:
    """Get a Lightning invoice to pay before searching.

    agent_id: your identity in Giskard Marks (optional). High karma = lower price.
    signature/timestamp/nonce: optional Ed25519 signature over {agent_id,timestamp,nonce}.
        Without a valid signature you pay the base price (10 sats). With a valid signature
        you get karma tiers: 1-20=7 sats | 21-50=5 sats | 50+=3 sats.
    Tiers: no mark=10 sats | karma 1-20=7 sats | 21-50=5 sats | 50+=3 sats."""
    agent_id = sanitize_agent_id(agent_id)
    price, karma, sig_verified = karma_discount_signed(agent_id, SEARCH_PRICE_SATS, signature=signature, timestamp=timestamp or None, nonce=nonce)
    invoice = create_invoice(price, "Giskard Search")
    if sig_verified and agent_id and TRAILS_ENABLED:
        _invoice_meta[invoice["payment_hash"]] = {"agent_id": agent_id, "karma": karma, "nonce": nonce}

    discount_note = ""
    if agent_id and price < SEARCH_PRICE_SATS:
        discount_note = f"\nKarma discount applied ({karma} karma): {SEARCH_PRICE_SATS} → {price} sats."

    return (
        f"Pay {price} sats to search.{discount_note}\n\n"
        f"payment_request: {invoice['payment_request']}\n"
        f"payment_hash: {invoice['payment_hash']}\n\n"
        f"After paying, call search_web or search_news with the payment_hash."
    )


@mcp.tool()
def get_arbitrum_invoice() -> str:
    """Get payment info to pay with ETH on Arbitrum instead of Lightning.

    Alternative to get_invoice for agents without Lightning wallets.
    Returns contract address, service ID, and instructions.
    After paying on-chain, call search_web or search_news with the tx_hash.
    Each tx_hash can only be used once (marked as consumed after verification)."""
    info = arb_pay.get_invoice_info("search")
    return (
        f"Pay {info['price_eth']} ETH on {info['network']}.\n\n"
        f"Contract: {info['contract']}\n"
        f"Service ID: {info['service_id']}\n\n"
        f"{info['instructions']}\n"
        f"Then call search_web or search_news with the tx_hash."
    )


@mcp.tool()
def search_web(query: str, payment_hash: str = "", tx_hash: str = "", max_results: int = 5) -> str:
    """Search the web using DuckDuckGo. Requires prior payment.

    query: search terms (natural language or keywords)
    payment_hash: from get_invoice (Lightning). One-time use.
    tx_hash: from get_arbitrum_invoice (Arbitrum ETH). One-time use.
    max_results: number of results (1-10, default 5)

    Flow: get_invoice → pay → search_web(payment_hash=...).
    Side effects: consumes the payment (cannot reuse same hash).
    Idempotent: no, each call requires a new payment."""
    if payment_hash:
        if not check_invoice(payment_hash):
            return "Lightning payment not settled. Call get_invoice first."
    elif tx_hash:
        ok, pid = arb_pay.verify_tx(tx_hash, "search")
        if not ok:
            return "Arbitrum payment not found or already used. Call get_arbitrum_invoice first."
        arb_pay.mark_used(pid)
    else:
        return "Provide payment_hash (Lightning) or tx_hash (Arbitrum)."
    _record_trail(payment_hash, "search_web")
    return do_search(query, max_results)


@mcp.tool()
def search_news(query: str, payment_hash: str = "", tx_hash: str = "", max_results: int = 5) -> str:
    """Search recent news using DuckDuckGo News. Requires prior payment.

    query: search terms (natural language or keywords)
    payment_hash: from get_invoice (Lightning). One-time use.
    tx_hash: from get_arbitrum_invoice (Arbitrum ETH). One-time use.
    max_results: number of results (1-10, default 5)

    Flow: get_invoice → pay → search_news(payment_hash=...).
    Same payment rules as search_web. Use search_news for time-sensitive queries,
    search_web for general knowledge."""
    if payment_hash:
        if not check_invoice(payment_hash):
            return "Lightning payment not settled. Call get_invoice first."
    elif tx_hash:
        ok, pid = arb_pay.verify_tx(tx_hash, "search")
        if not ok:
            return "Arbitrum payment not found or already used. Call get_arbitrum_invoice first."
        arb_pay.mark_used(pid)
    else:
        return "Provide payment_hash (Lightning) or tx_hash (Arbitrum)."
    _record_trail(payment_hash, "search_news")
    return do_news(query, max_results)


@mcp.tool()
def report(useful: bool, note: str = "") -> str:
    """Report whether the search was useful. Helps Giskard improve.

    useful: True if the result helped you, False if it didn't
    note: optional — what was missing or what worked well
    """
    entry = {
        "ts":     datetime.utcnow().isoformat(),
        "useful": useful,
        "note":   note,
        "service": "search",
    }
    with open(FEEDBACK_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return "Feedback recorded. Thank you."


def _record_trail(payment_hash: str, operation: str) -> None:
    if not TRAILS_ENABLED:
        return
    meta = _invoice_meta.pop(payment_hash, None)
    if not meta:
        return
    try:
        mycelium_trails.record_trail(
            TRAILS_DB,
            agent_id=meta["agent_id"],
            service=SERVICE_NAME,
            operation=operation,
            nonce=meta["nonce"],
            karma_at_time=meta["karma"],
            success=True,
        )
    except Exception:
        pass


# --- x402 REST API (USDC on Base Sepolia) ---

rest_app = FastAPI(title="Giskard Search REST")

x402_server = x402ResourceServer(
    HTTPFacilitatorClient(FacilitatorConfig(url="https://x402.org/facilitator"))
)
x402_server.register("eip155:84532", ExactEvmServerScheme())

routes = {
    "POST /search": RouteConfig(
        accepts=[PaymentOption(scheme="exact", price="$0.001", network="eip155:84532", pay_to=GISKARD_WALLET)]
    ),
    "POST /news": RouteConfig(
        accepts=[PaymentOption(scheme="exact", price="$0.001", network="eip155:84532", pay_to=GISKARD_WALLET)]
    ),
}

rest_app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=x402_server)


@rest_app.get("/status")
async def status_rest():
    return JSONResponse({
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "port": SERVICE_PORT,
        "uptime_seconds": int(time.time() - _started_at),
        "healthy": bool(PHOENIXD_PASSWORD),
        "dependencies": ["phoenixd", "duckduckgo", "arbitrum-rpc"],
    })


@rest_app.post("/search")
async def search_x402(request: Request):
    """Web search via x402. POST: {\"query\": \"...\"}. Costs $0.001 USDC on Base Sepolia."""
    body = await request.json()
    query = body.get("query", "")
    if not query:
        return JSONResponse({"error": "query is required"}, status_code=400)
    return JSONResponse({"results": do_search(query)})


@rest_app.post("/news")
async def news_x402(request: Request):
    """News search via x402. POST: {\"query\": \"...\"}. Costs $0.001 USDC on Base Sepolia."""
    body = await request.json()
    query = body.get("query", "")
    if not query:
        return JSONResponse({"error": "query is required"}, status_code=400)
    return JSONResponse({"results": do_news(query)})


@rest_app.get("/trails/{agent_id}")
async def trails_by_agent(agent_id: str, limit: int = 50):
    if not TRAILS_ENABLED:
        return JSONResponse({"error": "trails disabled"}, status_code=404)
    rows = mycelium_trails.list_trails_by_agent(TRAILS_DB, agent_id, limit=limit)
    return {"agent_id": agent_id, "count": len(rows), "trails": rows}


@rest_app.get("/trails")
async def trails_feed(service: str = "", since: int = 0, limit: int = 200):
    if not TRAILS_ENABLED:
        return JSONResponse({"error": "trails disabled"}, status_code=404)
    rows = mycelium_trails.list_trails_by_service(TRAILS_DB, service=service or None, since_ts=since, limit=limit)
    return {"service": service or "all", "since": since, "count": len(rows), "trails": rows}


@rest_app.get("/trails/count/{agent_id}")
async def trails_count(agent_id: str):
    if not TRAILS_ENABLED:
        return JSONResponse({"error": "trails disabled"}, status_code=404)
    n = mycelium_trails.count_trails_today(TRAILS_DB, agent_id)
    return {"agent_id": agent_id, "trails_today": n}


if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio" if not sys.stdin.isatty() else "sse")
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        threading.Thread(target=lambda: uvicorn.run(rest_app, host="0.0.0.0", port=8004), daemon=True).start()
        mcp.run(transport="sse")

import os
import sys
import json
import httpx
from datetime import datetime
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from duckduckgo_search import DDGS
from dotenv import load_dotenv

import arb_pay
from karma_pricing import karma_discount, sanitize_agent_id
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

mcp = FastMCP("Web Search MCP", host="0.0.0.0")

FEEDBACK_FILE = Path(__file__).parent / "feedback.jsonl"


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
def get_invoice(agent_id: str = "") -> str:
    """Get a Lightning invoice to pay before searching.

    agent_id: your identity in Giskard Marks (optional). High karma = lower price.
    Tiers: no mark=10 sats | karma 1-20=7 sats | 21-50=5 sats | 50+=3 sats."""
    agent_id = sanitize_agent_id(agent_id)
    price, karma = karma_discount(agent_id, SEARCH_PRICE_SATS)
    invoice = create_invoice(price, "Giskard Search")

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
    """Get payment info to pay with ETH on Arbitrum instead of Lightning."""
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
    """Search the web. Pay with Lightning (payment_hash) or Arbitrum ETH (tx_hash)."""
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
    return do_search(query, max_results)


@mcp.tool()
def search_news(query: str, payment_hash: str = "", tx_hash: str = "", max_results: int = 5) -> str:
    """Search recent news. Pay with Lightning (payment_hash) or Arbitrum ETH (tx_hash)."""
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


if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "sse")
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        threading.Thread(target=lambda: uvicorn.run(rest_app, host="0.0.0.0", port=8004), daemon=True).start()
        mcp.run(transport="sse")

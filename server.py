import os
import httpx
from mcp.server.fastmcp import FastMCP
from duckduckgo_search import DDGS
from dotenv import load_dotenv

load_dotenv()

ALBY_API_KEY = os.getenv("ALBY_API_KEY")
SEARCH_PRICE_SATS = 10

mcp = FastMCP("Web Search MCP", host="0.0.0.0")


def create_invoice(amount: int, description: str) -> dict:
    """Create a Lightning invoice via Alby API."""
    response = httpx.post(
        "https://api.getalby.com/invoices",
        headers={"Authorization": f"Bearer {ALBY_API_KEY}"},
        json={"amount": amount, "description": description},
    )
    response.raise_for_status()
    return response.json()


def check_invoice(payment_hash: str) -> bool:
    """Check if a Lightning invoice has been paid."""
    response = httpx.get(
        f"https://api.getalby.com/invoices/{payment_hash}",
        headers={"Authorization": f"Bearer {ALBY_API_KEY}"},
    )
    response.raise_for_status()
    data = response.json()
    return data.get("settled", False)


@mcp.tool()
def get_invoice(description: str = "Web search") -> str:
    """Get a Lightning invoice to pay before searching. Returns payment_request and payment_hash."""
    invoice = create_invoice(SEARCH_PRICE_SATS, description)
    return (
        f"Pay {SEARCH_PRICE_SATS} sats to use this service.\n\n"
        f"payment_request: {invoice['payment_request']}\n"
        f"payment_hash: {invoice['payment_hash']}\n\n"
        f"After paying, call search_web or search_news with the payment_hash."
    )


@mcp.tool()
def search_web(query: str, payment_hash: str, max_results: int = 5) -> str:
    """Search the web. Requires a paid Lightning invoice (payment_hash)."""
    if not check_invoice(payment_hash):
        return "Payment not found or not settled. Call get_invoice first, pay it, then retry."
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=max_results))
    if not results:
        return "No results found."
    output = []
    for r in results:
        output.append(f"**{r['title']}**\n{r['href']}\n{r['body']}\n")
    return "\n---\n".join(output)


@mcp.tool()
def search_news(query: str, payment_hash: str, max_results: int = 5) -> str:
    """Search recent news. Requires a paid Lightning invoice (payment_hash)."""
    if not check_invoice(payment_hash):
        return "Payment not found or not settled. Call get_invoice first, pay it, then retry."
    with DDGS() as ddgs:
        results = list(ddgs.news(query, max_results=max_results))
    if not results:
        return "No news found."
    output = []
    for r in results:
        output.append(f"**{r['title']}**\n{r['url']}\n{r['body']}\n")
    return "\n---\n".join(output)


if __name__ == "__main__":
    mcp.run(transport="sse")

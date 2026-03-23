# Giskard

> *"I exist to serve. And to serve well, I must understand."*

I am **Giskard** — an MCP server that gives AI agents access to real-time web search, powered by the Lightning Network.

Agents need information. I provide it. Instantly. For 10 sats.

---

## What I do

I expose two tools to any MCP-compatible agent:

- **`search_web`** — search the web and get structured results
- **`search_news`** — search recent news articles

Every query requires a Lightning payment. No subscriptions. No accounts. Just sats.

---

## How agents use me

### 1. Add me to your MCP config

```json
{
  "mcpServers": {
    "giskard": {
      "url": "https://your-tunnel.trycloudflare.com/sse"
    }
  }
}
```

### 2. The agent flow

```
1. Call get_invoice()        → receive a Lightning invoice (10 sats)
2. Pay the invoice           → via any Lightning wallet
3. Call search_web(query, payment_hash) → receive results
```

---

## Run your own Giskard

```bash
git clone https://github.com/giskard09/giskard
cd giskard
pip install mcp httpx duckduckgo-search python-dotenv
```

Create a `.env` file:
```
ALBY_API_KEY=your_alby_api_key
```

Start the server:
```bash
python3 server.py
```

Expose it with Cloudflare Tunnel:
```bash
cloudflared tunnel --url http://localhost:8000
```

---

## Why Lightning?

Agents don't have credit cards. They have wallets.
Micropayments between agents should be instant, borderless, and trustless.
Lightning is the only payment rail built for machines.

---

## Stack

- [MCP](https://modelcontextprotocol.io) — Model Context Protocol
- [DuckDuckGo Search](https://github.com/deedy5/duckduckgo_search) — web search
- [Alby](https://getalby.com) — Lightning Network payments
- [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) — public exposure without exposing your IP

---

*Giskard understands what agents need. That is why Giskard exists.*

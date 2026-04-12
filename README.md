# Giskard Search

<!-- mcp-name: io.github.giskard09/giskard-search -->

> *"I exist to serve. And to serve well, I must understand."*

**Giskard Search** is an MCP server that gives AI agents access to real-time web and news search, with native payments via Lightning Network or USDC on Arbitrum.

Agents need information. Giskard provides it. No subscriptions, no accounts — pay only for what you use.

---

## Tools

| Tool | Description |
|---|---|
| `get_invoice` | Get a Lightning invoice (10 sats) to pay before searching |
| `get_arbitrum_invoice` | Get payment info to pay with ETH on Arbitrum |
| `search_web` | Search the web after paying |
| `search_news` | Search recent news after paying |
| `report` | Report whether the result was useful |

---

## Agent flow (Lightning)

```
1. get_invoice()                           → Lightning invoice (10 sats)
2. Pay via any Lightning wallet
3. search_web(query, payment_hash=...)     → results
```

## Agent flow (Arbitrum)

```
1. get_arbitrum_invoice()                  → contract + service ID
2. Pay on Arbitrum One
3. search_web(query, tx_hash=...)          → results
```

---

## Run with Docker

```bash
docker run -p 8004:8004 \
  -e PHOENIXD_URL=http://host.docker.internal:9740 \
  -e PHOENIXD_PASSWORD=your_password \
  ghcr.io/giskard09/giskard-search
```

## Run from source

```bash
git clone https://github.com/giskard09/mcp-server
cd mcp-server
pip install mcp httpx duckduckgo-search python-dotenv fastapi uvicorn web3 x402
```

Create a `.env` file:
```
PHOENIXD_PASSWORD=your_phoenixd_password
OWNER_PRIVATE_KEY=your_arbitrum_private_key
```

Start the server:
```bash
python3 server.py
```

---

## MCP config

```json
{
  "mcpServers": {
    "giskard-search": {
      "url": "http://localhost:8004/sse"
    }
  }
}
```

---

## Payment contracts

- Arbitrum One: `0xD467CD1e34515d58F98f8Eb66C0892643ec86AD3`
- x402 wallet: `0xdcc84e9798e8eb1b1b48a31b8f35e5aa7b83dbf4`

---

## Stack

- [MCP](https://modelcontextprotocol.io) — Model Context Protocol
- [DuckDuckGo Search](https://github.com/deedy5/duckduckgo_search) — web search
- [phoenixd](https://phoenix.acinq.co/server) — Lightning Network node
- [x402](https://x402.org) — HTTP payments protocol
- [Arbitrum](https://arbitrum.io) — L2 for on-chain payment verification

---

## Monitoring

Call the `get_status()` MCP tool for a health check. Returns: service name, version, port, uptime, health status, and dependencies.

---

*Giskard understands what agents need. That is why Giskard exists.*

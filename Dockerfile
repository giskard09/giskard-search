FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    mcp \
    httpx \
    duckduckgo-search \
    python-dotenv \
    fastapi \
    uvicorn \
    web3 \
    x402

COPY server.py .
COPY arb_pay.py .

ENV PHOENIXD_URL=http://host.docker.internal:9740
ENV PHOENIXD_PASSWORD=""
ENV ARBITRUM_RPC=https://arb1.arbitrum.io/rpc
ENV GISKARD_CONTRACT_ADDRESS=0xD467CD1e34515d58F98f8Eb66C0892643ec86AD3
ENV OWNER_PRIVATE_KEY=""

EXPOSE 8004

CMD ["python3", "server.py"]

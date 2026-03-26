"""
Módulo compartido — verificación de pagos en Arbitrum para Giskard MCP servers.
"""
import os
from web3 import Web3

ARBITRUM_RPC      = os.getenv("ARBITRUM_RPC", "https://arb1.arbitrum.io/rpc")
CONTRACT_ADDRESS  = os.getenv("GISKARD_CONTRACT_ADDRESS", "0xD467CD1e34515d58F98f8Eb66C0892643ec86AD3")
OWNER_PRIVATE_KEY = os.getenv("OWNER_PRIVATE_KEY", "")

SERVICE_IDS = {
    "search":        0,
    "memory_store":  1,
    "memory_recall": 2,
    "oasis":         3,
}

# Precios en wei (deben coincidir con el contrato)
SERVICE_PRICES = {
    "search":        6_000_000_000_000,   # 0.000006 ETH
    "memory_store":  3_000_000_000_000,   # 0.000003 ETH
    "memory_recall": 2_000_000_000_000,   # 0.000002 ETH
    "oasis":         12_000_000_000_000,  # 0.000012 ETH
}

ABI = [
    {
        "inputs": [{"name": "paymentId", "type": "bytes32"}],
        "name": "isUsed", "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view", "type": "function",
    },
    {
        "inputs": [{"name": "paymentId", "type": "bytes32"}],
        "name": "markUsed", "outputs": [],
        "stateMutability": "nonpayable", "type": "function",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True,  "name": "paymentId", "type": "bytes32"},
            {"indexed": True,  "name": "payer",     "type": "address"},
            {"indexed": True,  "name": "service",   "type": "uint8"},
            {"indexed": False, "name": "amount",    "type": "uint256"},
        ],
        "name": "PaymentReceived", "type": "event",
    },
]

_w3       = None
_contract = None
_owner    = None


def _setup():
    global _w3, _contract, _owner
    if _w3 is None:
        _w3 = Web3(Web3.HTTPProvider(ARBITRUM_RPC))
        _contract = _w3.eth.contract(
            address=Web3.to_checksum_address(CONTRACT_ADDRESS),
            abi=ABI,
        )
        if OWNER_PRIVATE_KEY:
            _owner = _w3.eth.account.from_key(OWNER_PRIVATE_KEY)


def get_invoice_info(service: str) -> dict:
    """Retorna los datos para que el agente pague on-chain."""
    sid   = SERVICE_IDS.get(service)
    price = SERVICE_PRICES.get(service, 0)
    chain = "arbitrum-one" if "sepolia" in ARBITRUM_RPC else "arbitrum-one"
    return {
        "network":      chain,
        "contract":     CONTRACT_ADDRESS,
        "service_id":   sid,
        "price_wei":    price,
        "price_eth":    str(Web3.from_wei(price, "ether")),
        "instructions": (
            f"Call pay({sid}) on the contract sending {price} wei. "
            "Then pass the transaction hash to the tool."
        ),
    }


def verify_tx(tx_hash: str, service: str) -> tuple[bool, bytes | None]:
    """Verifica que tx_hash es un pago válido y no usado para el servicio."""
    _setup()
    sid = SERVICE_IDS.get(service)
    try:
        receipt = _w3.eth.get_transaction_receipt(tx_hash)
        if not receipt or receipt.status != 1:
            return False, None
        logs = _contract.events.PaymentReceived().process_receipt(receipt)
        for log in logs:
            args = log["args"]
            if args["service"] == sid:
                pid = args["paymentId"]
                if not _contract.functions.isUsed(pid).call():
                    return True, pid
    except Exception:
        return False, None
    return False, None


def mark_used(payment_id: bytes):
    """Marca el payment_id como usado en el contrato."""
    _setup()
    if not _owner:
        return
    tx = _contract.functions.markUsed(payment_id).build_transaction({
        "from":  _owner.address,
        "nonce": _w3.eth.get_transaction_count(_owner.address),
        "gas":   100_000,
    })
    signed = _w3.eth.account.sign_transaction(tx, OWNER_PRIVATE_KEY)
    _w3.eth.send_raw_transaction(signed.raw_transaction)

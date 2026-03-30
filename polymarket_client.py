"""
polymarket_client.py — Wrapper para la API de Polymarket (Gamma + CLOB).

Autenticación vía HMAC-SHA256 con los headers:
  POLY-API-KEY, POLY-API-SIGNATURE, POLY-API-TIMESTAMP, POLY-API-PASSPHRASE
"""

import hashlib
import hmac
import logging
import os
import time
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"


class PolymarketClient:
    """Cliente para las APIs pública (Gamma) y autenticada (CLOB) de Polymarket."""

    def __init__(self) -> None:
        self.api_key = os.getenv("POLYMARKET_API_KEY", "")
        self.secret = os.getenv("POLYMARKET_SECRET", "")
        self.passphrase = os.getenv("POLYMARKET_PASSPHRASE", "")
        self._http = httpx.Client(timeout=30)

    # ------------------------------------------------------------------
    # Helpers de autenticación
    # ------------------------------------------------------------------

    def _sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        """Genera la firma HMAC-SHA256 para la petición."""
        message = timestamp + method.upper() + path + body
        signature = hmac.new(
            self.secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signature

    def _auth_headers(self, method: str, path: str, body: str = "") -> dict[str, str]:
        """Devuelve los headers de autenticación requeridos por la CLOB API."""
        timestamp = str(int(time.time()))
        return {
            "POLY-API-KEY": self.api_key,
            "POLY-API-SIGNATURE": self._sign(timestamp, method, path, body),
            "POLY-API-TIMESTAMP": timestamp,
            "POLY-API-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Gamma API (pública) — discovery de mercados
    # ------------------------------------------------------------------

    def get_markets(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """Obtiene mercados activos desde la Gamma API pública."""
        try:
            resp = self._http.get(
                f"{GAMMA_BASE}/markets",
                params={
                    "active": "true",
                    "closed": "false",
                    "limit": limit,
                    "offset": offset,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else data.get("markets", [])
        except httpx.HTTPError as exc:
            logger.error("Error obteniendo mercados de Gamma API: %s", exc)
            return []

    def get_market(self, market_id: str) -> dict[str, Any] | None:
        """Obtiene el detalle de un mercado por su ID."""
        try:
            resp = self._http.get(f"{GAMMA_BASE}/markets/{market_id}")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            logger.error("Error obteniendo mercado %s: %s", market_id, exc)
            return None

    # ------------------------------------------------------------------
    # CLOB API (autenticada) — order book y ejecución
    # ------------------------------------------------------------------

    def get_orderbook(self, token_id: str) -> dict[str, Any] | None:
        """Obtiene el order book de un token desde la CLOB API."""
        try:
            path = f"/book?tokenId={token_id}"
            resp = self._http.get(
                f"{CLOB_BASE}/book",
                params={"tokenId": token_id},
                headers=self._auth_headers("GET", path),
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            logger.error("Error obteniendo order book para %s: %s", token_id, exc)
            return None

    def get_best_price(self, token_id: str, side: str = "YES") -> float | None:
        """
        Calcula el mejor precio disponible para YES o NO.
        Devuelve el mid-price del order book.
        """
        book = self.get_orderbook(token_id)
        if not book:
            return None
        try:
            if side.upper() == "YES":
                asks = book.get("asks", [])
                if asks:
                    return float(sorted(asks, key=lambda x: float(x["price"]))[0]["price"])
            else:
                bids = book.get("bids", [])
                if bids:
                    return float(sorted(bids, key=lambda x: float(x["price"]), reverse=True)[0]["price"])
        except (KeyError, ValueError, IndexError) as exc:
            logger.error("Error parseando order book: %s", exc)
        return None

    def place_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
    ) -> dict[str, Any] | None:
        """
        Coloca una orden en la CLOB API.

        Args:
            token_id: ID del token (YES o NO).
            side: 'BUY' o 'SELL'.
            price: Precio límite (0-1).
            size: Tamaño de la orden en USD.
        """
        import json

        body_data = {
            "tokenID": token_id,
            "side": side.upper(),
            "price": round(price, 4),
            "size": round(size, 2),
            "type": "GTC",
        }
        body_str = json.dumps(body_data, separators=(",", ":"))
        path = "/order"
        headers = self._auth_headers("POST", path, body_str)
        try:
            resp = self._http.post(
                f"{CLOB_BASE}/order",
                content=body_str,
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            logger.error("Error colocando orden: %s", exc)
            return None

    def cancel_order(self, order_id: str) -> bool:
        """Cancela una orden por su ID."""
        path = f"/order/{order_id}"
        headers = self._auth_headers("DELETE", path)
        try:
            resp = self._http.delete(f"{CLOB_BASE}/order/{order_id}", headers=headers)
            resp.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            logger.error("Error cancelando orden %s: %s", order_id, exc)
            return False

    def get_positions(self) -> list[dict[str, Any]]:
        """Obtiene las posiciones abiertas del usuario."""
        path = "/positions"
        headers = self._auth_headers("GET", path)
        try:
            resp = self._http.get(f"{CLOB_BASE}/positions", headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else data.get("positions", [])
        except httpx.HTTPError as exc:
            logger.error("Error obteniendo posiciones: %s", exc)
            return []

    def get_orders(self) -> list[dict[str, Any]]:
        """Obtiene las órdenes activas del usuario."""
        path = "/orders"
        headers = self._auth_headers("GET", path)
        try:
            resp = self._http.get(f"{CLOB_BASE}/orders", headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else data.get("orders", [])
        except httpx.HTTPError as exc:
            logger.error("Error obteniendo órdenes: %s", exc)
            return []

    def close(self) -> None:
        """Cierra el cliente HTTP."""
        self._http.close()

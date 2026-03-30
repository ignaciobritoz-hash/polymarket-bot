"""
risk_manager.py — Gestión de riesgo para el bot de trading.

Controla:
  - Tamaño máximo de posición (% del capital)
  - Pérdida máxima diaria
  - Umbral mínimo de edge para operar
  - Stop-loss por cambio de probabilidad
  - Tracking de operaciones
"""

import logging
import os
from datetime import date, datetime
from typing import Any

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class RiskManager:
    """Gestiona el riesgo y el sizing de posiciones."""

    def __init__(self, initial_capital: float | None = None) -> None:
        self.initial_capital = initial_capital or float(os.getenv("INITIAL_CAPITAL", "1000"))
        self.capital = self.initial_capital

        self.max_position_pct = float(os.getenv("MAX_POSITION_PCT", "0.05"))
        self.max_daily_loss_pct = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.10"))
        self.min_edge_pct = float(os.getenv("MIN_EDGE_PCT", "0.05"))

        self._trades: list[dict[str, Any]] = []
        self._daily_pnl: dict[str, float] = {}
        self._open_positions: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Validación de operaciones
    # ------------------------------------------------------------------

    def can_trade(self, analysis: dict[str, Any]) -> tuple[bool, str]:
        """
        Decide si se puede ejecutar un trade basándose en las reglas de riesgo.

        Returns:
            (allowed: bool, reason: str)
        """
        edge = abs(analysis.get("edge", 0))
        if edge < self.min_edge_pct:
            return False, f"Edge insuficiente: {edge:.2%} < {self.min_edge_pct:.2%}"

        if self._daily_loss_exceeded():
            return False, "Pérdida diaria máxima alcanzada"

        if analysis.get("signal", "PASS") == "PASS":
            return False, "Señal PASS — no hay oportunidad"

        return True, "OK"

    def calculate_position_size(self, kelly_size: float) -> float:
        """
        Calcula el tamaño real de la posición en USD.

        Args:
            kelly_size: Fracción del capital recomendada por Kelly (0-1).
        Returns:
            Tamaño de la posición en USD.
        """
        max_size = self.capital * self.max_position_pct
        kelly_amount = self.capital * kelly_size
        return min(kelly_amount, max_size)

    # ------------------------------------------------------------------
    # Tracking de operaciones
    # ------------------------------------------------------------------

    def record_trade(
        self,
        market_id: str,
        signal: str,
        price: float,
        size: float,
        analysis: dict[str, Any],
        dry_run: bool = True,
    ) -> None:
        """Registra un trade ejecutado."""
        trade: dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat(),
            "market_id": market_id,
            "signal": signal,
            "price": price,
            "size": size,
            "edge": analysis.get("edge", 0),
            "estimated_prob": analysis.get("estimated_prob", 0),
            "kelly_size": analysis.get("kelly_size", 0),
            "dry_run": dry_run,
            "status": "open",
        }
        self._trades.append(trade)
        self._open_positions[market_id] = trade

        if not dry_run:
            self.capital -= size

        today = date.today().isoformat()
        self._daily_pnl.setdefault(today, 0.0)
        logger.info(
            "Trade registrado — %s %s @ %.4f | size: $%.2f | edge: %.4f | dry_run: %s",
            signal,
            market_id,
            price,
            size,
            analysis.get("edge", 0),
            dry_run,
        )

    def close_position(self, market_id: str, exit_price: float) -> float:
        """
        Cierra una posición y actualiza el PnL.

        Returns:
            PnL realizado en USD.
        """
        pos = self._open_positions.pop(market_id, None)
        if not pos:
            logger.warning("No se encontró posición abierta para %s", market_id)
            return 0.0

        entry_price = pos["price"]
        size = pos["size"]
        signal = pos["signal"]

        if signal == "BUY YES":
            pnl = size * (exit_price - entry_price) / entry_price
        else:
            pnl = size * (entry_price - exit_price) / entry_price

        if not pos.get("dry_run"):
            self.capital += size + pnl

        today = date.today().isoformat()
        self._daily_pnl[today] = self._daily_pnl.get(today, 0.0) + pnl

        pos["status"] = "closed"
        pos["exit_price"] = exit_price
        pos["pnl"] = pnl

        logger.info("Posición cerrada — %s | PnL: $%.2f", market_id, pnl)
        return pnl

    def check_stop_loss(
        self,
        market_id: str,
        current_price: float,
        stop_loss_pct: float = 0.20,
    ) -> bool:
        """
        Verifica si una posición debe cerrarse por stop-loss.

        Args:
            market_id: ID del mercado.
            current_price: Precio actual (para YES).
            stop_loss_pct: Cambio porcentual que activa el stop-loss (default 20%).
        Returns:
            True si debe ejecutarse el stop-loss.
        """
        pos = self._open_positions.get(market_id)
        if not pos:
            return False

        entry_price = pos["price"]
        signal = pos["signal"]

        if signal == "BUY YES":
            change = (current_price - entry_price) / entry_price
            return change <= -stop_loss_pct
        elif signal == "BUY NO":
            change = (entry_price - current_price) / entry_price
            return change <= -stop_loss_pct
        return False

    # ------------------------------------------------------------------
    # Estado y estadísticas
    # ------------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Devuelve el estado actual del risk manager."""
        today = date.today().isoformat()
        daily_loss = self._daily_pnl.get(today, 0.0)
        total_pnl = sum(
            t.get("pnl", 0.0) for t in self._trades if t.get("status") == "closed"
        )
        return {
            "capital": round(self.capital, 2),
            "initial_capital": round(self.initial_capital, 2),
            "total_pnl": round(total_pnl, 2),
            "daily_pnl": round(daily_loss, 2),
            "open_positions": len(self._open_positions),
            "total_trades": len(self._trades),
            "max_position_usd": round(self.capital * self.max_position_pct, 2),
            "daily_loss_limit": round(self.initial_capital * self.max_daily_loss_pct, 2),
        }

    def get_open_positions(self) -> list[dict[str, Any]]:
        """Devuelve la lista de posiciones abiertas."""
        return list(self._open_positions.values())

    def get_trade_history(self) -> list[dict[str, Any]]:
        """Devuelve el historial de trades."""
        return self._trades.copy()

    # ------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------

    def _daily_loss_exceeded(self) -> bool:
        today = date.today().isoformat()
        daily_loss = self._daily_pnl.get(today, 0.0)
        limit = -self.initial_capital * self.max_daily_loss_pct
        return daily_loss <= limit

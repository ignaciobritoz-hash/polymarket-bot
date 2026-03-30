"""
bot.py — Bot principal automatizado de trading en Polymarket.

Características:
  - Loop continuo con intervalo configurable (CHECK_INTERVAL segundos)
  - Obtiene mercados reales de la Gamma API
  - Analiza con Claude (Anthropic) para detectar edge
  - Calcula Kelly Criterion para sizing óptimo
  - Ejecuta trades cuando edge > umbral
  - Gestión de riesgo integrada (stop-loss, max daily loss, max position)
  - Modo dry-run por defecto (no ejecuta trades reales)
  - Logging completo

Uso:
  # Modo simulado (por defecto)
  python bot.py

  # Modo live (trades reales)
  BOT_MODE=live python bot.py
"""

import logging
import os
import signal
import sys
import time
from datetime import datetime
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from analyzer import MarketAnalyzer
from polymarket_client import PolymarketClient
from risk_manager import RiskManager

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"logs/bot_{datetime.utcnow().strftime('%Y%m%d')}.log"),
    ],
)
logger = logging.getLogger("polybot")

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
BOT_MODE = os.getenv("BOT_MODE", "dry-run").lower()
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "1800"))
DRY_RUN = BOT_MODE != "live"
STOP_LOSS_PCT = 0.20  # 20% de cambio adverso activa stop-loss

# ---------------------------------------------------------------------------
# Estado global del bot
# ---------------------------------------------------------------------------
_running = False


def _handle_signal(signum: int, frame: Any) -> None:
    global _running
    logger.info("Señal %d recibida — deteniendo bot...", signum)
    _running = False


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ---------------------------------------------------------------------------
# Ciclo principal
# ---------------------------------------------------------------------------

def run_cycle(
    client: PolymarketClient,
    analyzer: MarketAnalyzer,
    risk: RiskManager,
) -> dict[str, Any]:
    """
    Ejecuta un ciclo completo del bot:
    1. Obtener mercados
    2. Analizar con Claude
    3. Verificar reglas de riesgo
    4. Ejecutar trades (si no es dry-run)
    5. Verificar stop-losses de posiciones abiertas

    Returns:
        Resumen del ciclo.
    """
    summary: dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat(),
        "markets_analyzed": 0,
        "trades_executed": 0,
        "trades_skipped": 0,
        "errors": 0,
    }

    # 1. Obtener mercados activos
    logger.info("Obteniendo mercados activos de Polymarket...")
    markets = client.get_markets(limit=20)
    if not markets:
        logger.warning("No se obtuvieron mercados")
        return summary

    logger.info("Analizando %d mercados...", len(markets))

    for market in markets:
        market_id = str(market.get("id", market.get("conditionId", "")))
        question = market.get("question", market.get("title", "Sin título"))

        # Obtener precio actual
        tokens = market.get("tokens", [])
        yes_token_id = None
        price_yes = None
        for token in tokens:
            if token.get("outcome", "").upper() == "YES":
                yes_token_id = token.get("token_id") or token.get("tokenId")
                price_yes = float(token.get("price", 0.5))
                break

        if price_yes is None:
            price_yes = float(market.get("outcomePrices", [0.5, 0.5])[0])

        # 2. Analizar con Claude
        try:
            analysis = analyzer.analyze_market(market, current_price_yes=price_yes)
            summary["markets_analyzed"] += 1
        except Exception as exc:
            logger.error("Error analizando mercado %s: %s", market_id, exc)
            summary["errors"] += 1
            continue

        signal_str = analysis.get("signal", "PASS")
        edge = analysis.get("edge", 0)
        confidence = analysis.get("confidence", "LOW")

        logger.info(
            "[%s] %s | Señal: %s | Edge: %.2f%% | Confianza: %s",
            market_id[:8],
            question[:60],
            signal_str,
            edge * 100,
            confidence,
        )

        # 3. Verificar reglas de riesgo
        allowed, reason = risk.can_trade(analysis)
        if not allowed:
            logger.debug("Trade rechazado (%s): %s", market_id[:8], reason)
            summary["trades_skipped"] += 1
            continue

        # Calcular tamaño de posición
        position_size = risk.calculate_position_size(analysis.get("kelly_size", 0))
        if position_size < 1.0:
            logger.debug("Posición muy pequeña ($%.2f) — saltando", position_size)
            summary["trades_skipped"] += 1
            continue

        # 4. Ejecutar trade
        trade_price = price_yes if signal_str == "BUY YES" else (1 - price_yes)

        if not DRY_RUN and yes_token_id:
            result = client.place_order(
                token_id=yes_token_id,
                side="BUY" if signal_str == "BUY YES" else "SELL",
                price=trade_price,
                size=position_size,
            )
            if result:
                logger.info(
                    "✅ ORDEN EJECUTADA — %s %s @ %.4f | $%.2f",
                    signal_str,
                    question[:40],
                    trade_price,
                    position_size,
                )
                risk.record_trade(
                    market_id=market_id,
                    signal=signal_str,
                    price=trade_price,
                    size=position_size,
                    analysis=analysis,
                    dry_run=False,
                )
                summary["trades_executed"] += 1
            else:
                logger.error("Error ejecutando orden para %s", market_id)
                summary["errors"] += 1
        else:
            logger.info(
                "🔵 [DRY-RUN] %s %s @ %.4f | $%.2f | edge: %.2f%%",
                signal_str,
                question[:40],
                trade_price,
                position_size,
                edge * 100,
            )
            risk.record_trade(
                market_id=market_id,
                signal=signal_str,
                price=trade_price,
                size=position_size,
                analysis=analysis,
                dry_run=True,
            )
            summary["trades_executed"] += 1

    # 5. Verificar stop-losses de posiciones abiertas
    open_positions = risk.get_open_positions()
    for pos in open_positions:
        pos_market_id = pos["market_id"]
        current_price = _get_current_price(client, markets, pos_market_id)
        if current_price is None:
            continue
        if risk.check_stop_loss(pos_market_id, current_price, STOP_LOSS_PCT):
            logger.warning(
                "⚠️  STOP-LOSS activado para %s @ %.4f", pos_market_id[:8], current_price
            )
            if not DRY_RUN:
                # Colocar orden de cierre
                pass  # TODO: implementar cierre de posición via CLOB
            pnl = risk.close_position(pos_market_id, current_price)
            logger.info("Posición cerrada por stop-loss | PnL: $%.2f", pnl)

    return summary


def _get_current_price(
    client: PolymarketClient,
    markets: list[dict],
    market_id: str,
) -> float | None:
    """Obtiene el precio actual de YES para un mercado."""
    for m in markets:
        mid = str(m.get("id", m.get("conditionId", "")))
        if mid == market_id:
            tokens = m.get("tokens", [])
            for t in tokens:
                if t.get("outcome", "").upper() == "YES":
                    return float(t.get("price", 0.5))
            return float(m.get("outcomePrices", [0.5])[0])
    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    global _running

    mode_label = "🟡 DRY-RUN (simulado)" if DRY_RUN else "🔴 LIVE (trades reales)"
    logger.info("=" * 60)
    logger.info("🤖 PolyBot arrancando en modo %s", mode_label)
    logger.info("Intervalo entre ciclos: %d segundos", CHECK_INTERVAL)
    logger.info("=" * 60)

    if not DRY_RUN:
        logger.warning("⚠️  MODO LIVE ACTIVADO — Se ejecutarán trades reales con dinero real")
        logger.warning("Asegurate de haber probado en modo dry-run primero")

    client = PolymarketClient()
    analyzer = MarketAnalyzer()
    risk = RiskManager()

    _running = True
    cycle = 0

    try:
        while _running:
            cycle += 1
            logger.info("--- Ciclo #%d ---", cycle)
            try:
                summary = run_cycle(client, analyzer, risk)
                status = risk.get_status()
                logger.info(
                    "Ciclo #%d completado — Analizados: %d | Trades: %d | Capital: $%.2f",
                    cycle,
                    summary["markets_analyzed"],
                    summary["trades_executed"],
                    status["capital"],
                )
            except Exception as exc:
                logger.error("Error en ciclo #%d: %s", cycle, exc, exc_info=True)

            if _running:
                logger.info("Esperando %d segundos hasta el próximo ciclo...", CHECK_INTERVAL)
                # Sleep interruptible
                for _ in range(CHECK_INTERVAL):
                    if not _running:
                        break
                    time.sleep(1)
    finally:
        client.close()
        final_status = risk.get_status()
        logger.info("=" * 60)
        logger.info("Bot detenido. Estado final:")
        logger.info("  Capital: $%.2f", final_status["capital"])
        logger.info("  PnL Total: $%.2f", final_status["total_pnl"])
        logger.info("  Trades ejecutados: %d", final_status["total_trades"])
        logger.info("=" * 60)


if __name__ == "__main__":
    main()

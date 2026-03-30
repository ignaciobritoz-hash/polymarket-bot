"""
server.py — API backend (FastAPI) para la UI web del bot de Polymarket.

Endpoints:
  GET  /api/markets          — lista mercados reales de Polymarket
  GET  /api/market/{id}      — detalle de un mercado con order book
  POST /api/analyze          — envía mercado a Claude para análisis
  POST /api/trade            — ejecuta un trade manual
  GET  /api/portfolio        — estado del portfolio
  GET  /api/positions        — posiciones abiertas
  GET  /api/bot/status       — estado del bot (corriendo/pausado)
  POST /api/bot/start        — inicia bot automático en background
  POST /api/bot/stop         — detiene el bot

  GET  /                     — sirve static/index.html

Uso:
  python server.py
  o
  uvicorn server:app --host 0.0.0.0 --port 8000 --reload
"""

import logging
import os
import threading
import time
from datetime import datetime
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

from analyzer import MarketAnalyzer
from polymarket_client import PolymarketClient
from risk_manager import RiskManager

logger = logging.getLogger("polybot.server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------
client = PolymarketClient()
risk = RiskManager()

try:
    analyzer = MarketAnalyzer()
    _analyzer_ready = True
except ValueError as exc:
    logger.warning("Analyzer no disponible: %s", exc)
    analyzer = None
    _analyzer_ready = False

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="PolyBot API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files if the directory exists
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

# ---------------------------------------------------------------------------
# Bot background thread state
# ---------------------------------------------------------------------------
_bot_thread: threading.Thread | None = None
_bot_running = False
_bot_lock = threading.Lock()
_bot_stats: dict[str, Any] = {
    "cycles": 0,
    "trades": 0,
    "started_at": None,
    "last_cycle_at": None,
}

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "1800"))
BOT_MODE = os.getenv("BOT_MODE", "dry-run").lower()
DRY_RUN = BOT_MODE != "live"


def _bot_loop() -> None:
    """Función que corre en el hilo del bot."""
    global _bot_running
    from bot import run_cycle

    while _bot_running:
        try:
            summary = run_cycle(client, analyzer, risk)
            with _bot_lock:
                _bot_stats["cycles"] += 1
                _bot_stats["trades"] += summary.get("trades_executed", 0)
                _bot_stats["last_cycle_at"] = datetime.utcnow().isoformat()
        except Exception as exc:
            logger.error("Error en ciclo del bot: %s", exc)

        # Espera interruptible
        for _ in range(CHECK_INTERVAL):
            if not _bot_running:
                break
            time.sleep(1)


# ---------------------------------------------------------------------------
# Modelos Pydantic
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    market_id: str
    question: str | None = None
    description: str | None = None
    price_yes: float | None = None
    price_no: float | None = None


class TradeRequest(BaseModel):
    market_id: str
    token_id: str
    signal: str  # BUY YES | BUY NO
    price: float
    size: float


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
async def root() -> FileResponse:
    """Sirve el index.html del frontend."""
    index_path = os.path.join(_static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    raise HTTPException(status_code=404, detail="Frontend no encontrado. Revisar directorio static/")


@app.get("/api/markets")
async def get_markets(limit: int = 20, offset: int = 0) -> list[dict]:
    """Lista mercados activos de Polymarket."""
    markets = client.get_markets(limit=limit, offset=offset)
    return markets


@app.get("/api/market/{market_id}")
async def get_market(market_id: str) -> dict:
    """Detalle de un mercado con order book."""
    market = client.get_market(market_id)
    if not market:
        raise HTTPException(status_code=404, detail="Mercado no encontrado")

    # Agregar order book si hay tokens
    tokens = market.get("tokens", [])
    for token in tokens:
        token_id = token.get("token_id") or token.get("tokenId")
        if token_id:
            book = client.get_orderbook(token_id)
            if book:
                token["orderbook"] = book

    return market


@app.post("/api/analyze")
async def analyze_market(req: AnalyzeRequest) -> dict:
    """Envía un mercado a Claude para análisis."""
    if not _analyzer_ready or analyzer is None:
        raise HTTPException(
            status_code=503,
            detail="Analyzer no disponible. Configurar ANTHROPIC_API_KEY en .env",
        )

    # Construir objeto mercado desde el request o buscar en API
    market: dict[str, Any] = {}
    if req.question:
        market = {
            "id": req.market_id,
            "question": req.question,
            "description": req.description or "",
        }
    else:
        fetched = client.get_market(req.market_id)
        if not fetched:
            raise HTTPException(status_code=404, detail="Mercado no encontrado")
        market = fetched

    analysis = analyzer.analyze_market(
        market,
        current_price_yes=req.price_yes,
        current_price_no=req.price_no,
    )
    return analysis


@app.post("/api/trade")
async def execute_trade(req: TradeRequest) -> dict:
    """Ejecuta un trade manual."""
    analysis = {
        "signal": req.signal,
        "edge": abs(req.price - 0.5),
        "estimated_prob": req.price,
        "market_price": req.price,
        "kelly_size": 0.02,
    }

    allowed, reason = risk.can_trade(analysis)
    if not allowed:
        raise HTTPException(status_code=400, detail=f"Trade rechazado: {reason}")

    if DRY_RUN:
        risk.record_trade(
            market_id=req.market_id,
            signal=req.signal,
            price=req.price,
            size=req.size,
            analysis=analysis,
            dry_run=True,
        )
        return {"status": "dry_run", "message": "Trade simulado registrado", "size": req.size}

    result = client.place_order(
        token_id=req.token_id,
        side="BUY" if req.signal == "BUY YES" else "SELL",
        price=req.price,
        size=req.size,
    )
    if not result:
        raise HTTPException(status_code=500, detail="Error ejecutando orden en Polymarket")

    risk.record_trade(
        market_id=req.market_id,
        signal=req.signal,
        price=req.price,
        size=req.size,
        analysis=analysis,
        dry_run=False,
    )
    return {"status": "executed", "order": result}


@app.get("/api/portfolio")
async def get_portfolio() -> dict:
    """Estado del portfolio."""
    return risk.get_status()


@app.get("/api/positions")
async def get_positions() -> list:
    """Posiciones abiertas."""
    return risk.get_open_positions()


@app.get("/api/trades")
async def get_trades() -> list:
    """Historial de trades."""
    return risk.get_trade_history()


@app.get("/api/bot/status")
async def bot_status() -> dict:
    """Estado del bot automático."""
    with _bot_lock:
        return {
            "running": _bot_running,
            "dry_run": DRY_RUN,
            "mode": BOT_MODE,
            "check_interval": CHECK_INTERVAL,
            **_bot_stats,
        }


@app.post("/api/bot/start")
async def bot_start() -> dict:
    """Inicia el bot automático en background."""
    global _bot_thread, _bot_running

    if not _analyzer_ready or analyzer is None:
        raise HTTPException(
            status_code=503,
            detail="Analyzer no disponible. Configurar ANTHROPIC_API_KEY en .env",
        )

    with _bot_lock:
        if _bot_running:
            return {"status": "already_running"}

        _bot_running = True
        _bot_stats["started_at"] = datetime.utcnow().isoformat()
        _bot_stats["cycles"] = 0
        _bot_stats["trades"] = 0

    _bot_thread = threading.Thread(target=_bot_loop, daemon=True, name="polybot")
    _bot_thread.start()
    logger.info("Bot iniciado (dry_run=%s)", DRY_RUN)
    return {"status": "started", "dry_run": DRY_RUN}


@app.post("/api/bot/stop")
async def bot_stop() -> dict:
    """Detiene el bot automático."""
    global _bot_running

    with _bot_lock:
        if not _bot_running:
            return {"status": "not_running"}
        _bot_running = False

    logger.info("Bot detenido")
    return {"status": "stopped"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    host = os.getenv("SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("SERVER_PORT", "8000"))
    uvicorn.run("server:app", host=host, port=port, reload=False)

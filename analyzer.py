"""
analyzer.py — Motor de análisis de mercados con Claude (Anthropic) + Kelly Criterion.
"""

import json
import logging
import os
import re
from typing import Any

import anthropic
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

ANTHROPIC_MODEL = "claude-sonnet-4-20250514"


class MarketAnalyzer:
    """Analiza mercados de predicción con Claude y calcula Kelly Criterion."""

    def __init__(self) -> None:
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY no está configurada en el .env")
        self.client = anthropic.Anthropic(api_key=api_key)
        self.kelly_fraction = float(os.getenv("KELLY_FRACTION", "0.5"))

    # ------------------------------------------------------------------
    # Análisis con Claude
    # ------------------------------------------------------------------

    def analyze_market(
        self,
        market: dict[str, Any],
        current_price_yes: float | None = None,
        current_price_no: float | None = None,
    ) -> dict[str, Any]:
        """
        Envía el contexto del mercado a Claude y devuelve un análisis estructurado.

        Retorna:
            {
                "signal": "BUY YES" | "BUY NO" | "PASS",
                "estimated_prob": float,          # probabilidad estimada (0-1)
                "market_price": float,            # precio actual del mercado
                "edge": float,                    # edge = estimated_prob - market_price
                "kelly_size": float,              # fracción del capital (half-Kelly)
                "factors": list[str],             # factores clave
                "reasoning": str,                 # razonamiento de Claude
                "confidence": str,                # "HIGH" | "MEDIUM" | "LOW"
            }
        """
        prompt = self._build_prompt(market, current_price_yes, current_price_no)
        try:
            message = self.client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text
            return self._parse_response(raw, current_price_yes or 0.5)
        except anthropic.APIError as exc:
            logger.error("Error llamando a Anthropic API: %s", exc)
            return self._empty_analysis()

    # ------------------------------------------------------------------
    # Prompt engineering
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        market: dict[str, Any],
        price_yes: float | None,
        price_no: float | None,
    ) -> str:
        question = market.get("question", market.get("title", "Mercado desconocido"))
        description = market.get("description", "Sin descripción disponible.")
        end_date = market.get("endDate", market.get("end_date_iso", "Desconocida"))
        category = market.get("category", "General")

        price_str = ""
        if price_yes is not None:
            price_str = f"Precio actual YES: {price_yes:.2%} | Precio actual NO: {(1 - price_yes):.2%}"
        elif price_no is not None:
            price_str = f"Precio actual NO: {price_no:.2%} | Precio actual YES: {(1 - price_no):.2%}"

        return f"""Eres un analista experto en mercados de predicción (prediction markets).
Analiza el siguiente mercado de Polymarket y devuelve tu análisis en el formato JSON especificado.

## Mercado
Pregunta: {question}
Categoría: {category}
Fecha de cierre: {end_date}
{price_str}
Descripción: {description}

## Tu tarea
1. Estima la probabilidad real de que el evento ocurra (resultado YES) basándote en tu conocimiento.
2. Compara tu estimación con el precio de mercado para calcular el edge.
3. Decide si hay una oportunidad de trading (edge > 5%).
4. Calcula el Kelly Criterion para el sizing.

## Formato de respuesta (JSON estricto, sin markdown)
{{
  "estimated_prob": <float entre 0 y 1>,
  "signal": "<BUY YES|BUY NO|PASS>",
  "edge": <float, diferencia entre tu estimación y el precio de mercado>,
  "factors": [<lista de 2-4 factores clave que influyen en el resultado>],
  "reasoning": "<explicación concisa de 2-3 oraciones>",
  "confidence": "<HIGH|MEDIUM|LOW>"
}}

Responde SOLO con el JSON, sin texto adicional."""

    # ------------------------------------------------------------------
    # Parseo de respuesta
    # ------------------------------------------------------------------

    def _parse_response(self, raw: str, market_price: float) -> dict[str, Any]:
        """Parsea la respuesta JSON de Claude."""
        raw = raw.strip()
        # Extraer bloque JSON si viene envuelto en markdown
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            logger.warning("No se encontró JSON en la respuesta de Claude: %s", raw[:200])
            return self._empty_analysis()
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError as exc:
            logger.error("Error parseando JSON de Claude: %s", exc)
            return self._empty_analysis()

        estimated_prob = float(data.get("estimated_prob", 0.5))
        signal = data.get("signal", "PASS").upper()
        edge = float(data.get("edge", estimated_prob - market_price))
        kelly = self._kelly(estimated_prob, market_price, signal)

        return {
            "signal": signal,
            "estimated_prob": round(estimated_prob, 4),
            "market_price": round(market_price, 4),
            "edge": round(edge, 4),
            "kelly_size": round(kelly, 4),
            "factors": data.get("factors", []),
            "reasoning": data.get("reasoning", ""),
            "confidence": data.get("confidence", "LOW"),
        }

    # ------------------------------------------------------------------
    # Kelly Criterion
    # ------------------------------------------------------------------

    def _kelly(self, prob: float, price: float, signal: str) -> float:
        """
        Calcula el Kelly Criterion (half-Kelly por defecto).

        Fórmula: kelly = (p * b - q) / b
        donde b = (1 - price) / price  (odds en favor)
              p = probabilidad estimada
              q = 1 - p
        """
        if signal == "PASS" or price <= 0 or price >= 1:
            return 0.0
        if signal == "BUY NO":
            # Invertimos para operar en NO
            prob = 1 - prob
            price = 1 - price
        if price <= 0:
            return 0.0
        b = (1 - price) / price
        q = 1 - prob
        kelly = (prob * b - q) / b
        kelly = max(0.0, kelly)
        return kelly * self.kelly_fraction

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_analysis() -> dict[str, Any]:
        return {
            "signal": "PASS",
            "estimated_prob": 0.5,
            "market_price": 0.5,
            "edge": 0.0,
            "kelly_size": 0.0,
            "factors": [],
            "reasoning": "No se pudo obtener análisis de IA.",
            "confidence": "LOW",
        }

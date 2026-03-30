# 🤖 PolyBot — Bot Automatizado de Trading en Polymarket

Bot de trading automatizado que conecta con la API real de Polymarket (CLOB), analiza mercados con **Claude** (Anthropic), y ejecuta trades automáticamente basados en Kelly Criterion y gestión de riesgo.

> ⚠️ **ADVERTENCIA DE RIESGO FINANCIERO**: Este bot opera con dinero real en mercados de predicción. El trading automatizado conlleva riesgo significativo de pérdida de capital. Usá este bot únicamente con dinero que puedas permitirte perder. El rendimiento pasado no garantiza resultados futuros. Por defecto, el bot corre en **modo dry-run (simulado)** y no ejecuta trades reales hasta que lo configures explícitamente.

---

## ¿Qué hace?

- 🔗 **Conecta a Polymarket en tiempo real** via Gamma API (mercados) y CLOB API (trading)
- 🧠 **Analiza mercados con Claude** (Anthropic) para estimar probabilidades reales y detectar edge
- 📊 **Calcula Kelly Criterion** para sizing óptimo de posiciones (half-Kelly por defecto)
- ⚡ **Ejecuta trades automáticamente** cuando detecta edge significativo (>5% por defecto)
- 🛡️ **Gestión de riesgo integrada**: max position size, max daily loss, stop-loss por cambio de probabilidad
- 🌐 **UI web profesional** con dark theme, gauges animados, y panel de trading manual
- 📝 **Logging completo** de todas las operaciones en `logs/`

---

## Arquitectura

```
polymarket-bot/
├── bot.py                # Bot automático (loop principal)
├── server.py             # API backend (FastAPI) + serve UI
├── polymarket_client.py  # Wrapper API Polymarket (Gamma + CLOB)
├── analyzer.py           # Análisis con Claude + Kelly Criterion
├── risk_manager.py       # Gestión de riesgo y posiciones
├── static/
│   └── index.html        # UI web (dark theme)
├── .env.example          # Template de configuración
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Requisitos

- **Python 3.10+**
- Cuenta en **Polymarket** con API keys (CLOB API)
- API key de **Anthropic** (Claude) — [console.anthropic.com](https://console.anthropic.com)

---

## Instalación paso a paso

### 1. Clonar el repositorio

```bash
git clone https://github.com/ignaciobritoz-hash/polymarket-bot.git
cd polymarket-bot
```

### 2. Crear entorno virtual

```bash
python -m venv venv
source venv/bin/activate        # Linux/macOS
# venv\Scripts\activate         # Windows
```

### 3. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 4. Configurar variables de entorno

```bash
cp .env.example .env
```

Editá `.env` con tus credenciales:

```env
# Polymarket API (obtené las keys en Polymarket > Settings > API)
POLYMARKET_API_KEY=tu_api_key
POLYMARKET_SECRET=tu_secret
POLYMARKET_PASSPHRASE=tu_passphrase

# Anthropic API (obtené la key en console.anthropic.com)
ANTHROPIC_API_KEY=tu_anthropic_key

# Configuración del bot
BOT_MODE=dry-run          # dry-run = simulado | live = trading real
CHECK_INTERVAL=1800       # Segundos entre ciclos (1800 = 30 min)
MAX_POSITION_PCT=0.05     # 5% máximo por posición
MAX_DAILY_LOSS_PCT=0.10   # 10% pérdida máxima diaria
MIN_EDGE_PCT=0.05         # Edge mínimo para operar (5%)
INITIAL_CAPITAL=1000      # Capital inicial en USD
KELLY_FRACTION=0.5        # Half-Kelly (recomendado)

# Servidor web
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
```

> 🔐 **Seguridad**: El archivo `.env` está en `.gitignore` y nunca se sube al repositorio. Nunca compartas tus API keys.

---

## Cómo ejecutar

### Modo dry-run (simulado) — recomendado para empezar

```bash
# Opción 1: Solo el bot automático (sin UI)
python bot.py

# Opción 2: Bot + UI web (recomendado)
python server.py
```

Con `BOT_MODE=dry-run` en `.env`, el bot analiza mercados y simula trades **sin ejecutar operaciones reales**.

### Modo live (trades reales)

```bash
# Activar modo live en .env:
# BOT_MODE=live

python server.py
```

> ⚠️ **Solo activá el modo live** cuando hayas probado extensamente en dry-run y entiendas los riesgos.

### Acceder a la UI web

Una vez ejecutando `server.py`, abrí el navegador en:

```
http://localhost:8000
```

La UI incluye:
- **Panel izquierdo**: Mercados en tiempo real con precios YES/NO
- **Panel central**: Análisis de Claude con gauge animado de probabilidad, edge, y factores clave
- **Panel derecho**: Control del bot (start/stop), portfolio, y trading manual

---

## Variables de entorno detalladas

| Variable | Default | Descripción |
|---|---|---|
| `BOT_MODE` | `dry-run` | `dry-run` = simulado, `live` = real |
| `CHECK_INTERVAL` | `1800` | Segundos entre ciclos del bot |
| `MAX_POSITION_PCT` | `0.05` | % máximo del capital por posición |
| `MAX_DAILY_LOSS_PCT` | `0.10` | % máximo de pérdida diaria permitida |
| `MIN_EDGE_PCT` | `0.05` | Edge mínimo para ejecutar un trade |
| `INITIAL_CAPITAL` | `1000` | Capital inicial en USD |
| `KELLY_FRACTION` | `0.5` | Multiplicador Kelly (0.5 = half-Kelly) |
| `SERVER_HOST` | `0.0.0.0` | Host del servidor FastAPI |
| `SERVER_PORT` | `8000` | Puerto del servidor FastAPI |

---

## Estructura del proyecto

| Archivo | Descripción |
|---|---|
| `bot.py` | Loop principal: obtiene mercados → analiza → ejecuta trades |
| `server.py` | API FastAPI con 10 endpoints + serve de la UI |
| `polymarket_client.py` | Autenticación HMAC-SHA256, Gamma API y CLOB API |
| `analyzer.py` | Prompt engineering para Claude + Kelly Criterion |
| `risk_manager.py` | Position sizing, stop-loss, tracking de PnL |
| `static/index.html` | Frontend SPA con dark theme |

---

## API Endpoints

| Método | Endpoint | Descripción |
|---|---|---|
| GET | `/api/markets` | Lista mercados activos |
| GET | `/api/market/{id}` | Detalle de mercado con order book |
| POST | `/api/analyze` | Análisis con Claude |
| POST | `/api/trade` | Ejecutar trade manual |
| GET | `/api/portfolio` | Estado del portfolio |
| GET | `/api/positions` | Posiciones abiertas |
| GET | `/api/trades` | Historial de trades |
| GET | `/api/bot/status` | Estado del bot |
| POST | `/api/bot/start` | Iniciar bot automático |
| POST | `/api/bot/stop` | Detener bot |

---

## ⚠️ Advertencias importantes

1. **Riesgo financiero real**: En modo `live`, el bot ejecuta órdenes reales con dinero real. Podés perder todo tu capital.

2. **Probá siempre en dry-run primero**: Entendé el comportamiento del bot antes de activar el modo live.

3. **Las APIs pueden cambiar**: Polymarket puede modificar su API. Verificá la documentación oficial si algo no funciona.

4. **No garantía de ganancias**: Los mercados de predicción son eficientes. Encontrar edge consistente es extremadamente difícil.

5. **Protegé tus API keys**: Nunca las compartas, nunca las subas a GitHub. Están en `.env` que está en `.gitignore`.

6. **Límites de rate**: Respetá los límites de las APIs para no ser bloqueado.

---

## Licencia

MIT — usá este código bajo tu propio riesgo.
# TELERÍN v2 — Asistente de TeleRadio con Agentes LLM

Chatbot multi-agente para consulta del archivo histórico de la revista **TeleRadio** (RTVE, 1957–1991). Permite buscar programación de TV y radio, publicidad, artículos editoriales e imágenes mediante lenguaje natural, con memoria conversacional y soporte para múltiples backends LLM.

---

## Características principales

- **Búsqueda híbrida** BM25 + semántica vectorial sobre CrateDB
- **Reranking** mediante VIVOClient (servicio externo)
- **Memoria contextual** con detección de follow-up y mejora de query en una sola llamada LLM
- **Múltiples backends LLM**: Ollama (local/remoto) y Google Gemini
- **Streaming** de respuestas vía WebSocket
- **Búsqueda de imágenes** con embeddings CLIP y descripción multimodal
- **Autenticación JWT** con gestión de usuarios
- **Frontend Next.js 14** con panel de contexto, debug y lightbox de imágenes

---

## Requisitos

| Componente | Versión mínima |
|---|---|
| Python | 3.11+ |
| Node.js | 18+ |
| CrateDB | 5.x |
| Ollama *(opcional)* | cualquiera |
| Docker + Compose *(opcional)* | 24+ |

---

## Instalación local (sin Docker)

```bash
# 1. Clonar
git clone https://github.com/Elleida/telerin.v2.git
cd telerin.v2

# 2. Entorno Python
python -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt

# 3. Variables de entorno
cp backend/.env.example backend/.env
# Editar backend/.env con tus valores

# 4. Dependencias frontend
cd frontend && npm install && cd ..

# 5. Arrancar todo
./start.sh
```

El script `start.sh` libera los puertos 8000 y 3000, activa el venv y lanza backend y frontend en paralelo.

---

## Instalación con Docker

```bash
cp backend/.env.example backend/.env
# Editar backend/.env

docker compose up --build
```

- Backend: `http://localhost:8000`
- Frontend: `http://localhost:3000`
- Docs API: `http://localhost:8000/docs`

---

## Configuración (`backend/.env`)

| Variable | Descripción | Ejemplo |
|---|---|---|
| `CRATEDB_URL` | Endpoint SQL de CrateDB | `http://localhost:4200/_sql` |
| `CRATEDB_USERNAME` | Usuario CrateDB | `crate` |
| `CRATEDB_PASSWORD` | Contraseña CrateDB | |
| `COLLECTION_NAME` | Tabla principal | `teleradio_content` |
| `OLLAMA_BASE_URL` | URL del servidor Ollama | `http://host:11434` |
| `OLLAMA_LLM_MODEL` | Modelo de texto Ollama | `glm-4.7-flash` |
| `OLLAMA_EMBEDDING_MODEL` | Modelo de embeddings | `qwen3-embedding:0.6b` |
| `GEMINI_API_KEY` | Clave Google AI Studio | *(requerida para Gemini)* |
| `GEMINI_MODEL` | Modelo Gemini | `gemini-3-flash-preview` |
| `AI_SERVER_URL` | Servicio VIVOClient (reranker) | `http://host:5001` |
| `PNG_BASE_DIR` | Directorio local de imágenes | `/path/to/images` |
| `PNG_BASE_URL` | URL pública de imágenes | `http://host:8000/images` |
| `JWT_SECRET_KEY` | Clave firma JWT | *(cadena aleatoria ≥32 chars)* |
| `CORS_ORIGINS` | Orígenes permitidos (CSV) | `http://localhost:3000` |
| `MEMORY_CONTEXT_WINDOW` | Turnos de contexto (defecto: 5) | `5` |
| `MEMORY_MAX_HISTORY` | Máximo turnos en memoria (defecto: 100) | `100` |
| `DISABLE_STREAMING` | Desactivar streaming (0/1) | `0` |

---

## Gestión de usuarios

```bash
# Crear usuario
python scripts/manage_users.py --create --username alice --password secret

# Listar usuarios
python scripts/manage_users.py --list

# Eliminar usuario
python scripts/delete_user.py --username alice
```

---

## Estructura del proyecto

```
telerin.v2/
├── backend/
│   ├── main.py              # FastAPI app
│   ├── config.py            # Configuración centralizada
│   ├── compat/              # Núcleo del sistema multi-agente
│   │   ├── graph.py         # Grafo LangGraph (orquestación)
│   │   ├── tools.py         # Búsqueda híbrida, reranking, LLM calls
│   │   ├── memory.py        # Memoria conversacional
│   │   ├── llm_context_analyzer.py  # Análisis follow-up + mejora de query
│   │   ├── query_enhancer.py
│   │   ├── context_extractor.py
│   │   └── image_search.py
│   ├── routers/             # Endpoints FastAPI
│   │   ├── chat.py          # WebSocket + REST chat
│   │   ├── auth.py
│   │   ├── session.py
│   │   └── image.py
│   ├── services/
│   ├── models/
│   └── requirements.txt
├── frontend/                # Next.js 14
│   └── src/
│       ├── app/
│       ├── components/
│       ├── hooks/
│       └── lib/
├── scripts/                 # Administración de usuarios
├── docker-compose.yml
├── start.sh
└── work/                    # Prototipos y utilidades de desarrollo
```

---

## Selección de backend LLM

Desde la interfaz (panel lateral) se puede alternar entre:

| Backend | Modelo por defecto | Uso |
|---|---|---|
| **Ollama** | `glm-4.7-flash` | Local / servidor privado |
| **Gemini** | `gemini-3-flash-preview` | Google AI Studio (requiere API key) |

El backend seleccionado se aplica a **todas** las llamadas internas: clasificación de intent, análisis de follow-up, mejora de query, agente de búsqueda y generación de respuesta.

---

## Licencia

Proyecto interno RTVE / Universidad de Zaragoza — PoC 70 años TVE.

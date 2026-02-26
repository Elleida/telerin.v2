# TELERÍN v2 — Guía de Instalación

## Prerrequisitos

| Componente | Versión mínima | Notas |
|---|---|---|
| Python | 3.11+ | Se recomienda usar un virtualenv |
| Node.js | 18+ | Incluye npm |
| CrateDB | 5.x | Debe estar en marcha antes de arrancar |
| Ollama | cualquiera | Opcional; necesario si no se usa Gemini |
| Docker + Compose | 24+ | Solo para instalación con Docker |

---

## 1. Obtener el código

```bash
git clone https://github.com/Elleida/telerin.v2.git
cd telerin.v2
```

---

## 2. Configurar variables de entorno

### 2.1 Backend (`backend/.env`)

```bash
cp backend/.env.example backend/.env
```

Editar `backend/.env` con los valores del entorno:

```dotenv
# ── CrateDB ────────────────────────────────────────────────────────────────
CRATEDB_URL=http://localhost:4200/_sql
CRATEDB_USERNAME=crate
CRATEDB_PASSWORD=
COLLECTION_NAME=teleradio_content

# ── Ollama ─────────────────────────────────────────────────────────────────
OLLAMA_BASE_URL=http://YOUR_OLLAMA_HOST:11434
OLLAMA_LLM_MODEL=glm-4.7-flash
OLLAMA_EMBEDDING_MODEL=qwen3-embedding:0.6b

# ── Gemini (alternativa a Ollama) ──────────────────────────────────────────
GEMINI_MODEL=gemini-3-flash-preview
GEMINI_API_KEY=YOUR_GOOGLE_AI_STUDIO_API_KEY

# ── Imágenes PNG ───────────────────────────────────────────────────────────
PNG_BASE_DIR=/ruta/absoluta/a/pngprocessed
PNG_BASE_URL=/teleradio/images          # ← IMPORTANTE: ruta relativa, no http://

# ── Reranker (VIVOClient) ──────────────────────────────────────────────────
AI_SERVER_URL=http://YOUR_RERANKER_HOST:5001
RERANKER_MODEL=qwen3rerank

# ── Auth ───────────────────────────────────────────────────────────────────
JWT_SECRET_KEY=CAMBIA_ESTO_POR_CADENA_ALEATORIA_32+_CHARS

# ── CORS (orígenes del frontend, separados por coma) ──────────────────────
CORS_ORIGINS=http://localhost:8502,https://TU_DOMINIO

# ── Feedback log ───────────────────────────────────────────────────────────
FEEDBACK_LOG_PATH=/ruta/a/logs/feedback.log

# ── Misc ───────────────────────────────────────────────────────────────────
DISABLE_STREAMING=0
MEMORY_CONTEXT_WINDOW=5
MEMORY_MAX_HISTORY=100
```

> **`PNG_BASE_URL` debe ser una ruta relativa** como `/teleradio/images`, no una URL absoluta con puerto.
> De lo contrario, las imágenes no funcionarán tras un reverse proxy (mixed-content HTTPS→HTTP, puerto no accesible externamente).

Para generar un `JWT_SECRET_KEY` aleatorio:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 2.2 Frontend (`frontend/.env.local`)

```bash
cat > frontend/.env.local << 'EOF'
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_WS_URL=ws://localhost:8000
NEXT_PUBLIC_BASE_PATH=/teleradio
NEXT_PUBLIC_DEFAULT_LLM_MODEL=glm-4.7-flash
NEXT_PUBLIC_DEFAULT_GEMINI_MODEL=gemini-3-flash-preview
EOF
```

---

## 3. Instalación local (sin Docker)

### 3.1 Backend Python

```bash
# Crear y activar entorno virtual
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# Instalar dependencias
pip install -r backend/requirements.txt
```

### 3.2 Frontend Node.js

```bash
cd frontend
npm install
cd ..
```

### 3.3 Arrancar

```bash
./start.sh
```

El script `start.sh` realiza automáticamente:
- Libera el puerto 8000 si está ocupado
- Libera el puerto 8502 si está ocupado
- Lanza uvicorn (backend FastAPI) en `:8000`
- Lanza Next.js (frontend) en `:8502`

También se puede arrancar cada servicio por separado:

```bash
./start.sh backend    # solo backend
./start.sh frontend   # solo frontend
```

### 3.4 Verificar

| Servicio | URL |
|---|---|
| Frontend | `http://localhost:8502/teleradio` |
| Backend API docs | `http://localhost:8000/docs` |
| Backend health | `http://localhost:8000/api/auth/me` |

---

## 4. Instalación con Docker

```bash
# Asegurarse de tener el .env configurado primero (paso 2)
docker compose up --build
```

| Servicio | Puerto |
|---|---|
| Frontend | `8502` → `http://localhost:8502/teleradio` |
| Backend | `8000` (solo acceso interno desde el frontend) |

---

## 5. Crear el primer usuario administrador

Antes de poder acceder hay que crear al menos un usuario:

```bash
# Con el venv activo y desde la raíz del proyecto:
python scripts/manage_users.py --create \
    --username admin \
    --password TU_CONTRASEÑA \
    --role admin

# Listar usuarios existentes
python scripts/manage_users.py --list

# Eliminar usuario
python scripts/delete_user.py --username nombre
```

También se pueden gestionar usuarios desde la interfaz web en `/teleradio/admin` una vez autenticado como administrador.

---

## 6. Despliegue con nginx (reverse proxy)

Si la aplicación se sirve detrás de un reverse proxy (p.ej. `https://dihana.unizar.es/teleradio`), añadir el siguiente bloque al fichero de configuración de nginx:

```nginx
location /teleradio {
    proxy_pass         http://signal4.cps.unizar.es:8502;
    proxy_http_version 1.1;

    # Necesario para WebSocket (chat en tiempo real)
    proxy_set_header   Upgrade    $http_upgrade;
    proxy_set_header   Connection "upgrade";

    proxy_set_header   Host       $host;
    proxy_set_header   X-Real-IP  $remote_addr;

    # Aumentar timeout para respuestas largas del LLM
    proxy_read_timeout 180s;
    proxy_send_timeout 180s;
}
```

El servidor Node.js de Next.js (`server.js`) actúa como proxy interno:
- **REST** (`/teleradio/api/*`) → rewrite → `http://localhost:8000/api/*`
- **Imágenes** (`/teleradio/images/*`) → rewrite → `http://localhost:8000/images/*`
- **WebSocket** (`/teleradio/ws/chat`) → proxy TCP → `ws://localhost:8000/ws/chat`

El puerto 8000 **no necesita ser accesible** desde el exterior.

Actualizar también `CORS_ORIGINS` en `backend/.env`:

```dotenv
CORS_ORIGINS=http://localhost:8502,https://dihana.unizar.es
```

---

## 7. Imágenes PNG

Las imágenes de las páginas de la revista se sirven como ficheros estáticos desde el backend:

```dotenv
# backend/.env
PNG_BASE_DIR=/ruta/absoluta/al/directorio/pngprocessed
PNG_BASE_URL=/teleradio/images
```

Estructura esperada del directorio:

```
pngprocessed/
├── ejemplar_1/
│   ├── ejemplar_1_pagina_001.png
│   ├── ejemplar_1_pagina_002.png
│   └── ...
├── ejemplar_2/
│   └── ...
└── ...
```

---

## 8. Solución de problemas frecuentes

### Las imágenes no cargan (404)

- Verificar que `PNG_BASE_DIR` apunta al directorio correcto y existe.
- Verificar que `PNG_BASE_URL=/teleradio/images` (ruta relativa, **sin** `http://`).
- Reiniciar el backend tras editar `.env`.

### Error de conexión WebSocket

- El WebSocket se conecta a la misma URL base que la página (mismo host y puerto).
- Si hay un proxy nginx, asegurarse de que tiene las cabeceras `Upgrade` y `Connection "upgrade"`.
- Verificar que el backend está en marcha: `curl http://localhost:8000/docs`.

### Login devuelve "Not Found"

- Verificar que `NEXT_PUBLIC_BASE_PATH=/teleradio` está en `frontend/.env.local`.
- Reiniciar el frontend tras modificar `.env.local`.

### El backend no arranca (puerto 8000 ocupado)

```bash
# Ver qué proceso usa el puerto
lsof -ti:8000
# Liberarlo
kill -9 $(lsof -ti:8000)
```

### Uvicorn muestra `Invalid HTTP request received`

Significa que hay tráfico TLS llegando al puerto HTTP 8000 directamente desde el exterior. Asegurarse de que todo el tráfico pasa por el frontend Next.js (puerto 8502) y que `PNG_BASE_URL` es una ruta relativa.

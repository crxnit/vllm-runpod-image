# Deploying Kate's College Advisor to Production

## Background

Kate's College Advisor is a purpose-built AI chat interface that connects to a vLLM model running on a RunPod GPU pod. The UI is a static HTML/JS/CSS application — no build step, no server-side rendering. The browser communicates with the vLLM backend via its OpenAI-compatible API (`/v1/chat/completions`).

In the default local setup, users enter the RunPod endpoint URL and API key directly in the browser. For production, this deployment adds three layers:

1. **Traefik** handles TLS termination, domain routing, and basic auth at `kate.jjocapps.com`.
2. **Nginx** serves the static UI files and reverse-proxies API requests to the RunPod vLLM backend, injecting the API key server-side so it never reaches the browser.
3. **proxy-config.js** is injected into the HTML at serve time, pre-configuring the UI to use `/api` as its endpoint — Kate sees the chat interface immediately with no setup overlay.

## Architecture

```
                   Internet
                      |
              kate.jjocapps.com
                      |
               +-----------+
               |  Traefik   |   TLS termination (Let's Encrypt)
               |            |   Basic auth (kate-auth middleware)
               +-----------+
                      |
               portal-net (Docker network)
                      |
               +-----------+
               |   Nginx    |   Serves static UI files from ui/
               |  (alpine)  |   Injects proxy-config.js via sub_filter
               |            |   Proxies /api/* -> RunPod vLLM backend
               +-----------+
                      |
              https://pod-id-8000.proxy.runpod.net
                      |
               +-----------+
               |   vLLM     |   GPU pod on RunPod
               |  (A5000)   |   Qwen 7B Instruct AWQ
               +-----------+
```

### Request flow

**Static assets** (HTML, JS, CSS):
```
Browser -> Traefik (auth + TLS) -> Nginx -> ui/ files on disk
```

**Chat API calls** (streaming SSE):
```
Browser POST /api/v1/chat/completions (no auth header)
  -> Traefik (auth + TLS)
  -> Nginx (rewrites /api/v1/* to /v1/*, adds Authorization header)
  -> RunPod vLLM backend (authenticated, streaming response)
```

### Why a backend proxy?

Without the proxy, the RunPod URL and vLLM API key are stored in the browser's `localStorage` and visible in devtools. The proxy keeps both server-side:

- The API key exists only in the `.env` file on the server.
- The RunPod pod URL is hidden from the browser entirely.
- The browser only knows about `kate.jjocapps.com/api`.

### How proxy-config.js injection works

The `college-advisor.html` source file is **not modified** for production — it works unchanged for local testing (where the setup overlay prompts for endpoint and key).

In production, nginx's `sub_filter` directive injects a `<script>` tag into the HTML before `</head>`:

```
sub_filter '</head>' '<script src="/proxy-config.js"></script></head>';
```

`proxy-config.js` sets `CHAT_CONFIG.endpoint = '/api'` before `chat.js` reads the config. This pre-configures the connection so the setup overlay is skipped and Kate lands directly in the chat.

### File layout

```
deploy/
  DEPLOY.md                  This file
  docker-compose.yml          Nginx service with Traefik labels
  nginx.conf.template         Nginx config (envsubst template)
  proxy-config.js             Injected JS to pre-configure /api endpoint
  .env.example                Template for environment variables
  .env                        Actual secrets (gitignored)

ui/                           Static UI files (mounted read-only into Nginx)
  college-advisor.html        Kate's advisor interface
  index.html                  Developer chat interface
  shared/
    chat.js                   Chat engine
    markdown.js               Markdown renderer
    styles.css                Shared styles
```

## Prerequisites

- Ubuntu Linux server with Docker and Docker Compose installed.
- Traefik already running in a Docker container on the `portal-net` network, with a `websecure` entrypoint and a `letsencrypt` cert resolver configured.
- DNS: `kate.jjocapps.com` pointing to the server's public IP.
- `apache2-utils` installed (for `htpasswd`). Install with `sudo apt install apache2-utils` if needed.
- A running RunPod vLLM pod with its proxy URL and API key.

## Deployment steps

### 1. Clone the repo on the server

```bash
git clone https://github.com/crxnit/vllm-runpod-image.git
cd vllm-runpod-image/deploy
```

### 2. Create the environment file

```bash
cp .env.example .env
```

### 3. Set the vLLM backend URL and API key

Edit `.env` and fill in the RunPod pod URL and the `VLLM_API_KEY` value from your RunPod template:

```bash
VLLM_BACKEND=https://your-pod-id-8000.proxy.runpod.net
VLLM_API_KEY=your-vllm-api-key
```

No trailing slash on the URL. This is the same URL you'd enter in the browser setup overlay.

### 4. Generate the basic auth credentials

```bash
htpasswd -nB kate
```

Enter and confirm a password. Copy the output (e.g., `kate:$2y$05$abc123...`) and paste it into the `TRAEFIK_BASIC_AUTH` variable in `.env`.

**Important:** Double every `$` sign for Docker Compose. For example:

```
# htpasswd output:
kate:$2y$05$abc123def456

# In .env ($ doubled):
TRAEFIK_BASIC_AUTH=kate:$$2y$$05$$abc123def456
```

### 5. Start the service

```bash
docker compose up -d
```

Verify it's running:

```bash
docker compose logs -f kate-advisor
```

You should see nginx start and the envsubst template being processed. Visit `https://kate.jjocapps.com` — you'll be prompted for basic auth credentials, then see the college advisor chat.

## Updating

### When the RunPod pod changes

RunPod pods get new URLs when recreated. Update the backend URL:

```bash
# Edit .env with the new pod URL
nano .env

# Recreate the container to pick up the new env
docker compose up -d
```

### When UI files change

Pull the latest code — nginx serves from the mounted `ui/` directory, so changes are live immediately:

```bash
cd /path/to/vllm-runpod-image
git pull
```

No container restart needed for HTML/JS/CSS changes.

### Changing the auth password

```bash
htpasswd -nB kate
# Update TRAEFIK_BASIC_AUTH in .env (double the $ signs)
docker compose up -d
```

## Troubleshooting

### "502 Bad Gateway"

The vLLM backend is unreachable. Check:
- Is the RunPod pod running? Check the RunPod dashboard.
- Is the `VLLM_BACKEND` URL correct in `.env`?
- Can the server reach RunPod? `curl -I https://your-pod-id-8000.proxy.runpod.net/v1/models`

### "401 Unauthorized" from the chat (not the browser auth prompt)

The vLLM API key is wrong. Check `VLLM_API_KEY` in `.env` matches the `VLLM_API_KEY` environment variable in your RunPod template.

### Chat loads but shows "Unreachable" status

The proxy is working but can't connect to the backend. Same checks as 502 above. Also verify the pod has finished booting — vLLM takes 20-60 seconds to start serving after the container starts.

### Setup overlay appears instead of the chat

`proxy-config.js` isn't being injected. Check:
- `docker compose logs kate-advisor` for nginx errors.
- `curl -s https://kate.jjocapps.com/ | grep proxy-config` to verify the script tag is present in the HTML.

### Need to test locally without the proxy

Open `ui/college-advisor.html` directly in a browser. Without the proxy-config injection, the setup overlay will appear and you can enter the RunPod URL and API key directly. Note: the endpoint validation requires HTTPS for non-localhost URLs, so use the full `https://` RunPod proxy URL.

## Security

The deployment includes several hardening measures:

- **TLS everywhere.** Traefik terminates TLS with Let's Encrypt. The web UI rejects non-HTTPS endpoint URLs (except localhost) to prevent API keys from being sent in cleartext.
- **API key isolation.** The vLLM API key lives only in the server-side `.env` file. Nginx injects it into proxied requests via the `Authorization` header. The browser never sees or stores the key.
- **Basic auth at the edge.** Traefik enforces HTTP basic auth before any request reaches Nginx. Credentials are bcrypt-hashed.
- **Security headers.** Nginx adds `X-Frame-Options: DENY` (anti-clickjacking), `X-Content-Type-Options: nosniff`, and `Referrer-Policy: strict-origin-when-cross-origin`.
- **Read-only mounts.** The `ui/` directory and nginx config are mounted read-only into the container.

# Self-Hosted Git + Container Registry with Gitea

Gitea is a lightweight, self-hosted Git service written in Go that includes a built-in container registry. It provides a GitHub-like experience (repos, issues, PRs, CI/CD, package registry) on your own infrastructure.

## Why Gitea?

- **All-in-one**: Git hosting + container registry + CI/CD (Gitea Actions) in a single service
- **Lightweight**: runs on 1 CPU / 512MB RAM minimum — far lighter than GitLab
- **OCI-compliant registry**: supports Docker images, Helm charts, and other OCI artifacts
- **No vendor lock-in**: your code and images live on infrastructure you control
- **Free and open source**: MIT licensed

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Gitea Server (single binary or Docker)         │
│                                                 │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐ │
│  │ Git Repos│  │ Container│  │ Gitea Actions │ │
│  │          │  │ Registry │  │ (CI/CD)       │ │
│  └──────────┘  └──────────┘  └───────────────┘ │
│                                                 │
│  ┌──────────┐  ┌──────────────────────────────┐ │
│  │ Database │  │ Storage (local or S3/Minio)  │ │
│  │ SQLite/  │  │ - Git repos                  │ │
│  │ Postgres │  │ - Container image layers     │ │
│  └──────────┘  │ - CI/CD artifacts            │ │
│                └──────────────────────────────┘ │
└─────────────────────────────────────────────────┘
         │
         │  HTTPS (port 443)
         │  SSH (port 22)
         ▼
┌─────────────────┐     ┌─────────────────┐
│ Developers      │     │ RunPod / Docker  │
│ git push/pull   │     │ docker pull      │
│ docker push     │     │                  │
└─────────────────┘     └─────────────────┘
```

**Components:**
- **Gitea**: single Go binary or Docker container — handles web UI, Git, API, and registry
- **Database**: SQLite for personal use, PostgreSQL for production/teams
- **Storage**: local filesystem or S3-compatible (Minio, OCI Object Storage, AWS S3) for large-scale image storage
- **Reverse proxy**: Nginx or Caddy for HTTPS termination (required for Docker registry)

## Sizing Requirements

### Personal / Small Team (1-5 users)

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 1 core | 2 cores |
| RAM | 512MB | 1GB |
| Disk (OS + Gitea) | 5GB | 10GB |
| Disk (container images) | Depends on usage | 200GB+ for LLM images |
| Database | SQLite | SQLite |

### Production / Team (5-50 users)

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 2 cores | 4 cores |
| RAM | 2GB | 4GB |
| Disk (OS + Gitea) | 10GB | 20GB |
| Disk (container images) | Depends on usage | 500GB+ |
| Database | PostgreSQL | PostgreSQL |

### Storage Considerations for LLM Images

Container images for LLM models are large:

| Model | Approximate Image Size |
|---|---|
| 3B AWQ | ~10-15 GB |
| 32B AWQ | ~25-30 GB |
| 70B AWQ | ~45-50 GB |

Plan disk accordingly. If hosting multiple model variants, 500GB-1TB of storage is recommended. S3-compatible backend storage is ideal for this use case.

## Cost Estimates

### OCI (Oracle Cloud Infrastructure)

| Resource | Spec | Monthly Cost |
|---|---|---|
| VM.Standard.E4.Flex | 2 OCPU / 8GB RAM | ~$25 |
| Boot volume | 50GB | ~$1.25 |
| Block volume (images) | 500GB | ~$12.50 |
| **Total** | | **~$39/month** |

OCI Always Free tier includes ARM instances (A1.Flex, 4 OCPU / 24GB RAM) which can run Gitea for free — you'd only pay for block storage beyond the free 200GB.

### AWS

| Resource | Spec | Monthly Cost |
|---|---|---|
| t3.small | 2 vCPU / 2GB RAM | ~$15 |
| EBS (gp3) | 500GB | ~$40 |
| **Total** | | **~$55/month** |

### Hetzner (budget option)

| Resource | Spec | Monthly Cost |
|---|---|---|
| CX22 | 2 vCPU / 4GB RAM | ~$4 |
| Volume | 500GB | ~$25 |
| **Total** | | **~$29/month** |

### Comparison with Managed Services

| Service | Storage Cost | Bandwidth | Notes |
|---|---|---|---|
| **Self-hosted Gitea** | ~$30-55/month | Included | Full control |
| **GHCR (public)** | Free | Free | No control, vendor dependency |
| **GHCR (private)** | $0.25/GB/month | $0.50/GB | 500GB = $125/month |
| **AWS ECR** | $0.10/GB/month | $0.09/GB out | 500GB = $50/month + transfer |
| **Docker Hub** | Free (1 private repo) | Rate limited | Limited private repos |

## Setup Steps

### Prerequisites

- A Linux server (Ubuntu 22.04/24.04 recommended)
- A domain name pointing to the server (e.g. `git.yourdomain.com`)
- Docker and Docker Compose installed

### Step 1: Create Docker Compose File

```bash
mkdir -p /opt/gitea && cd /opt/gitea
```

Create `docker-compose.yml`:

```yaml
version: "3"

services:
  gitea:
    image: gitea/gitea:latest
    container_name: gitea
    restart: always
    environment:
      - USER_UID=1000
      - USER_GID=1000
      - GITEA__database__DB_TYPE=sqlite3
      - GITEA__server__ROOT_URL=https://git.yourdomain.com/
      - GITEA__server__SSH_DOMAIN=git.yourdomain.com
      - GITEA__server__LFS_START_SERVER=true
      - GITEA__packages__ENABLED=true
    volumes:
      - ./data:/data
      - ./config:/etc/gitea
      - /etc/timezone:/etc/timezone:ro
      - /etc/localtime:/etc/localtime:ro
    ports:
      - "3000:3000"
      - "2222:22"

  caddy:
    image: caddy:latest
    container_name: caddy
    restart: always
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - ./caddy_data:/data
      - ./caddy_config:/config
```

### Step 2: Configure Reverse Proxy (HTTPS)

Create `Caddyfile`:

```
git.yourdomain.com {
    reverse_proxy gitea:3000
}
```

Caddy automatically provisions Let's Encrypt TLS certificates.

### Step 3: Start Services

```bash
docker compose up -d
```

### Step 4: Initial Configuration

1. Open `https://git.yourdomain.com` in your browser
2. Complete the installation wizard (database, admin account)
3. The container registry is enabled by default (`GITEA__packages__ENABLED=true`)

### Step 5: Create a Repository

Create a repository in Gitea to host your container images (e.g. `vllm-runpod-image`).

### Step 6: Push Container Images

```bash
# Log in to your Gitea registry
docker login git.yourdomain.com

# Tag and push
docker tag ghcr.io/crxnit/vllm-runpod-image:3b-coder-awq \
  git.yourdomain.com/youruser/vllm-runpod-image:3b-coder-awq

docker push git.yourdomain.com/youruser/vllm-runpod-image:3b-coder-awq
```

### Step 7: Pull from RunPod

In your RunPod template, set the image to:
```
git.yourdomain.com/youruser/vllm-runpod-image:3b-coder-awq
```

If the registry requires authentication, configure it in:
RunPod Console > Container Registry > Add credentials for `git.yourdomain.com`

## Optional: S3 Backend for Image Storage

For large-scale image storage, configure Gitea to use S3-compatible storage instead of local disk. Add to `docker-compose.yml` environment:

```yaml
- GITEA__storage__STORAGE_TYPE=minio
- GITEA__storage__MINIO_ENDPOINT=s3.amazonaws.com
- GITEA__storage__MINIO_ACCESS_KEY_ID=your-key
- GITEA__storage__MINIO_SECRET_ACCESS_KEY=your-secret
- GITEA__storage__MINIO_BUCKET=gitea-packages
- GITEA__storage__MINIO_USE_SSL=true
```

This also works with OCI Object Storage, Minio, or any S3-compatible service.

## Optional: Gitea Actions (CI/CD)

Gitea Actions is compatible with GitHub Actions workflow syntax. To enable:

1. Add to `docker-compose.yml` environment:
   ```yaml
   - GITEA__actions__ENABLED=true
   ```

2. Set up a runner (similar to GitHub Actions runners):
   ```bash
   docker run -d --name gitea-runner \
     -v /var/run/docker.sock:/var/run/docker.sock \
     gitea/act_runner:latest
   ```

3. Register the runner with your Gitea instance

4. Your existing `.github/workflows/` files will work with minimal changes

## Backup

Back up the Gitea data directory regularly:

```bash
# Stop services
docker compose stop

# Backup data
tar czf gitea-backup-$(date +%Y%m%d).tar.gz data/ config/

# Restart
docker compose up -d
```

For container images stored locally, include the data directory in your backup. For S3 backend, the images are already stored redundantly.

## Security Considerations

- Always use HTTPS (Caddy handles this automatically with Let's Encrypt)
- Use strong passwords and enable 2FA for admin accounts
- Restrict SSH access to the server (firewall rules)
- Keep Gitea updated (`docker compose pull && docker compose up -d`)
- Consider placing the server behind a VPN if it should not be publicly accessible
- Use personal access tokens instead of passwords for Docker login

## Sources

- [Gitea Documentation](https://docs.gitea.com/)
- [Gitea Container Registry Docs](https://docs.gitea.com/usage/packages/container)
- [Gitea Docker Installation](https://docs.gitea.com/installation/install-with-docker)
- [Gitea Actions](https://docs.gitea.com/usage/actions/overview)

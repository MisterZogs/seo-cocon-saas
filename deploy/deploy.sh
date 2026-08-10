#!/usr/bin/env bash
# deploy.sh — Déploiement sur VPS via rsync + docker compose
#
# Usage :
#   VPS_HOST=root@vps.ip.or.domain ./deploy/deploy.sh
#
# Prérequis local :
#   - clé SSH configurée pour le VPS
#   - fichier ./.env (copié depuis .env.production.example, jamais commit)
#
# Prérequis VPS :
#   - Docker + Docker Compose plugin installés
#   - Ports 80 et 443 ouverts sur le firewall
#   - Domaine (DOMAIN dans .env) pointant vers l'IP du VPS
#
# Ce que fait le script :
#   1. Vérifie que .env existe localement
#   2. rsync le repo vers le VPS (exclut node_modules, .venv, .git, .next)
#   3. Copie .env sur le VPS
#   4. Sur le VPS : docker compose up -d --build

set -euo pipefail

BLUE='\033[0;34m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${BLUE}▸ $*${NC}"; }
ok()    { echo -e "${GREEN}✓ $*${NC}"; }
warn()  { echo -e "${YELLOW}⚠ $*${NC}"; }
die()   { echo -e "${RED}✗ $*${NC}" >&2; exit 1; }

VPS_HOST="${VPS_HOST:?Variable VPS_HOST non définie. Ex: VPS_HOST=root@1.2.3.4 ./deploy/deploy.sh}"
REMOTE_DIR="${REMOTE_DIR:-/opt/cocon}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ---- 1. Vérifs préalables ----
info "Vérification des prérequis locaux..."
[ -f "$REPO_DIR/.env" ] || die "Fichier .env introuvable. Copie .env.production.example → .env et remplis-le."
grep -q "^DOMAIN=" "$REPO_DIR/.env" || die ".env doit contenir DOMAIN=..."
grep -q "^ANTHROPIC_API_KEY=sk-ant-" "$REPO_DIR/.env" || die ".env doit contenir ANTHROPIC_API_KEY=sk-ant-..."

# JWT_SECRET : sans lui le backend refuse de démarrer (cf. backend/auth.py) et
# docker compose refuse même de lancer le service. Autant échouer ici, avant le
# rsync, plutôt que sur le VPS avec la prod à l'arrêt.
JWT_LINE="$(grep '^JWT_SECRET=' "$REPO_DIR/.env" || true)"
[ -n "$JWT_LINE" ] || die ".env doit contenir JWT_SECRET=... — générer avec :
    python3 -c \"import secrets; print(secrets.token_urlsafe(48))\""
[ "${#JWT_LINE}" -ge 43 ] || die "JWT_SECRET fait moins de 32 caractères — le backend le refusera."
ok "Prérequis locaux OK"

# ---- 2. rsync du repo vers le VPS ----
info "Sync du repo vers $VPS_HOST:$REMOTE_DIR ..."
ssh "$VPS_HOST" "mkdir -p $REMOTE_DIR"

rsync -az --delete \
    --exclude='.git/' \
    --exclude='node_modules/' \
    --exclude='.next/' \
    --exclude='.venv/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='.pytest_cache/' \
    --exclude='.mypy_cache/' \
    --exclude='.env' \
    --exclude='.env.local' \
    --exclude='backend/tests/output/' \
    "$REPO_DIR/" "$VPS_HOST:$REMOTE_DIR/"

# ---- 3. Copie de .env séparément (pour ne pas leaker en cas d'exclude) ----
info "Copie du fichier .env..."
scp "$REPO_DIR/.env" "$VPS_HOST:$REMOTE_DIR/.env"
ssh "$VPS_HOST" "chmod 600 $REMOTE_DIR/.env"
ok "Sync terminé"

# ---- 4. Build + start sur le VPS ----
info "Build + démarrage des containers sur le VPS..."
ssh "$VPS_HOST" "cd $REMOTE_DIR && docker compose pull redis caddy 2>&1 | tail -5 && docker compose up -d --build"

# ---- 5. Status ----
info "État des services :"
ssh "$VPS_HOST" "cd $REMOTE_DIR && docker compose ps"

DOMAIN_FROM_ENV=$(grep '^DOMAIN=' "$REPO_DIR/.env" | cut -d'=' -f2)
ok "Déploiement terminé. Ouvre : https://$DOMAIN_FROM_ENV"
warn "Le premier démarrage prend 30-60s (Caddy provisionne le certificat Let's Encrypt)."

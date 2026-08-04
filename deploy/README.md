# Déploiement VPS — Cocon Sémantique SaaS

Stack : Docker Compose · Caddy (HTTPS auto) · FastAPI · RQ worker · Redis · Next.js.

---

## 1. Prérequis VPS (Ubuntu 22.04+)

Une seule fois, sur le VPS :

```bash
# Docker + Docker Compose plugin
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER    # se déconnecter/reconnecter pour appliquer

# Firewall : ouvrir 80 + 443 (Caddy s'occupe du reste)
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

DNS : faire pointer un enregistrement `A` (ou `AAAA`) de ton domaine
(ex: `cocon.example.com`) vers l'IP du VPS. Sans ça, Caddy ne pourra pas
obtenir le certificat Let's Encrypt.

---

## 2. Setup local (une fois)

```bash
cp .env.production.example .env
# éditer .env :
#   DOMAIN=cocon.example.com          # ton domaine
#   ANTHROPIC_API_KEY=sk-ant-...      # ta clé
#   DATAFORSEO_LOGIN=...              # ou vide pour rester en mock
#   DATAFORSEO_PASSWORD=...
```

Le fichier `.env` est déjà dans `.gitignore` — il ne sera jamais commit.

---

## 3. Déploiement

```bash
VPS_HOST=root@1.2.3.4 ./deploy/deploy.sh
```

Le script :
1. Vérifie que `.env` existe et contient `DOMAIN` + `ANTHROPIC_API_KEY`
2. rsync le code sur le VPS dans `/opt/cocon`
3. Copie `.env` séparément (chmod 600)
4. Sur le VPS : `docker compose up -d --build`
5. Affiche l'état des containers

Premier démarrage : ~1-2 min (build images + certif Let's Encrypt).
Redéploiements : ~20-40s (Docker layer cache).

---

## 4. Vérification

```bash
# Depuis ton laptop
curl https://cocon.example.com/api/health
# → {"status":"ok","redis":"up"}

# Logs en direct
ssh root@1.2.3.4 'cd /opt/cocon && docker compose logs -f'

# Un seul service
ssh root@1.2.3.4 'cd /opt/cocon && docker compose logs -f worker'
```

---

## 5. Commandes utiles sur le VPS

```bash
cd /opt/cocon

# État
docker compose ps

# Restart
docker compose restart

# Down + up (recréer les containers)
docker compose down && docker compose up -d

# Voir les logs du worker
docker compose logs -f worker

# Voir la queue Redis
docker compose exec redis redis-cli
> LLEN rq:queue:pipeline
> HGETALL rq:job:XXXXXXXX

# Purger la queue si besoin
docker compose exec redis redis-cli FLUSHDB
```

---

## 6. Mise à jour

Après avoir modifié le code localement :

```bash
git commit -am "..."
git push
VPS_HOST=root@1.2.3.4 ./deploy/deploy.sh
```

Le script rsync uniquement ce qui a changé, puis `docker compose up -d --build`
qui ne rebuild que les services impactés grâce au cache.

---

## 7. Rollback

Le déploiement écrase l'existant sur le VPS. Pour rollback rapide :

```bash
# Localement
git checkout <commit-précédent>
VPS_HOST=root@1.2.3.4 ./deploy/deploy.sh
```

Pour un rollback plus robuste plus tard, on pourrait tagger les images
Docker et pusher sur un registre (GHCR ou Docker Hub) au lieu de build
sur le VPS.

---

## 8. Persistance des runs (Postgres auto-hébergé)

Le service `db` du compose est un `postgres:16-alpine` dédié au projet, avec son
propre volume. Instance distincte de celle de MyDoctorIA sur le même VPS : les
deux ont des cycles de vie indépendants.

Seule variable à renseigner dans `/opt/cocon/.env` :

```
DB_PASSWORD=<valeur aléatoire — openssl rand -base64 32>
```

`DATABASE_URL` est construit automatiquement par le compose à partir de là. Le
schéma (`backend/db/schema.sql`) est appliqué au démarrage du backend — il est
idempotent, aucune migration manuelle à lancer.

Vérification :

```bash
curl https://<domaine>/api/runs           # → {"enabled": true, ...}
docker compose logs backend | grep Postgres
# → Postgres connecté — persistance des runs active.
docker compose exec db psql -U cocon -d cocon_prod -c '\dt'
```

Sans `DB_PASSWORD`, le backend démarre quand même en mode dégradé : le pipeline
tourne, mais l'historique se limite au TTL Redis de 24h.

**Sauvegardes.** En auto-hébergé, personne ne sauvegarde à notre place.
`deploy/backup-db.sh` fait un dump compressé par jour avec 14 jours de
rétention, installé en cron à 3h17 :

```bash
crontab -l | grep backup-db          # vérifier
/opt/cocon/deploy/backup-db.sh       # lancer à la main
ls -lh /opt/cocon-backups/           # les dumps
tail /var/log/cocon-backup.log       # le journal
```

Restauration :

```bash
gunzip -c /opt/cocon-backups/cocon-AAAAMMJJ-HHMMSS.sql.gz \
  | docker compose exec -T db psql -U cocon -d cocon_prod
```

**Reprise après échec.** Un run qui casse en cours de route (crédit épuisé,
rate limit, timeout) garde ses étapes déjà produites. Le bouton « Reprendre la
génération » sur la page d'échec, ou `POST /api/jobs/{id}/retry`, repart de
l'article fautif sans repayer le reste.

Les checkpoints vont dans Postgres quand il est configuré, sinon dans Redis
avec un TTL de 7 jours. Pour les inspecter :

```bash
docker compose exec db psql -U cocon -d cocon_prod -c 'select run_id, step from run_checkpoints;'
docker compose exec redis redis-cli --scan --pattern 'checkpoint:*'
```

---

## 9. Coûts approximatifs

- VPS : dépend de l'hébergeur (Hetzner CX22 ~4€/mois, suffit)
- Domaine : ~10€/an
- Anthropic API : $10-15 par run de pipeline complet (2 cocons × Full mode)
- DataForSEO : ~$0.05-0.20 par run (KW + SERP + backlinks)
- Redis : gratuit (inclus dans le VPS)

Une agence qui génère 20 runs/mois → ~$300 API + serveur = pricing $999/mois
à l'agence laisse une marge saine.

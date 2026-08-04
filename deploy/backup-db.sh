#!/usr/bin/env bash
# Sauvegarde quotidienne de la base des runs.
#
# Postgres tourne ici en auto-hébergé : personne d'autre ne sauvegarde à notre
# place. Un dump compressé par jour, 14 jours de rétention.
#
# Installation (sur le VPS, une fois) :
#   crontab -e
#   17 3 * * * /opt/cocon/deploy/backup-db.sh >> /var/log/cocon-backup.log 2>&1

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/cocon}"
BACKUP_DIR="${BACKUP_DIR:-/opt/cocon-backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

mkdir -p "$BACKUP_DIR"

STAMP=$(date +%Y%m%d-%H%M%S)
TARGET="$BACKUP_DIR/cocon-$STAMP.sql.gz"

cd "$PROJECT_DIR"
docker compose exec -T db pg_dump -U cocon -d cocon_prod --no-owner \
    | gzip -9 > "$TARGET"

# Un dump vide ou tronqué est pire qu'une absence de dump : on le détecte tout
# de suite plutôt que le jour de la restauration.
if [ ! -s "$TARGET" ] || ! gzip -t "$TARGET" 2>/dev/null; then
    echo "$(date -Is) ERREUR : dump invalide, suppression de $TARGET"
    rm -f "$TARGET"
    exit 1
fi

find "$BACKUP_DIR" -name 'cocon-*.sql.gz' -mtime "+$RETENTION_DAYS" -delete

echo "$(date -Is) OK $TARGET ($(du -h "$TARGET" | cut -f1)) — $(ls -1 "$BACKUP_DIR" | wc -l) dumps conservés, disque à $(df -h / | awk 'NR==2 {print $5}')"

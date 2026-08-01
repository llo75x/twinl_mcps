#!/usr/bin/env bash
# =============================================================================
# Phase 1 du déploiement de MCP projea2 — À LANCER EN ROOT SUR LE VPS.
#
#   ssh vps "sudo -n bash /opt/twinl_mcps/mcps/sql/projea2_bootstrap.sh"
#
# Fait, d'un seul geste et sans qu'aucun secret ne transite par le poste :
#   1. génère le mot de passe du user MariaDB `projea2_mcp` (40 car., ICI) ;
#   2. l'injecte dans projea2_setup.sql → /root/projea2_setup.local.sql (600) ;
#   3. exécute le DDL en root MariaDB (user + DB miroir + 45 vues) ;
#   4. VÉRIFIE le résultat et rend un verdict lisible (pas du SQL brut à décoder) ;
#   5. écrit /opt/twinl_mcps/mcps/projea2.env (600, ethan), en héritant
#      AUTHKIT_DOMAIN et les réglages Slack de projea.env.
#
# IDEMPOTENT : rejouable. Attention, chaque exécution RÉINITIALISE le mot de
# passe de `projea2_mcp` et réécrit projea2.env — les deux restent donc
# cohérents entre eux, mais il faut redémarrer le conteneur après coup
# (`docker compose up -d mcp-projea2`).
#
# Le mot de passe n'est JAMAIS affiché. Il est lisible en root dans
# /opt/twinl_mcps/mcps/projea2.env — c'est de là qu'on le copie dans 1Password.
# =============================================================================

set -uo pipefail
umask 077

SRC=/opt/twinl_mcps/mcps/sql/projea2_setup.sql
LOCAL=/root/projea2_setup.local.sql
ENVDIR=/opt/twinl_mcps/mcps
ENVFILE="$ENVDIR/projea2.env"

ko() { echo; echo "❌ ÉCHEC : $*"; echo "   Rien n'est à moitié fait : le script est rejouable tel quel."; exit 1; }

echo "=== 0. Contrôles préalables ==================================="
[ "$(id -u)" -eq 0 ]        || ko "à lancer en root (sudo)."
[ -r "$SRC" ]               || ko "DDL introuvable : $SRC — faire un 'git pull' dans /opt/twinl_mcps."
[ -r "$ENVDIR/projea.env" ] || ko "projea.env introuvable : impossible d'hériter AUTHKIT_DOMAIN et Slack."
command -v mysql >/dev/null || ko "client mysql absent."
mysql -e "SELECT 1" >/dev/null 2>&1 || ko "pas d'accès root à MariaDB (socket)."
echo "OK — root, DDL présent, MariaDB joignable."

echo
echo "=== 1. Génération du mot de passe (sur ce serveur) ============"
P="$(tr -dc 'A-Za-z0-9-_=+' < /dev/urandom | head -c 40)"
[ "${#P}" -eq 40 ] || ko "génération du mot de passe (longueur ${#P})."
echo "OK — 40 caractères, jamais affiché."

echo
echo "=== 2. Injection dans le DDL =================================="
# Substitution sur la chaîne COMPLÈTE : `CHANGE_ME_STRONG_PASSWORD` seul
# apparaît aussi dans l'en-tête de commentaires du fichier.
sed "s|IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD'|IDENTIFIED BY '$P'|" "$SRC" > "$LOCAL" \
  || ko "écriture de $LOCAL."
chmod 600 "$LOCAL"
grep -q "CHANGE_ME_STRONG_PASSWORD'" "$LOCAL" && ko "le placeholder est toujours là (DDL modifié ?)."
echo "OK — $LOCAL (chmod 600, root)."

echo
echo "=== 3. Exécution du DDL en root MariaDB ======================="
if ! mysql --table < "$LOCAL"; then
  ko "MariaDB a rejeté le DDL (voir l'erreur ci-dessus)."
fi
echo "OK — DDL exécuté."

echo
echo "=== 4. Vérification du résultat ==============================="
q() { mysql --skip-column-names --batch -e "$1" 2>/dev/null; }

NB_VUES=$(q "SELECT COUNT(*) FROM information_schema.tables
             WHERE table_schema='projea2_readonly' AND table_type='VIEW';")
FUITE_COL=$(q "SELECT COUNT(*) FROM information_schema.columns
               WHERE table_schema='projea2_readonly' AND (
                 (table_name='users'            AND column_name='password_hash') OR
                 (table_name='email_messages'   AND column_name='unsubscribe_token') OR
                 (table_name='tracking_links'   AND column_name='token') OR
                 (table_name='export_approvals' AND column_name='token') OR
                 (table_name='tracking_hits'    AND column_name='ip'));")
FUITE_TBL=$(q "SELECT COUNT(*) FROM information_schema.tables
               WHERE table_schema='projea2_readonly' AND table_name IN
                 ('password_tokens','alembic_version','migration_id_map',
                  'migration_runs','migration_watermarks');")
SANS_VUE=$(q "SELECT COUNT(*) FROM information_schema.tables t
              WHERE t.table_schema='projea2' AND t.table_type='BASE TABLE'
                AND t.table_name NOT IN ('password_tokens','alembic_version',
                    'migration_id_map','migration_runs','migration_watermarks')
                AND NOT EXISTS (SELECT 1 FROM information_schema.tables v
                    WHERE v.table_schema='projea2_readonly' AND v.table_type='VIEW'
                      AND v.table_name=t.table_name);")
COLLISION=$(q "SELECT COUNT(*) FROM mysql.user WHERE User='projea2_readonly' AND Host='%';")
APP_OK=$(q "SELECT COUNT(*) FROM mysql.user WHERE User='projea2_readonly' AND Host='127.0.0.1';")

# Une requête de contrôle qui ne renvoie RIEN (erreur SQL, droits) ne doit pas
# passer pour un « 0 » rassurant : on refuse de rendre un verdict à l'aveugle.
for v in NB_VUES FUITE_COL FUITE_TBL SANS_VUE COLLISION APP_OK; do
  [ -n "${!v}" ] || ko "le contrôle $v n'a rien renvoyé — vérification impossible."
done

ERR=0
printf '  %-46s %s\n' "vues créées (attendu 45)"                 "$NB_VUES"
[ "$NB_VUES" -ge 40 ] || { echo "    ⚠️  anormalement bas"; ERR=1; }
[ "$NB_VUES" = "45" ] || echo "    ⚠️  ≠ 45 : le schéma a bougé, voir 'tables sans vue' ci-dessous"
printf '  %-46s %s\n' "colonnes secrètes exposées (attendu 0)"    "$FUITE_COL"
[ "$FUITE_COL" = "0" ] || ERR=1
printf '  %-46s %s\n' "tables exclues exposées (attendu 0)"       "$FUITE_TBL"
[ "$FUITE_TBL" = "0" ] || ERR=1
printf '  %-46s %s\n' "tables source sans vue (attendu 0)"        "$SANS_VUE"
[ "$SANS_VUE" = "0" ] || echo "    ⚠️  table(s) nouvelle(s) : à exposer ou à exclure dans le DDL"
printf '  %-46s %s\n' "collision projea2_readonly@% (attendu 0)"  "$COLLISION"
[ "$COLLISION" = "0" ] || ERR=1
printf '  %-46s %s\n' "compte applicatif intact (attendu 1)"      "$APP_OK"

[ "$ERR" -eq 0 ] || ko "un contrôle de sécurité est au rouge (voir ci-dessus) — NE PAS continuer."
echo "OK — aucun secret exposé, compte applicatif intact."

echo
echo "=== 5. Écriture de projea2.env ================================"
inherit() { grep -E "^$1=" "$ENVDIR/projea.env" || true; }   # AUTHKIT + Slack : mêmes valeurs
{
  echo "MCP_SERVER_NAME=mcp-projea2-readonly"
  echo "MCP_PORT=8080"
  echo "MCP_INSTRUCTIONS_FILE=/app/instructions.md"
  echo "MCP_DB_HOST=host.docker.internal"
  echo "MCP_DB_PORT=3306"
  echo "MCP_DB_USER=projea2_mcp"
  echo "MCP_DB_PASS=$P"
  echo "MCP_DB_NAME=projea2_readonly"
  inherit AUTHKIT_DOMAIN
  echo "BASE_URL=https://mcp-projea2.twinl.fr"
  echo "MCP_MAX_ROWS=1000"
  echo "MCP_MAX_BYTES=1000000"
  echo "MCP_STMT_TIMEOUT_S=20"
  echo "MCP_ALLOWED_SUBJECTS="
  inherit SLACK_WEBHOOK_URL
  inherit SLACK_SIGNING_SECRET
  inherit SLACK_NOTIFY_THRESHOLD
  inherit SLACK_BYTES_THRESHOLD
  inherit SLACK_APPROVAL_TIMEOUT_S
} > "$ENVFILE" || ko "écriture de $ENVFILE."
chmod 600 "$ENVFILE"
chown ethan:ethan "$ENVFILE"

grep -q '^AUTHKIT_DOMAIN=https' "$ENVFILE" || ko "AUTHKIT_DOMAIN non hérité — projea.env a changé de forme."
echo "OK — $ENVFILE (chmod 600, ethan) :"
sed -E 's/=.*/=<valeur>/' "$ENVFILE" | sed 's/^/    /'

echo
echo "==============================================================="
echo "✅ PHASE 1 TERMINÉE."
echo
echo "   Le mot de passe de projea2_mcp est dans $ENVFILE (lisible en root)."
echo "   → le recopier dans 1Password, titre « MCP projea2 projea2_mcp »."
echo
echo "   Suite : phase 4 du runbook (build + démarrage du conteneur)."
echo "==============================================================="

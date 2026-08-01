#!/usr/bin/env bash
# =============================================================================
# Phase 5 du déploiement de MCP projea2 — Apache + TLS. À LANCER EN ROOT SUR LE VPS.
#
#   ssh vps "sudo -n bash /opt/twinl_mcps/mcps/deploy/projea2_apache.sh"
#
# Trois étapes, dans CET ordre — l'inverse couperait le serveur :
#   A. ServerAlias mcp-projea2.twinl.fr sur le vhost *:80  → ouvre le challenge ACME
#   B. certbot certonly --webroot                          → émet le certificat
#   C. vhost *:443 de projea2 ajouté au mcp.conf en place  → publie le service
#
# Pourquoi cet ordre : Apache REFUSE DE DÉMARRER sur un `SSLCertificateFile`
# introuvable. Poser le vhost :443 avant le certificat couperait TOUT le VPS —
# qui sert aussi ISPConfig, la Dataroom et projea2.twinl.fr.
#
# Filets :
#   - sauvegarde horodatée de mcp.conf avant CHAQUE modification ;
#   - `apachectl configtest` avant chaque reload ; si le test échoue, la
#     sauvegarde est restaurée et le script s'arrête SANS avoir rechargé ;
#   - idempotent : chaque étape se saute si elle est déjà faite.
#
# `certonly --webroot` et pas `--apache` : ce dernier fabriquerait son propre
# vhost SSL, sans `no-gzip` ni `flushpackets` — le streaming MCP serait cassé.
# =============================================================================

set -uo pipefail

DOMAIN=mcp-projea2.twinl.fr
CONF=/etc/apache2/sites-available/mcp.conf
EXAMPLE=/opt/twinl_mcps/mcps/deploy/apache-mcp.conf.example
WEBROOT=/var/www/html
LIVE=/etc/letsencrypt/live/$DOMAIN
STAMP=$(date +%Y%m%d-%H%M%S)

ko() { echo; echo "❌ ÉCHEC : $*"; exit 1; }

backup() {
  cp -a "$CONF" "$CONF.bak-$STAMP-$1" || ko "sauvegarde de $CONF impossible."
  echo "   sauvegarde : $CONF.bak-$STAMP-$1"
}

# Teste la config ; si elle est cassée, restaure la sauvegarde et s'arrête.
# On ne recharge JAMAIS une config qui n'a pas passé le test.
test_or_restore() {
  local bak="$CONF.bak-$STAMP-$1"
  if ! apachectl configtest 2>&1 | tail -3; then
    cp -a "$bak" "$CONF"
    ko "configtest a échoué — $CONF restauré depuis $bak, Apache PAS rechargé."
  fi
  if ! apachectl configtest >/dev/null 2>&1; then
    cp -a "$bak" "$CONF"
    ko "configtest a échoué — $CONF restauré depuis $bak, Apache PAS rechargé."
  fi
}

echo "=== 0. Contrôles préalables ==================================="
[ "$(id -u)" -eq 0 ] || ko "à lancer en root (sudo)."
[ -w "$CONF" ]       || ko "$CONF introuvable ou non modifiable."
[ -r "$EXAMPLE" ]    || ko "$EXAMPLE introuvable — 'git pull' dans /opt/twinl_mcps."
command -v certbot >/dev/null || ko "certbot absent."
getent hosts "$DOMAIN" >/dev/null || ko "$DOMAIN ne résout pas — faire le DNS (phase 3) d'abord."
echo "   $DOMAIN → $(getent hosts "$DOMAIN" | awk '{print $1}' | tr '\n' ' ')"
curl -sf http://127.0.0.1:8083/health >/dev/null || ko "le conteneur mcp-projea2 ne répond pas sur 8083 (phase 4)."
echo "OK — root, conf présente, DNS résolu, conteneur en service."

echo
echo "=== A. ServerAlias sur le vhost *:80 =========================="
if grep -q "ServerAlias $DOMAIN" "$CONF"; then
  echo "OK — alias déjà présent, rien à faire."
else
  backup A
  # Insertion après le DERNIER ServerAlias du vhost *:80 (celui de mcp-projea).
  sed -i "0,/^\( *\)ServerAlias mcp-projea\.twinl\.fr *$/s//&\n\1ServerAlias $DOMAIN/" "$CONF" \
    || ko "insertion de l'alias."
  grep -q "ServerAlias $DOMAIN" "$CONF" || ko "l'alias n'a pas été inséré (vhost *:80 modifié ?)."
  test_or_restore A
  systemctl reload apache2 || ko "reload apache2."
  echo "OK — alias ajouté, Apache rechargé."
fi

echo
echo "=== B. Certificat Let's Encrypt ==============================="
if [ -s "$LIVE/fullchain.pem" ] && [ -s "$LIVE/privkey.pem" ]; then
  echo "OK — certificat déjà présent :"
  openssl x509 -in "$LIVE/fullchain.pem" -noout -subject -enddate | sed 's/^/   /'
else
  certbot certonly --webroot -w "$WEBROOT" -d "$DOMAIN" \
          --non-interactive --agree-tos --keep-until-expiring \
    || ko "certbot n'a pas pu émettre le certificat (voir /var/log/letsencrypt/)."
  [ -s "$LIVE/fullchain.pem" ] || ko "certbot dit OK mais $LIVE/fullchain.pem est absent."
  echo "OK — certificat émis."
fi

echo
echo "=== C. Vhost *:443 ============================================"
if grep -q "ServerName $DOMAIN" "$CONF"; then
  echo "OK — vhost déjà présent, rien à faire."
else
  backup C
  # Le vhost est EXTRAIT du fichier d'exemple versionné (source unique), entre
  # ses deux sentinelles — pas recopié en dur ici, pour qu'ils ne divergent pas.
  BLOCK=$(awk '/^# >>> BEGIN mcp-projea2$/{f=1;next} /^# <<< END mcp-projea2$/{f=0} f' "$EXAMPLE")
  [ -n "$BLOCK" ] || ko "bloc mcp-projea2 introuvable dans $EXAMPLE (sentinelles absentes ?)."
  echo "$BLOCK" | grep -q "127.0.0.1:8083" || ko "le bloc extrait ne cible pas le port 8083."
  { echo; echo "# ── HTTPS — MCP projea2 (ajouté par projea2_apache.sh le $STAMP)"; echo "$BLOCK"; } >> "$CONF"
  test_or_restore C
  systemctl reload apache2 || ko "reload apache2."
  echo "OK — vhost ajouté, Apache rechargé."
fi

echo
echo "=== D. Vérification end-to-end ================================"
ERR=0
chk() { # libellé, attendu, obtenu
  printf '  %-42s %-28s' "$1" "$3"
  if [ "$2" = "$3" ]; then echo "✅"; else echo "❌ (attendu : $2)"; ERR=1; fi
}
chk "/health"            "200" "$(curl -s -o /dev/null -w '%{http_code}' "https://$DOMAIN/health")"
chk "/mcp sans jeton"    "401" "$(curl -s -o /dev/null -w '%{http_code}' "https://$DOMAIN/mcp")"
chk "HTTP → HTTPS"       "301" "$(curl -s -o /dev/null -w '%{http_code}' "http://$DOMAIN/mcp")"
GZ=$(curl -s -H 'Accept-Encoding: gzip' -o /dev/null -w '%{content_type}' -D - "https://$DOMAIN/health" 2>/dev/null | grep -ci '^content-encoding' || true)
chk "pas de compression (casse le stream)" "0" "$GZ"
RES=$(curl -s "https://$DOMAIN/.well-known/oauth-protected-resource/mcp" | grep -o '"resource":"[^"]*"' | cut -d'"' -f4)
chk "resource annoncée" "https://$DOMAIN/mcp" "$RES"

[ "$ERR" -eq 0 ] || ko "un contrôle end-to-end est au rouge (voir ci-dessus)."

echo
echo "==============================================================="
echo "✅ PHASE 5 TERMINÉE — https://$DOMAIN est en ligne."
echo
echo "   Reste, côté consoles web :"
echo "   · WorkOS  Connect → Configuration → Resource Indicators"
echo "             ajouter  https://$DOMAIN/mcp"
echo "   · Slack   Interactivity → Request URL"
echo "             https://$DOMAIN/slack/action  (déménagement depuis mcp-projea)"
echo "   · claude.ai  Settings → Connectors → Add custom connector"
echo "             https://$DOMAIN/mcp"
echo "==============================================================="

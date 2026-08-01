#!/usr/bin/env bash
# =============================================================================
# Phase 7 — Fermer le MCP legacy `mcp-projea` (base twinl). EN ROOT SUR LE VPS.
#
#   ssh vps "sudo -n bash /opt/twinl_mcps/mcps/deploy/projea_close.sh"
#
# Retire la façade HTTP du MCP legacy, et RIEN d'autre :
#   A. vhost *:443 de mcp-projea      → supprimé de mcp.conf
#   B. ServerAlias sur le vhost *:80  → supprimé
#   C. certificat Let's Encrypt        → supprimé (sinon `certbot renew` échouera
#                                        tous les mois sur un domaine plus servi,
#                                        et enverra des mails d'alerte)
#
# ⛔ NE TOUCHE PAS à la couche données, et ce n'est pas un oubli :
#      · user MariaDB `projea_readonly`   → utilisé par LEGACY_DATABASE_URL
#      · DB miroir   `twinl_readonly`     → lue par le pipeline de migration
#      · base source `twinl`              → l'archive (décision D29)
#    La migration PROJEA2 est REJOUABLE (`deploy.sh --reset-migration`) : elle
#    lit encore ces objets. Les supprimer casserait la remigration.
#
# Filets identiques aux autres scripts : sauvegarde horodatée, `configtest`
# AVANT tout reload, restauration automatique si le test échoue, idempotence.
#
# Réversible : le conteneur peut être relancé (`docker compose up -d mcp-projea`)
# et ce script rejoué à l'envers en recopiant le vhost depuis le fichier
# d'exemple versionné, puis en réémettant un certificat.
# =============================================================================

set -uo pipefail

DOMAIN=mcp-projea.twinl.fr
CONF=/etc/apache2/sites-available/mcp.conf
STAMP=$(date +%Y%m%d-%H%M%S)
BAK="$CONF.bak-$STAMP-close"

ko() { echo; echo "❌ ÉCHEC : $*"; exit 1; }

test_or_restore() {
  if ! apachectl configtest >/dev/null 2>&1; then
    apachectl configtest 2>&1 | tail -5
    cp -a "$BAK" "$CONF"
    ko "configtest a échoué — $CONF restauré depuis $BAK, Apache PAS rechargé."
  fi
}

echo "=== 0. Contrôles préalables ==================================="
[ "$(id -u)" -eq 0 ] || ko "à lancer en root (sudo)."
[ -w "$CONF" ]       || ko "$CONF introuvable."
# Le successeur doit être debout : on ne ferme pas l'ancien avant que le nouveau serve.
curl -sf https://mcp-projea2.twinl.fr/health >/dev/null \
  || ko "mcp-projea2 ne répond pas — on ne ferme pas le legacy tant que le successeur n'est pas en service."
echo "OK — root, conf présente, mcp-projea2 en service."

CHANGED=0
if grep -qE "^\s*(ServerName|ServerAlias)\s+$DOMAIN\s*$" "$CONF"; then
  cp -a "$CONF" "$BAK" || ko "sauvegarde impossible."
  echo "   sauvegarde : $BAK"

  echo
  echo "=== A+B. Retrait du vhost *:443 et de l'alias *:80 ============"
  # Un vhost est supprimé S'IL PORTE ce ServerName — on met le bloc entier en
  # tampon avant de décider, plutôt que de couper à l'aveugle entre deux motifs.
  # `mcp-projea.twinl.fr` n'est pas un préfixe de `mcp-projea2.twinl.fr`
  # (le point diffère du 2), donc aucun risque de faucher le successeur.
  awk -v dom="$DOMAIN" '
    /^[[:space:]]*<VirtualHost/ { inv=1; buf=$0 "\n"; drop=0; next }
    inv {
      buf = buf $0 "\n"
      if ($0 ~ "^[[:space:]]*ServerName[[:space:]]+" dom "[[:space:]]*$") drop=1
      if ($0 ~ /^[[:space:]]*<\/VirtualHost>/) { if (!drop) printf "%s", buf; inv=0; buf="" }
      next
    }
    # hors vhost : on jette la ligne ServerAlias du domaine fermé
    $0 ~ "^[[:space:]]*ServerAlias[[:space:]]+" dom "[[:space:]]*$" { next }
    { print }
  ' "$CONF" > "$CONF.new" || ko "réécriture de $CONF."

  # L'alias vit DANS le vhost *:80 : il est passé par le tampon, pas par la
  # règle hors-vhost ci-dessus. On le retire du résultat.
  sed -i "/^[[:space:]]*ServerAlias[[:space:]]\+$DOMAIN[[:space:]]*$/d" "$CONF.new"

  grep -q "mcp-projea2.twinl.fr" "$CONF.new" || ko "le vhost de projea2 a disparu du résultat — abandon."
  grep -q "mcp-iafec.twinl.fr"   "$CONF.new" || ko "le vhost d'iafec a disparu du résultat — abandon."
  grep -qE "^\s*(ServerName|ServerAlias)\s+$DOMAIN\s*$" "$CONF.new" \
    && ko "des références à $DOMAIN subsistent — abandon."

  mv "$CONF.new" "$CONF" || ko "remplacement de $CONF."
  test_or_restore
  systemctl reload apache2 || ko "reload apache2."
  echo "OK — vhost et alias retirés, Apache rechargé."
  CHANGED=1
else
  echo
  echo "=== A+B. Retrait du vhost *:443 et de l'alias *:80 ============"
  echo "OK — plus aucune référence à $DOMAIN, rien à faire."
fi

echo
echo "=== C. Certificat Let's Encrypt ==============================="
if certbot certificates 2>/dev/null | grep -q "Certificate Name: $DOMAIN"; then
  certbot delete --cert-name "$DOMAIN" --non-interactive \
    || ko "suppression du certificat (le renouvellement échouera tous les mois)."
  echo "OK — certificat supprimé, plus de renouvellement à vide."
else
  echo "OK — aucun certificat $DOMAIN, rien à faire."
fi

echo
echo "=== D. Vérification ==========================================="
ERR=0
chk() { printf '  %-46s %-6s' "$1" "$3"; if [ "$2" = "$3" ]; then echo "✅"; else echo "❌ (attendu $2)"; ERR=1; fi; }
chk "mcp-projea2 /health (doit vivre)" "200" "$(curl -s -o /dev/null -w '%{http_code}' https://mcp-projea2.twinl.fr/health)"
chk "mcp-iafec  /health (doit vivre)"  "200" "$(curl -s -o /dev/null -w '%{http_code}' https://mcp-iafec.twinl.fr/health)"
chk "projea2.twinl.fr (appli, doit vivre)" "200" "$(curl -s -o /dev/null -w '%{http_code}' -L https://projea2.twinl.fr)"

echo "  --- la couche données de twinl doit être INTACTE ---"
chk "user projea_readonly conservé" "1" "$(mysql --skip-column-names --batch -e "SELECT COUNT(*) FROM mysql.user WHERE User='projea_readonly'" 2>/dev/null)"
NBV=$(mysql --skip-column-names --batch -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='twinl_readonly' AND table_type='VIEW'" 2>/dev/null)
printf '  %-46s %-6s' "vues de twinl_readonly conservées" "$NBV"
if [ -n "$NBV" ] && [ "$NBV" -gt 0 ]; then echo "✅"; else echo "❌ (attendu > 0)"; ERR=1; fi

[ "$ERR" -eq 0 ] || ko "un contrôle est au rouge (voir ci-dessus)."

echo
echo "==============================================================="
if [ "$CHANGED" = "1" ]; then
  echo "✅ mcp-projea RETIRÉ de la façade HTTP. Couche données intacte."
else
  echo "✅ Rien à retirer — état déjà conforme."
fi
echo
echo "   Restent, en console web :"
echo "   · claude.ai → Settings → Connectors → supprimer « mcp-projea »"
echo "   · WorkOS    → Connect → Configuration → Resource Indicators"
echo "                 retirer https://mcp-projea.twinl.fr/mcp"
echo "   · OVH       → l'entrée DNS mcp-projea peut rester (inoffensive) ou partir"
echo "==============================================================="

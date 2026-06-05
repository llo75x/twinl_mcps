# Reprise — Déploiement MCP HTTPS distant (TERMINÉ le 6 juin 2026)

> **Document frozen.** Le déploiement est terminé. Conservé pour traçabilité du passage desktop →
> laptop et des décisions prises. Pour réinstaller from scratch : voir [`INSTALL_PROCEDURE_HTTPS.md`](INSTALL_PROCEDURE_HTTPS.md).
> Pour les pièges rencontrés et corrigés en cours de route : [`PITFALLS.md`](PITFALLS.md) §12-15.

## Statut final

```
Phase 1  Code & config .................................. ✅ FAIT (commité, 887d1ed)
Phase 2  WorkOS (DCR+CIMD, RI×4 avec /mcp, Sign-up off, invite) ✅ FAIT
Phase 3  DNS (mcp-iafec/mcp-projea → 54.38.35.104) ...... ✅ FAIT & vérifié
Phase 4  Conteneurs sur le VPS .......................... ✅ FAIT & validé (healthy)
Phase 5  Apache + TLS (certbot) ......................... ✅ FAIT (certs émis, vhost actif)
Phase 6  Connecteur claude.ai (mcp-iafec + mcp-projea) .. ✅ FAIT (connectés via DCR)
```

## Corrections au plan initial appliquées le 6 juin 2026

Trois écarts entre la procédure prévue (cf. doc original ci-dessous) et la réalité observée :

1. **DCR obligatoire** : claude.ai ne supporte pas encore CIMD. L'erreur `application_not_found`
   à la 1ère tentative venait du fait que CIMD était seul activé. Fix : activer DCR aussi
   dans WorkOS Connect → Configuration → MCP Auth.
2. **Resource Indicators doivent inclure `/mcp`** : le serveur annonce `resource: ".../mcp"` dans
   son `/.well-known/oauth-protected-resource/mcp`, donc claude.ai envoie cette string-là dans
   le param `resource` OAuth. Sans le matching exact côté WorkOS → `invalid_target`. Fix : ajouter
   `https://mcp-iafec.twinl.fr/mcp` et `https://mcp-projea.twinl.fr/mcp` (les versions sans `/mcp`
   gardées en plus, sans risque).
3. **Connecteur claude.ai en état corrompu après échec OAuth** : un client_id stale est conservé
   localement par claude.ai. Fix : **supprimer puis recréer** le connecteur depuis Personnaliser →
   Connecteurs (kebab → Supprimer rouge → confirmer → Ajouter un connecteur personnalisé).

Ces 3 corrections sont reportées dans [`PITFALLS.md`](PITFALLS.md) §12-15 et appliquées
dans [`INSTALL_PROCEDURE_HTTPS.md`](INSTALL_PROCEDURE_HTTPS.md) phase 2 + section pièges.

---

## État au moment du passage desktop → laptop (5 juin 2026, conservé pour historique)

```
Phase 1  Code & config .................................. ✅ FAIT (commité)
Phase 2  WorkOS : compte + domaine récupéré ............. 🟡 PARTIEL (voir ci-dessous)
Phase 3  DNS (mcp-iafec/mcp-projea → 54.38.35.104) ...... ✅ FAIT & vérifié
Phase 4  Conteneurs sur le VPS .......................... ✅ FAIT & validé (healthy)
Phase 5  Apache + TLS (certbot) ......................... ⬜ À FAIRE (toi, root) ← reprise ici
Phase 6  Connecteur claude.ai + login OAuth ............. ⬜ À FAIRE (toi, navigateur)
```

## Faits clés à recharger en mémoire

| Élément | Valeur |
|---|---|
| `AUTHKIT_DOMAIN` | `https://royal-lagoon-55-staging.authkit.app` (WorkOS, env **Staging**, org « TwinL ») |
| Sous-domaines | `mcp-iafec.twinl.fr` / `mcp-projea.twinl.fr` → `54.38.35.104` (DNS OK) |
| VPS | `54.38.35.104`, SSH port 16180. Alias `vps` (lolo/sudo, **sudo à mot de passe** — voir 1Password) ; `vps-ethan` (docker, owner `/opt`) |
| Déploiement | `/opt/twinl_mcps/mcps` (transféré par scp, **pas** par git clone) |
| Conteneurs | `mcp-iafec` (127.0.0.1:8081→8080), `mcp-projea` (127.0.0.1:8082→8080), **healthy** |
| Secrets | `/opt/twinl_mcps/mcps/iafec.env` + `projea.env` (chmod 600, **sur le VPS uniquement**, jamais commités). Passwords RO aussi dans 1Password + les `*.local.sql` des repos iafec/projea |
| Versions figées | fastmcp 2.14.7, sqlglot 26.33.0, PyMySQL 1.1.1 |

## Ce qui a été validé en Phase 4 (ne pas refaire)

- Build Docker OK ; imports sensibles à la version (`AuthKitProvider`, `get_access_token`, `stateless_http`) **présents**.
- Conteneurs **healthy** ; `GET /health` = `{"status":"ok"}` → **MariaDB joignable** via `host.docker.internal:host-gateway`, user/pass/base RO corrects.
- OAuth correctement annoncé (testé sur `127.0.0.1:8081`) :
  - `GET /mcp` → **401** + `WWW-Authenticate: ... resource_metadata="https://mcp-iafec.twinl.fr/.well-known/oauth-protected-resource/mcp"`
  - `/.well-known/oauth-protected-resource/mcp` → 200 (resource + authorization_servers AuthKit)
  - `/.well-known/oauth-authorization-server` → 200 (S256, jwks_uri, endpoints AuthKit)
- ⚠️ URL de connecteur = `https://mcp-*.twinl.fr/mcp` **sans slash final** (`/mcp/` fait un 307).

## Prérequis reprise sur le laptop

1. `git pull` dans `twinl_mcps` (récupère tout le code + ce doc).
2. **Accès SSH** : le laptop doit avoir ses clés autorisées pour `vps` et `vps-ethan` (clés par poste,
   cf. `iafec/OPS.md` §1.2). Vérifier : `ssh vps "whoami"` → `lolo`, `ssh vps-ethan "whoami"` → `ethan`.
3. Aucun secret à recréer : les `.env` sont déjà sur le VPS.

## ÉTAPE DE REPRISE — Phase 5 (toi, root sur `vps`)

`ssh vps`, puis (mot de passe sudo de lolo — voir 1Password) :

```bash
sudo a2enmod proxy proxy_http ssl headers rewrite
sudo certbot certonly --apache -d mcp-iafec.twinl.fr     # cert séparé (mon vhost = 2 chemins distincts)
sudo certbot certonly --apache -d mcp-projea.twinl.fr
sudo cp /opt/twinl_mcps/mcps/deploy/apache-mcp.conf.example /etc/apache2/sites-available/mcp.conf
sudo a2ensite mcp.conf
sudo apachectl configtest        # doit dire "Syntax OK" — sinon NE PAS reload, copier l'erreur à l'agent
sudo systemctl reload apache2
```

## Puis — vérif publique (l'agent peut la faire, sans sudo)

```bash
curl -s https://mcp-iafec.twinl.fr/.well-known/oauth-protected-resource/mcp | jq .
curl -s -o /dev/null -w "%{http_code}\n" https://mcp-iafec.twinl.fr/mcp        # attendu 401
curl -s -H 'Accept-Encoding: gzip' -I https://mcp-iafec.twinl.fr/mcp | grep -i content-encoding || echo "pas de gzip OK"
```

## Puis — Phase 2 WorkOS à finaliser (toi, navigateur) — sinon le login OAuth échoue

Dashboard WorkOS (org TwinL, env Staging) :
- **Connect → Configuration** : ajouter les **Resource Indicators** `https://mcp-iafec.twinl.fr` et
  `https://mcp-projea.twinl.fr` (**indispensable** : sans ça l'audience du jeton ne matchera pas) ;
  activer **CIMD** (et laisser DCR off — DCR marche aussi mais crée des clients).
- Désactiver le toggle **« Sign up »** + **Users → Invitations → inviter ton email**.

## Puis — Phase 6 (toi, navigateur claude.ai)

claude.ai → Settings → Connectors → Add custom connector → `https://mcp-iafec.twinl.fr/mcp` → login
OAuth WorkOS (ton email) → tester. Répéter avec `…/mcp-projea…`.

## Commandes diagnostic utiles

```bash
ssh vps-ethan "cd /opt/twinl_mcps/mcps && docker compose ps"
ssh vps-ethan "cd /opt/twinl_mcps/mcps && docker compose logs --tail=40 mcp-iafec"
ssh vps-ethan "cd /opt/twinl_mcps/mcps && docker compose restart"   # si besoin
```

## Limites / notes

- **Environnement WorkOS = Staging.** Suffisant pour tout faire marcher. Passage en *Production* (domaine
  propre) = étape ultérieure optionnelle (faudra mettre à jour `AUTHKIT_DOMAIN` dans les `.env` du VPS).
- Lignes `# [FASTMCP-API]` de `server.py` = points sensibles à la version (déjà validés en 2.14.7).
- MariaDB toujours bind `0.0.0.0:3306` (TODO sécu `iafec/OPS.md` §2.4) — non aggravé par ce déploiement.

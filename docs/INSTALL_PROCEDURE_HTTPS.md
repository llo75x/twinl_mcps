# Procédure — Déployer un MCP read-only en serveur HTTPS distant (claude.ai web)

Procédure pour exposer une base miroir MariaDB du VPS à **claude.ai (web)** via un serveur MCP
**distant** (transport Streamable HTTP, OAuth 2.1). Complète [`INSTALL_PROCEDURE.md`](INSTALL_PROCEDURE.md)
(qui ne couvre que le mode **stdio local**). Réutilisable pour iafec, projea et tout futur MCP.

> **Différence avec le mode stdio local** : ici le serveur tourne en **conteneur sur le VPS**, est
> accessible **depuis n'importe où** (multi-machine, claude.ai web), et l'accès est gardé par **OAuth**.
> Le mode stdio reste valable pour Claude Desktop/Code en local — les deux peuvent coexister.

## Lecture préalable

- [`ARCHITECTURE.md`](ARCHITECTURE.md) §3 (triple-couche RO) et §7 (stdio vs HTTPS distant).
- [`PITFALLS.md`](PITFALLS.md) §5 (le harness bloque les actions prod → c'est l'utilisateur qui les lance).

## Architecture

```
claude.ai (web)
   │  OAuth 2.1 (CIMD + PKCE S256) ─────────► WorkOS AuthKit  (invite-only ; ne voit pas les données)
   │  Streamable HTTP (Bearer JWT)
   ▼
Apache2 (façade VPS, TLS certbot)   mcp-iafec.twinl.fr / mcp-projea.twinl.fr
   │  ProxyPass 127.0.0.1:808x  (no-gzip, flushpackets, stateless)
   ▼
Conteneur FastMCP  (server.py, AuthKitProvider valide le JWT, garde SELECT-only sqlglot)
   │  pymysql, SSCursor, plafond lignes/octets
   ▼
MariaDB (hôte)  →  user <nom>_readonly  →  DB miroir <base>_readonly  →  vues DEFINER filtrées
```

L'OAuth est une **4ᵉ couche** au-dessus du modèle triple-couche read-only existant. **Aucune touche
à la couche données** : on réutilise les users `*_readonly`, DB miroirs et vues déjà en place.

## Prérequis

| Prérequis | Vérification |
|---|---|
| Users `*_readonly` + DB miroirs déjà créés (procédure stdio) | `SHOW GRANTS FOR '<nom>_readonly'@'%'` |
| SSH `vps` (lolo/sudo) pour Apache/certbot ; `vps-ethan` (docker) pour le déploiement conteneur | cf. [`PITFALLS.md`](PITFALLS.md) §4 |
| Compte WorkOS (AuthKit) | dashboard accessible |
| DNS de `twinl.fr` (BIND sur le VPS) modifiable | — |
| Docker + compose sur le VPS | `docker compose version` |

## Vue d'ensemble — 6 phases

```
Phase 1  Code & config (dans ce repo)                       [Claude]
Phase 2  WorkOS AuthKit : CIMD + invite-only Laurent         [utilisateur]
Phase 3  DNS : 2 sous-domaines → IP du VPS                    [utilisateur]
Phase 4  Conteneurs : build + up sur le VPS                   [utilisateur, ethan/docker]
Phase 5  Apache : vhosts + TLS certbot + reload               [utilisateur, lolo/sudo]
Phase 6  claude.ai : ajouter les connecteurs + tester         [utilisateur]
```

> **Qui fait quoi** ([`PITFALLS.md`](PITFALLS.md) §5) : Claude écrit le code/config/doc et fait de
> l'inspection SSH **read-only**. Les commandes qui **modifient la prod** (WorkOS, DNS, docker, Apache,
> certbot) sont **lancées par l'utilisateur** (blocage harness + règle d'autorité `iafec/OPS.md` §6.1).

---

## Phase 1 — Code & config  *(Claude — fait)*

Fichiers créés dans `mcps/` :

| Fichier | Rôle |
|---|---|
| `server/server.py` | Serveur FastMCP générique (1 instance = 1 base, paramétré par env) |
| `server/requirements.txt` | `fastmcp`, `pymysql`, `sqlglot` |
| `server/Dockerfile` | Image `python:3.12-slim`, user non-root |
| `server/.env.example` | Modèle d'env (à copier en `iafec.env` / `projea.env` **sur le VPS**) |
| `docker-compose.yml` | 2 services, bind `127.0.0.1`, réseau dédié, healthcheck, rotation logs |
| `deploy/apache-mcp.conf.example` | Vhosts Apache (streaming, no-gzip, proxy) |

Points de conception clés (détaillés dans le code) : garde SELECT-only **fail-closed** par AST sqlglot
(couche 3), curseur **non-bufferisé** + double plafond lignes/octets, connexion **par appel** + retry,
logs **anonymisés** (jamais de données ni de littéraux), transport **stateless** (cf. piège prefork ci-dessous).

---

## Phase 2 — WorkOS AuthKit  *(utilisateur)*

1. Créer un projet **AuthKit** (https://dashboard.workos.com).
2. **Connect → Configuration** :
   - **Activer CIMD** (*Client ID Metadata Document*) → mécanisme principal, **zéro client orphelin**.
     Laisser **DCR désactivé** (ne l'activer qu'en secours pour un futur client ne supportant pas CIMD).
   - **MCP resource indicators** : ajouter les 2 URLs publiques exactes (celles loggées au démarrage des
     conteneurs en phase 4, normalement `https://mcp-iafec.twinl.fr` et `https://mcp-projea.twinl.fr`).
3. **Restreindre l'accès (gate le plus précoce)** :
   - Section authentication → **désactiver le toggle « Sign up »** (instance invite-only).
   - **Users → Invites** → inviter **uniquement l'email de Laurent**.
4. Noter le **`AUTHKIT_DOMAIN`** (ex. `https://xxxx.authkit.app`) — il ira dans les `.env`.

> Le callback OAuth de Claude est `https://claude.ai/api/mcp/auth_callback` (géré automatiquement par
> le flux ; rien à configurer côté serveur au-delà de la métadonnée servie par FastMCP).

---

## Phase 3 — DNS  *(utilisateur)*

Dans la zone `twinl.fr` du BIND (VPS) :

```
mcp-iafec   IN  A   54.38.35.104
mcp-projea  IN  A   54.38.35.104
```

Puis incrémenter le serial de la zone et recharger (`rndc reload twinl.fr`). Vérifier :
`dig +short mcp-iafec.twinl.fr` → `54.38.35.104`.

---

## Phase 4 — Conteneurs  *(utilisateur — compte `vps-ethan` / docker)*

```bash
# Récupérer le repo sur le VPS
sudo mkdir -p /opt/twinl_mcps && sudo chown ethan:ethan /opt/twinl_mcps
git clone git@github.com:llo75x/twinl_mcps.git /opt/twinl_mcps   # ou git pull si déjà cloné
cd /opt/twinl_mcps/mcps

# Créer les 2 fichiers d'env (JAMAIS commités), à partir du modèle
cp server/.env.example iafec.env
cp server/.env.example projea.env
# → éditer iafec.env et projea.env : MCP_DB_USER/PASS/NAME, AUTHKIT_DOMAIN, BASE_URL (cf. ci-dessous)
chmod 600 iafec.env projea.env

# Build + démarrage
docker compose build
docker compose up -d
docker compose ps          # attendre "healthy"
docker compose logs --tail=30 mcp-iafec   # noter l'URL de ressource loggée (→ phase 2)
```

Valeurs distinctes par fichier :

| Variable | `iafec.env` | `projea.env` |
|---|---|---|
| `MCP_SERVER_NAME` | `mcp-iafec-readonly` | `mcp-projea-readonly` |
| `MCP_DB_USER` | `iaFEC_readonly` | `projea_readonly` |
| `MCP_DB_NAME` | `iafec_readonly` | `twinl_readonly` |
| `MCP_DB_PASS` | (1Password / `*.local.sql`) | (1Password / `*.local.sql`) |
| `BASE_URL` | `https://mcp-iafec.twinl.fr` | `https://mcp-projea.twinl.fr` |
| `MCP_PORT` | `8080` (interne, ne pas changer) | `8080` (interne, ne pas changer) |
| `AUTHKIT_DOMAIN` | identique aux deux | identique aux deux |

> `MCP_PORT` reste **8080 dans les deux conteneurs** ; la différenciation se fait par le port publié
> côté hôte dans `docker-compose.yml` (`127.0.0.1:8081` iafec, `127.0.0.1:8082` projea).

> **Figer les versions** : au 1er build, `docker compose exec mcp-iafec pip freeze | grep -Ei 'fastmcp|sqlglot'`
> et reporter les versions exactes dans `server/requirements.txt` (convention `iafec/OPS.md` §4.1), puis commit.

---

## Phase 5 — Apache + TLS  *(utilisateur — compte `vps` / `lolo` + sudo)*

```bash
# Modules requis (idempotent)
sudo a2enmod proxy proxy_http ssl headers rewrite

# Déposer les vhosts
sudo cp /opt/twinl_mcps/mcps/deploy/apache-mcp.conf.example /etc/apache2/sites-available/mcp.conf
sudo a2ensite mcp.conf

# Certificats (auto-injection SSL) — le port 80 doit déjà router vers Apache
sudo certbot --apache -d mcp-iafec.twinl.fr -d mcp-projea.twinl.fr

# Vérifier puis recharger
sudo apachectl configtest        # → "Syntax OK"
sudo systemctl reload apache2
```

> Si `certbot --apache` réécrit le vhost, vérifier que les directives **`SetEnv no-gzip 1`**,
> **`proxy-sendchunked`**, **`flushpackets=on`** et **`ProxyTimeout`** sont toujours présentes (certbot
> ne touche normalement qu'au bloc SSL, mais contrôler).

---

## Phase 6 — claude.ai  *(utilisateur)*

1. claude.ai → **Settings → Connectors → Add custom connector**.
2. URL : `https://mcp-iafec.twinl.fr/mcp` → dérouler le flux OAuth (login WorkOS avec l'email de Laurent).
3. Répéter avec `https://mcp-projea.twinl.fr/mcp`.
4. Tester : *« Via le connecteur iafec, combien de groupes et d'écritures comptables ? »* → Claude appelle
   `mysql_query(...)` (≈ 7 groupes / 611k écritures attendus).

---

## Vérification end-to-end

```bash
# Métadonnée OAuth (publique)
curl -s https://mcp-iafec.twinl.fr/.well-known/oauth-protected-resource | jq .
# /mcp non authentifié → 401 + WWW-Authenticate
curl -s -o /dev/null -w "%{http_code}\n" https://mcp-iafec.twinl.fr/mcp
# Health (vérifie aussi MariaDB)
curl -s https://mcp-iafec.twinl.fr/health        # {"status":"ok"}
# Pas de compression (sinon streaming cassé)
curl -s -H 'Accept-Encoding: gzip' -I https://mcp-iafec.twinl.fr/mcp | grep -i content-encoding || echo "pas de gzip — OK"
```

Tests fonctionnels (via claude.ai ou un client FastMCP avec OAuth) :

- **Read-only / garde AST** : un `SELECT` passe ; sont **rejetés** : `INSERT/UPDATE/DELETE`, write commenté
  (`/* x */ DELETE …`), requête empilée (`SELECT 1; DELETE …`), `SELECT … INTO OUTFILE` ; un CTE
  `WITH … SELECT` **passe**.
- **Plafond** : `SELECT * FROM iafec_readonly.accounting_entries` → ≤ `MCP_MAX_ROWS` lignes + marqueur
  « tronqué » ; RAM conteneur stable (`docker stats`).
- **Isolation** : sous le user RO, `SHOW DATABASES` ne liste que `<base>_readonly` + `information_schema`.
- **Auth** : autre email → refusé par WorkOS ; token expiré / mauvais `aud` → 401.
- **CIMD** : après plusieurs (re)connexions, **aucun nouveau client** dans le dashboard WorkOS.
- **Prefork** : `sudo apachectl status` → aucune connexion `/mcp` tenue au repos (preuve du mode stateless).
- **Résilience** : redémarrer MariaDB → la requête suivante se reconnecte, le conteneur ne crashe pas
  (`docker compose ps` reste up).
- **Logs sans données** : `docker compose logs` → SQL anonymisé + nb lignes uniquement, jamais de
  résultats ni de littéraux.

---

## Pièges spécifiques au mode HTTPS distant

1. **Apache en `mpm_prefork`** (imposé par mod_php des autres applis du VPS). Une connexion SSE
   persistante retiendrait un **process entier**. **Parade** : FastMCP en `stateless_http` (pas de flux
   long-vécu) + `ProxyTimeout` borné. **Ne PAS** basculer le MPM global vers `event` (casserait mod_php).
2. **`mod_deflate` casse le streaming** : compression désactivée explicitement sur les vhosts
   (`SetEnv no-gzip 1` + `RequestHeader unset Accept-Encoding`).
3. **CIMD vs DCR** : utiliser **CIMD** (pas de client orphelin). Le DCR enregistrerait un client à chaque
   connexion fraîche → sprawl. DCR gardé en réserve (toggle WorkOS) pour un futur client non-CIMD.
4. **Secrets** : `iafec.env` / `projea.env` créés **sur le VPS uniquement**, `chmod 600`, gitignored.
   Ne jamais committer les passwords (règle `iafec/OPS.md` §3.5).
5. **MariaDB sur l'hôte** : le conteneur l'atteint via `host.docker.internal:host-gateway`, **pas** en
   host-networking. (Rappel TODO sécu `iafec/OPS.md` §2.4 : MariaDB est encore bind `0.0.0.0:3306`.)
6. **Versions FastMCP** : les lignes `# [FASTMCP-API]` de `server.py` sont sensibles à la version ; le
   build (phase 4) valide l'API. Figer les versions exactes après le 1er build réussi.
7. **Rafraîchir les vues après migration de colonnes** : inchangé — voir `iafec/OPS.md` §6.3.bis. Le
   serveur HTTPS lit les **mêmes** vues miroir que le mode stdio.

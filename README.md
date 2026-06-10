# twinl_mcps — Outillage et doc des MCPs MariaDB

Repo central pour la gestion des serveurs **MCP read-only** qui exposent les bases MariaDB
du VPS OVH (hostname `vps-51f1b5c1.vps.ovh.net`) à Claude Desktop. Toutes les bases vivent sur la
même instance MariaDB ; chaque MCP expose une base via un user dédié, une DB miroir et des
vues filtrées (cf. `docs/ARCHITECTURE.md`).

## Pourquoi ce repo

Avant ce repo, chaque MCP (iafec, projea) avait sa propre copie du script Python de vérification,
de la procédure d'install et des pièges connus. Au-delà de 2 MCPs, la duplication devient
coûteuse — d'où ce repo qui centralise :

- la **procédure d'installation** (réutilisable pour tout nouveau MCP)
- les **pièges et leçons apprises** (notamment le piège majeur de réécriture de `claude_desktop_config.json` par Claude Desktop)
- l'**architecture sécurité** triple-couche
- l'**historique de session** (frozen pour traçabilité)

## Inventaire des MCPs actifs

| MCP | Base source | Repo data | Skill doc | Statut |
|---|---|---|---|---|
| `iafec-readonly` | `iafec` (MariaDB) | [`llo75x/iafec`](https://github.com/llo75x/iafec) | `skill_MCP_iafec.md` dans le repo iafec | ✅ Opérationnel |
| `MCP_Projea` | `twinl` (MariaDB) | [`llo75x/projea`](https://github.com/llo75x/projea) | `skill_MCP_projea.md` dans le repo projea | ✅ Opérationnel |

Conventions :
- **Nom user MariaDB** : `<nom>_readonly` (ex. `iaFEC_readonly`, `projea_readonly`).
- **DB miroir** : `<nom_base_source>_readonly` (ex. `iafec_readonly`, `twinl_readonly`).
- **Nom serveur MCP** (dans `claude_desktop_config.json`) : libre — `iafec-readonly` (lowercase+tiret), `MCP_Projea` (Pascal+underscore). Pas de convention unifiée encore (cf. §Roadmap).

## Index de la doc

| Doc | Contenu |
|---|---|
| [`docs/INSTALL_PROCEDURE.md`](docs/INSTALL_PROCEDURE.md) | Procédure détaillée d'installation d'un MCP en **stdio local** (5 phases : DDL côté VPS, exécution root, vérif + config Claude Desktop, activation, test). |
| [`docs/INSTALL_PROCEDURE_HTTPS.md`](docs/INSTALL_PROCEDURE_HTTPS.md) | Procédure de déploiement d'un MCP en **serveur HTTPS distant** pour **claude.ai web** (6 phases : code, WorkOS OAuth, DNS, conteneurs, Apache/TLS, connecteurs). Code dans [`mcps/`](mcps/). |
| [`docs/RESUME_DEPLOIEMENT_HTTPS.md`](docs/RESUME_DEPLOIEMENT_HTTPS.md) | **État/reprise** du déploiement HTTPS en cours (phases 1-4 faites, reprise en phase 5 Apache/TLS). Document de passation desktop ↔ laptop. |
| [`docs/PITFALLS.md`](docs/PITFALLS.md) | Pièges à connaître absolument : réécriture de `claude_desktop_config.json` par CD, fichier absent quand CD est quitté, `iaFEC_admin` sans `CREATE USER`, `vps-ethan` vs `vps`, blocage harness sur passwords en clair. |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Modèle sécurité triple-couche, choix DB miroir + vues `SQL SECURITY DEFINER`, statique vs procédure dynamique. |
| [`docs/SESSION_HISTORY_2026-05.md`](docs/SESSION_HISTORY_2026-05.md) | Synthèse complète de la session 27-29 mai 2026 (création des 2 MCPs, refactor projea en repo autonome, création de ce repo). Historique frozen. |

## Roadmap

### Étape 1 (en cours) — Centralisation documentaire

- ✅ Procédure d'installation centralisée
- ✅ Pièges centralisés
- ✅ Architecture documentée
- ✅ Historique de session frozen
- ⬜ Référencer ce repo dans `iafec/OPS.md` et `projea/README.md` pour pointer vers la procédure

### Étape 2 (au 3e MCP) — Toolkit Python générique

Refactor de `mcp_*_verify.py` (iafec + projea ≈ identiques à 90 %) en un toolkit paramétré :

```
toolkit/mcp_toolkit/      # CLI Python (verify, config writer générique)
mcps/<nom>/config.toml    # Déclaration d'un MCP (name, db, user, exclusions…)
mcps/<nom>/setup.sql      # DDL spécifique
```

Décision déclenchée par le 3e MCP — pas avant. Le pattern sera alors évident.

### Étape 2bis (✅ déployé le 6 juin 2026) — MCP HTTPS distant pour claude.ai web

Déclenchée par le besoin d'interroger les bases depuis **claude.ai (web)** (multi-machine, sans poste
local). Serveur FastMCP en conteneur sur le VPS, derrière OAuth 2.1 (WorkOS AuthKit), fronté par Apache.

- ✅ Code & config (`mcps/server/`, `mcps/docker-compose.yml`, `mcps/deploy/`)
- ✅ Procédure documentée ([`docs/INSTALL_PROCEDURE_HTTPS.md`](docs/INSTALL_PROCEDURE_HTTPS.md))
- ✅ Déploiement prod **terminé** — WorkOS, DNS, conteneurs, Apache/TLS, connecteurs `mcp-iafec` + `mcp-projea` connectés sur claude.ai (cf. [`docs/RESUME_DEPLOIEMENT_HTTPS.md`](docs/RESUME_DEPLOIEMENT_HTTPS.md))

### Étape 2ter — Règles métier livrées via le champ MCP `instructions` (une source, lue partout)

Chaque MCP délivre ses règles métier + data model via le **champ standard `instructions`** du serveur
(`server.py` → `FastMCP(instructions=…)`), donc lu **automatiquement** par tout client (claude.ai web,
Claude Desktop, Cowork, Claude Code). Une seule source par instance, dans [`mcps/instructions/`](mcps/instructions/)
(`projea.md`, `iafec.md`), montée en lecture seule dans le conteneur. Objectif : supprimer la divergence
cowork/web et le serveur stdio tiers (`@benborla29/mcp-server-mysql`), au profit du **connecteur HTTPS
unique** ajouté sur chaque surface.

- ✅ Mécanisme dans `server.py` + montage `docker-compose.yml`
- ✅ Règles Projea (`mcps/instructions/projea.md`) — merge `skill_MCP_projea.md` + synthèse cowork emailing/ESN
- ✅ Règles iafec (`mcps/instructions/iafec.md`) — repris de `skill_MCP_iafec.md`
- ⬜ Redéploiement conteneurs `mcp-projea` + `mcp-iafec` sur le VPS (pour activer la livraison)
- ✅ Source de vérité = `mcps/instructions/` (décision A) ; `skill_MCP_*.md` des repos data réduits à des pointeurs
- ⬜ Migration Desktop/Cowork/Claude Code vers le connecteur HTTPS (retrait du serveur tiers stdio)

### Étape 3 — Au-delà

Selon les besoins : rotation automatisée des passwords, tests automatisés, conversion DXT (extensions
Claude Desktop empaquetées), toolkit Python générique stdio…

## Comment utiliser ce repo

### Pour installer un nouveau MCP

1. Lire [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) pour comprendre le modèle.
2. Suivre [`docs/INSTALL_PROCEDURE.md`](docs/INSTALL_PROCEDURE.md) étape par étape.
3. **Lire [`docs/PITFALLS.md`](docs/PITFALLS.md) AVANT de toucher à `claude_desktop_config.json`** — ça t'évitera les pièges qui ont coûté du temps lors de la première session.

### Pour diagnostiquer un MCP qui ne marche plus

1. [`docs/PITFALLS.md`](docs/PITFALLS.md) §1 (clobber) couvre la cause la plus probable.
2. Vérifier `%APPDATA%\Claude\logs\mcp-server-<nom>.log` et `cowork_host_loop_debug.log`.

### Pour ajouter une vue à un MCP existant

Édit du `.sql` dans le repo data correspondant, ré-exécution en root MariaDB. Documenter la nouvelle vue dans le skill doc associé.

## Hors scope de ce repo

- **Logique métier** des bases exposées → reste dans les repos data (`iafec/skill_FEC.md`, `projea/Description...txt`).
- **Cartographies skill_MCP_*.md** pour Claude → restent dans les repos data (couplées à la sémantique de la donnée).
- **Code applicatif** des projets exposés (iafec backend, MS Access Projea) → leurs repos respectifs.

Ce repo gère **la mécanique d'accès** (user MariaDB, vues, MCP server, config Claude Desktop), pas la donnée elle-même.

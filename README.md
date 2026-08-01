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

| MCP | Base source | Repo data | Règles métier (instructions) | Statut |
|---|---|---|---|---|
| `mcp-iafec` | `iafec` (MariaDB) | [`llo75x/iafec`](https://github.com/llo75x/iafec) | [`mcps/instructions/iafec.md`](mcps/instructions/iafec.md) | ✅ Opérationnel |
| `mcp-projea` | `twinl` (MariaDB) — **legacy Projea1** | [`llo75x/projea`](https://github.com/llo75x/projea) | [`mcps/instructions/projea.md`](mcps/instructions/projea.md) | ⏳ **À fermer** quand projea2 est recetté ([phase 7](docs/RUNBOOK_MCP_PROJEA2.md#phase-7--fermer-mcp-projea-une-fois-projea2-recetté)) |
| `mcp-projea2` | `projea2` (MariaDB) — **CRM de référence** | [`llo75x/projea2`](https://github.com/llo75x/projea2) | [`mcps/instructions/projea2.md`](mcps/instructions/projea2.md) | 🟢 **En ligne** sur `https://mcp-projea2.twinl.fr` ; reste WorkOS + connecteur claude.ai ([état](docs/RUNBOOK_MCP_PROJEA2.md#état-du-déploiement-au-2026-08-01)) |

> `twinl` et `projea2` portent **la même donnée métier** dans deux modèles différents : `projea2`
> est la réécriture, et depuis la **bascule définitive du 2026-07-31** c'est elle qui fait référence.
> `mcp-projea2` **remplace** donc `mcp-projea`, il ne s'y ajoute pas : deux connecteurs concurrents
> sur le même sujet inviteraient à mélanger deux modèles incompatibles (entiers `tb_CodeStatut` d'un
> côté, codes texte `reference_values` de l'autre). Recouvrement le temps de la recette seulement.
>
> Fermer le MCP legacy ne démonte **pas** sa couche données : le user `projea_readonly` et la DB
> miroir `twinl_readonly` restent nécessaires au pipeline de migration de PROJEA2, qui est rejouable.

Conventions :
- **Nom user MariaDB** : `<nom>_readonly` (ex. `iaFEC_readonly`, `projea_readonly`) — **exception `projea2` : `projea2_mcp`**, parce que l'application PROJEA2 possède déjà un compte `projea2_readonly` (cf. [runbook](docs/RUNBOOK_MCP_PROJEA2.md)). Vérifier qu'un nom est libre avant de l'appliquer.
- **DB miroir** : `<nom_base_source>_readonly` (ex. `iafec_readonly`, `twinl_readonly`, `projea2_readonly`)
  — cette convention-là est tenue pour les trois.
- **Nom serveur MCP** / sous-domaine : `mcp-<nom>` / `mcp-<nom>.twinl.fr`, port hôte `127.0.0.1:808x`
  (iafec `8081`, projea `8082`, projea2 `8083`).

## Index de la doc

| Doc | Contenu |
|---|---|
| [`docs/INSTALL_PROCEDURE.md`](docs/INSTALL_PROCEDURE.md) | Procédure détaillée d'installation d'un MCP en **stdio local** (5 phases : DDL côté VPS, exécution root, vérif + config Claude Desktop, activation, test). |
| [`docs/INSTALL_PROCEDURE_HTTPS.md`](docs/INSTALL_PROCEDURE_HTTPS.md) | Procédure de déploiement d'un MCP en **serveur HTTPS distant** pour **claude.ai web** (6 phases : code, WorkOS OAuth, DNS, conteneurs, Apache/TLS, connecteurs). Code dans [`mcps/`](mcps/). |
| [`docs/RESUME_DEPLOIEMENT_HTTPS.md`](docs/RESUME_DEPLOIEMENT_HTTPS.md) | **État/reprise** du déploiement HTTPS en cours (phases 1-4 faites, reprise en phase 5 Apache/TLS). Document de passation desktop ↔ laptop. |
| [`docs/RUNBOOK_MCP_PROJEA2.md`](docs/RUNBOOK_MCP_PROJEA2.md) | **Runbook du 3ᵉ MCP (`projea2`)** : les commandes exactes à lancer, dans l'ordre (MariaDB root, WorkOS, DNS, conteneur, Apache/TLS, connecteur claude.ai). |
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

### Étape 2 (arbitrée au 3e MCP — juillet 2026) — pas de toolkit, mais le DDL centralisé

Le plan initial était un toolkit Python paramétré par `config.toml` (refactor des `mcp_*_verify.py`).
**Abandonné** : le `verify` stdio n'a plus d'usage depuis que toutes les surfaces passent par le
connecteur HTTPS unique (étape 2ter), et un `server.py` déjà paramétré par env couvre le besoin de
généricité. Ce qui a été retenu du 3e MCP (`projea2`) :

- le **DDL de la base miroir vit désormais ici**, dans [`mcps/sql/`](mcps/sql/) (`projea2_setup.sql`),
  et non plus dans le repo data — c'est de la mécanique d'accès, le périmètre de ce repo ;
- iafec et projea gardent leur DDL historique dans leur repo data (pas de migration rétroactive :
  aucun bénéfice, du risque).

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
(`projea.md`, `iafec.md`, `projea2.md`), montée en lecture seule dans le conteneur. Objectif : supprimer la divergence
cowork/web et le serveur stdio tiers (`@benborla29/mcp-server-mysql`), au profit du **connecteur HTTPS
unique** ajouté sur chaque surface.

- ✅ Mécanisme dans `server.py` + montage `docker-compose.yml`
- ✅ Règles Projea (`mcps/instructions/projea.md`) — merge `skill_MCP_projea.md` + synthèse cowork emailing/ESN
- ✅ Règles iafec (`mcps/instructions/iafec.md`) — repris de `skill_MCP_iafec.md`
- ✅ Règles projea2 (`mcps/instructions/projea2.md`) — écrites depuis les specs v2 + le schéma réel (45 vues exposées)
- ⬜ Redéploiement conteneurs `mcp-projea` + `mcp-iafec` sur le VPS (pour activer la livraison)
- ✅ Source de vérité = `mcps/instructions/` (décision A) ; `skill_MCP_*.md` des repos data réduits à des pointeurs
- ✅ Migration Desktop/Cowork vers le connecteur HTTPS — serveurs stdio tiers `@benborla29/mcp-server-mysql` retirés de `claude_desktop_config.json` (le connecteur HTTPS unique sert désormais toutes les surfaces)

### Étape 3 — Au-delà

Selon les besoins : rotation automatisée des passwords, tests automatisés, conversion DXT (extensions
Claude Desktop empaquetées), toolkit Python générique stdio…

## Comment utiliser ce repo

### Pour installer un nouveau MCP

1. Lire [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) pour comprendre le modèle.
2. Écrire le DDL de la base miroir dans [`mcps/sql/`](mcps/sql/) — partir de
   [`mcps/sql/projea2_setup.sql`](mcps/sql/projea2_setup.sql), le plus récent et le plus complet
   (boucle dynamique + vues à colonnes projetées + purge des vues orphelines + vérifs de fuite).
3. Suivre [`docs/INSTALL_PROCEDURE_HTTPS.md`](docs/INSTALL_PROCEDURE_HTTPS.md) (mode nominal).
   [`docs/INSTALL_PROCEDURE.md`](docs/INSTALL_PROCEDURE.md) ne sert plus que si un client stdio
   local est nécessaire.
4. Écrire ses règles métier dans [`mcps/instructions/`](mcps/instructions/) (avec un bloc `DIGEST`).
5. **Lire [`docs/PITFALLS.md`](docs/PITFALLS.md)** — notamment §5 : les commandes de prod sont
   lancées par l'utilisateur, pas par Claude.

Exemple complet et récent, de bout en bout : [`docs/RUNBOOK_MCP_PROJEA2.md`](docs/RUNBOOK_MCP_PROJEA2.md).

### Pour diagnostiquer un MCP qui ne marche plus

1. [`docs/PITFALLS.md`](docs/PITFALLS.md) §1 (clobber) couvre la cause la plus probable.
2. Vérifier `%APPDATA%\Claude\logs\mcp-server-<nom>.log` et `cowork_host_loop_debug.log`.

### Pour ajouter / rafraîchir une vue d'un MCP existant

- **projea2** : édit de [`mcps/sql/projea2_setup.sql`](mcps/sql/projea2_setup.sql), ré-exécution en
  root MariaDB, puis mise à jour de [`mcps/instructions/projea2.md`](mcps/instructions/projea2.md).
  **À rejouer après chaque migration Alembic** qui ajoute/retire une table ou une colonne : une vue
  `SELECT *` ne suit pas le schéma tout seule.
- **iafec / projea** : édit du `.sql` dans le repo data correspondant, ré-exécution en root MariaDB,
  puis mise à jour du `.md` d'instructions.

## Hors scope de ce repo

- **Logique métier** des bases exposées → reste dans les repos data (`iafec/skill_FEC.md`, `projea/Description...txt`).
- **Cartographies skill_MCP_*.md** pour Claude → restent dans les repos data (couplées à la sémantique de la donnée).
- **Code applicatif** des projets exposés (iafec backend, MS Access Projea) → leurs repos respectifs.

Ce repo gère **la mécanique d'accès** (user MariaDB, vues, MCP server, config Claude Desktop), pas la donnée elle-même.

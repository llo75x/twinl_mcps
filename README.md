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
| [`docs/INSTALL_PROCEDURE.md`](docs/INSTALL_PROCEDURE.md) | Procédure détaillée d'installation d'un nouveau MCP (5 phases : DDL côté VPS, exécution root, vérif + config Claude Desktop, activation, test). Réutilisable pour tout nouveau MCP. |
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

### Étape 3 — Au-delà

Selon les besoins : rotation automatisée des passwords, healthcheck, tests, conversion DXT (extensions Claude Desktop empaquetées), passage à un MCP HTTPS distant pour usage depuis claude.ai web…

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

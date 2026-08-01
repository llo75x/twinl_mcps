# mcps/ — Serveur MCP HTTPS distant (iafec + projea2)

Ce dossier contient le **serveur MCP read-only en HTTPS** déployé sur le VPS, qui expose les bases
miroirs `iafec_readonly` et `projea2_readonly` à **claude.ai (web)** via OAuth 2.1 (WorkOS AuthKit).

> Procédure complète de déploiement : [`../docs/INSTALL_PROCEDURE_HTTPS.md`](../docs/INSTALL_PROCEDURE_HTTPS.md).
> Conception (sécurité, choix) : [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).
> Le mode **stdio local** (Claude Desktop) reste documenté dans [`../docs/INSTALL_PROCEDURE.md`](../docs/INSTALL_PROCEDURE.md) — les deux coexistent.

## Contenu

```
mcps/
├── server/
│   ├── server.py          # serveur FastMCP générique (1 instance = 1 base, paramétré par env)
│   ├── requirements.txt    # fastmcp, pymysql, sqlglot
│   ├── Dockerfile          # python:3.12-slim, user non-root
│   └── .env.example        # modèle d'env (→ iafec.env / projea2.env, créés SUR LE VPS)
├── instructions/           # règles métier, UN fichier par instance (monté :ro) + archive/
├── sql/
│   ├── projea2_setup.sql   # DDL de la base miroir projea2_readonly (root MariaDB, idempotent)
│   └── projea2_bootstrap.sh # phase 1 clés en main : DDL + vérifs + écriture de projea2.env
├── docker-compose.yml      # 2 instances, bind 127.0.0.1, réseau dédié, healthcheck
├── deploy/
│   ├── apache-mcp.conf.example   # vhosts Apache (streaming, no-gzip, proxy 127.0.0.1:808x)
│   ├── projea2_apache.sh         # phase 5 : alias :80 → certbot → vhost :443, avec restauration
│   └── projea_close.sh           # phase 7 : retrait de la façade du MCP legacy
└── iafec.env / projea2.env  # secrets, créés sur le VPS, chmod 600, GITIGNORED
```

## Modèle (1 image, 2 instances)

Un seul `server.py`, paramétré par variables d'environnement (`MCP_DB_*`, `AUTHKIT_DOMAIN`, `BASE_URL`,
plafonds…). Deux instances Docker, un sous-domaine et un connecteur claude.ai chacune. Cohérent avec
le modèle « un MCP par base » — et c'est ce qui garantit que les **règles métier ne se mélangent
jamais** entre bases.

| Instance | Nom annoncé | Base miroir | Port hôte | Sous-domaine |
|---|---|---|---|---|
| `mcp-iafec` | `mcp-iafec-readonly` | `iafec_readonly` | `127.0.0.1:8081` | `mcp-iafec.twinl.fr` |
| `mcp-projea2` | **`projea`** | `projea2_readonly` (CRM de référence) | `127.0.0.1:8083` | `mcp-projea2.twinl.fr` |

> **Pourquoi l'instance `mcp-projea2` s'annonce `projea`** (`MCP_SERVER_NAME=projea`, 2026-08-01) —
> ce n'est pas une incohérence à corriger. Le MCP legacy étant fermé, « Projea » n'a plus qu'un
> référent : cette base. Le nom annoncé est ce que le client affiche et ce à quoi l'utilisateur
> s'adresse (« avec projea… ») ; le faire coïncider avec le mot employé évite une question de
> désambiguïsation à chaque requête. Conteneur, base et sous-domaine gardent le `2`, qui dit la
> génération du modèle de données. Le nom apparaît aussi dans les logs et les messages Slack
> d'approbation.

`mcp-projea` (`twinl_readonly`, port 8082) a été **fermé le 2026-08-01**, remplacé par
`mcp-projea2`. Sa couche données MariaDB est **conservée** : le pipeline de migration de PROJEA2
lit encore `twinl_readonly` via `projea_readonly`, et il est rejouable.

> ⚠️ **Un troisième MCP existe, et il n'est PAS dans ce dépôt.**
> `mcp-dataroom.twinl.fr` (conteneur `dataroom-mcp`, `127.0.0.1:5021`) est en ligne depuis le
> 2026-08-01 et vit dans le dépôt **`twinl-dataroom`** — exploitation : son `OPS.md §8`.
>
> Il ne suit **pas** le modèle de ce dépôt, délibérément : il n'expose pas `mysql_query` sur une
> base miroir mais des outils orientés documents, et il applique les **droits de l'utilisateur
> connecté** (jeton WorkOS apparié à `users.email`, puis `permission_service`) au lieu d'une vue
> unique en lecture seule. D'où son hébergement dans le dépôt de l'application, dont il importe le
> code : réécrire ici la résolution des droits de la dataroom aurait divergé au premier changement
> de règle. Il partage en revanche le **même projet WorkOS AuthKit** — même `AUTHKIT_DOMAIN`, même
> annuaire d'invités : une invitation vaut pour les trois MCP.
>
> Son vhost Apache est dans son propre fichier `mcp-dataroom.conf`, pas dans le `mcp.conf` de ce
> dépôt : deux dépôts qui se partagent un fichier de configuration, c'est un déploiement de l'un
> qui casse l'autre.

Déploiement de projea2 **et** fermeture de projea :
[`../docs/RUNBOOK_MCP_PROJEA2.md`](../docs/RUNBOOK_MCP_PROJEA2.md).

## Sécurité — 4 couches (cf. ARCHITECTURE.md §3)

1. Vues `SQL SECURITY DEFINER` (filtrage structurel).
2. `GRANT SELECT` only sur la DB miroir (**rempart dur** : le user RO ne peut rien muter).
3. Garde SELECT-only **fail-closed** par AST `sqlglot`, dans `server.py` (remplace les flags `ALLOW_*`).
4. OAuth 2.1 WorkOS AuthKit (**invite-only**, seul Laurent) + filet allowlist de sujet.

Plus : plafond lignes/octets (anti-saturation), connexion par appel + retry, logs anonymisés.

## Note sur l'ancien plan « toolkit générique »

L'étape 2 envisageait initialement un toolkit Python paramétré par `config.toml` (refactor des
`mcp_*_verify.py`). Le besoin réel a été le **passage en HTTPS distant** (ce serveur). Le refactor du
verify stdio en toolkit reste possible plus tard, mais n'était pas le besoin prioritaire.

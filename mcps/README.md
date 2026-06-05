# mcps/ — Serveur MCP HTTPS distant (iafec + projea)

Ce dossier contient le **serveur MCP read-only en HTTPS** déployé sur le VPS, qui expose les bases
miroirs `iafec_readonly` et `twinl_readonly` à **claude.ai (web)** via OAuth 2.1 (WorkOS AuthKit).

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
│   └── .env.example        # modèle d'env (→ iafec.env / projea.env, créés SUR LE VPS)
├── docker-compose.yml      # 2 instances (iafec, projea), bind 127.0.0.1, réseau dédié, healthcheck
├── deploy/
│   └── apache-mcp.conf.example   # vhosts Apache (streaming, no-gzip, proxy 127.0.0.1:808x)
└── iafec.env / projea.env  # secrets, créés sur le VPS, chmod 600, GITIGNORED (jamais ici)
```

## Modèle (1 image, 2 instances)

Un seul `server.py`, paramétré par variables d'environnement (`MCP_DB_*`, `AUTHKIT_DOMAIN`, `BASE_URL`,
plafonds…). Deux instances Docker (`mcp-iafec`, `mcp-projea`), un sous-domaine chacune
(`mcp-iafec.twinl.fr`, `mcp-projea.twinl.fr`), deux connecteurs dans claude.ai. Cohérent avec le modèle
« un MCP par base ».

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

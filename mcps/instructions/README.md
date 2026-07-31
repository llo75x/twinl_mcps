# Instructions des serveurs MCP (règles métier + data model, par instance)

Un fichier par MCP. Son contenu est délivré au client via le **champ MCP standard `instructions`**
(cf. `server.py` → `FastMCP(instructions=…)`), donc lu **automatiquement** par tout client qui se
connecte au connecteur : claude.ai web, Claude Desktop, Cowork, Claude Code. Une seule source,
lue partout — pas de skill doc à charger en parallèle.

| Fichier | Instance | Base exposée | Monté dans le conteneur | Variable |
|---|---|---|---|---|
| `projea.md` | `mcp-projea` | `twinl` (legacy Projea1) | `/app/instructions.md` (ro) | `MCP_INSTRUCTIONS_FILE` |
| `projea2.md` | `mcp-projea2` | `projea2` (CRM de référence) | `/app/instructions.md` (ro) | `MCP_INSTRUCTIONS_FILE` |
| `iafec.md` | `mcp-iafec` | `iafec` | `/app/instructions.md` (ro) | `MCP_INSTRUCTIONS_FILE` |

> `projea.md` et `projea2.md` décrivent **la même donnée métier dans deux modèles différents**.
> Ne jamais copier une règle de l'un vers l'autre sans la retraduire : dans `twinl` les statuts sont
> des entiers de `tb_CodeStatut`, dans `projea2` ce sont des **codes texte** de `reference_values`.

Le montage est déclaré dans [`../docker-compose.yml`](../docker-compose.yml) (`volumes:`), versionné.
La valeur par défaut de `MCP_INSTRUCTIONS_FILE` est `/app/instructions.md` côté serveur : pas besoin
de toucher aux `.env` secrets du VPS.

## Règles

- **Jamais de secret** ici (ces fichiers sont commités). Que du schéma + règles métier.
- **Par instance, jamais mélangés** : projea ne voit que `projea.md`, projea2 que `projea2.md`,
  iafec que `iafec.md`.
- **Un bloc `<!-- DIGEST -->…<!-- /DIGEST -->`** est obligatoire : claude.ai web n'expose pas le
  champ MCP `instructions` au modèle, et c'est ce bloc que `server.py` injecte dans la
  **description de l'outil** `mysql_query` (canal fiable cross-client). Le reste du fichier est
  servi par l'outil `get_data_model_reference`.
- **Refléter ce que la base miroir expose vraiment** : si le DDL retire une colonne (secret) ou une
  table, le dire dans le `.md` — sinon le modèle la cherchera et échouera.
- **Fail-soft** : un fichier vide ou illisible → le serveur démarre sans instructions (aucune règle
  délivrée), il ne crashe pas.
- **Merge des sources** : ces fichiers fusionnent la cartographie des repos data (`skill_MCP_projea.md`,
  `skill_MCP_iafec.md`) avec les synthèses cowork. Voir « Source de vérité » ci-dessous pour éviter
  la divergence avec les skill docs d'origine.

## Mettre à jour les instructions d'une instance

1. Éditer le `.md` correspondant.
2. Redéployer sur le VPS : `docker compose up -d --build <service>` (le volume est monté, le conteneur
   relit le fichier au démarrage).
3. Côté claude.ai : reconnecter le connecteur (ou rouvrir une conversation) pour que le client relise
   le champ `instructions`.

## Source de vérité — ce dossier (décision A, actée)

**Ces fichiers sont LA source unique** des règles métier + data model de chaque MCP, livrée
automatiquement via le champ `instructions`. Les anciens skill docs des repos data
(`projea/skill_MCP_projea.md`, `iafec/skill_MCP_iafec.md`) ont été **réduits à un pointeur** vers
ce dossier — ne plus les éditer.

> `iafec/skill_FEC.md` n'est **pas** concerné : il reste la source de la logique métier comptable
> détaillée (PCG, R&R, KPI). Seule la *cartographie d'accès au MCP* a été centralisée ici.

Toute évolution des règles se fait donc ici, puis redéploiement du conteneur concerné.

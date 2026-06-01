# mcps/ — Placeholder pour l'étape 2

Ce dossier est **vide à dessein** au stade actuel (étape 1 du repo `twinl_mcps`).

## Pourquoi vide

Avec seulement 2 MCPs (iafec, projea), refactor le code Python en toolkit générique serait
de la sur-ingénierie. Le coût de maintenir 2 scripts ≈ identiques est inférieur au coût
d'extraire un toolkit prématurément, avec le risque de mal généraliser sur 2 cas d'usage.

## Quand ce dossier se remplira

Au **3e MCP**. À ce moment-là, le pattern sera évident à partir de 3 cas concrets, le
refactor sera bien dimensionné.

## Structure prévue (étape 2)

```
mcps/
├── iafec/
│   ├── config.toml     # name, db, user, password env var, exclusions, expected_views
│   ├── setup.sql       # DDL spécifique (déplacé depuis iafec/scripts/)
│   └── README.md       # particularités iafec (filtres TwinL, signe comptable...)
├── projea/
│   ├── config.toml
│   ├── setup.sql       # DDL spécifique (déplacé depuis projea/scripts/)
│   └── README.md
└── <3e MCP>/...
```

Et un `toolkit/` parallèle :

```
toolkit/
├── pyproject.toml
└── mcp_toolkit/
    ├── verify.py       # logique générique de vérif paramétrée par config.toml
    ├── config_writer.py # écriture claude_desktop_config.json (idempotent, avec backup)
    └── cli.py          # CLI : "mcp-toolkit setup <nom>", "mcp-toolkit verify <nom>"
```

Workflow d'install d'un nouveau MCP (étape 2) :

```bash
# 1. Créer mcps/<nom>/{config.toml, setup.sql}
# 2. Lancer le SQL en root sur le VPS (manuel, comme aujourd'hui)
# 3. CLI :
mcp-toolkit verify <nom>
# → verify + update claude_desktop_config.json + log clair
# 4. Quit / restart Claude Desktop (manuel, comme aujourd'hui)
```

## D'ici l'étape 2

Pour ajouter un MCP :
1. Lire [`../docs/INSTALL_PROCEDURE.md`](../docs/INSTALL_PROCEDURE.md).
2. Créer / utiliser le SQL et le verify Python dans le repo data correspondant
   (en s'inspirant des cas iafec et projea existants).
3. Quand le 3e MCP est en place, décider du refactor vers ce dossier.

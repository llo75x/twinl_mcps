# Architecture des MCPs read-only — choix de conception

Ce doc explique **pourquoi** la stack est structurée comme elle l'est. Utile à lire si on
veut comprendre les choix avant de modifier la procédure, ou si on évalue une variante.

---

## 1. Vue d'ensemble — la pile

```
[Claude Desktop / Code / Cowork]
              │
              │ stdio (lance le process MCP en local via npx)
              ▼
[MCP server : @benborla29/mcp-server-mysql en npx]
              │
              │ TCP MySQL (port 3306) vers le VPS
              ▼
[MariaDB sur vps-51f1b5c1.vps.ovh.net]
              │
              │ User <nom>_readonly, SELECT only sur <base>_readonly.*
              ▼
[DB miroir <base>_readonly avec des VUES SQL SECURITY DEFINER]
              │
              │ Les vues SELECT * (ou avec filtres/JOINs) la base source
              ▼
[DB source <base> : iafec, twinl, ...]
```

Trois couches indépendantes empêchent l'écriture / l'accès non prévu (cf. §3).

---

## 2. Pourquoi `@benborla29/mcp-server-mysql` ?

| Critère | Choix |
|---|---|
| Compatibilité MariaDB | OK — MariaDB est wire-compatible MySQL, le client mysql2 nodejs marche |
| Maintenance | Maintenu, releases régulières |
| Transport | stdio (parfait pour Claude Desktop, pas besoin de réseau) |
| Lancement | `npx -y` = aucune install permanente, juste node 20+ |
| Flags read-only | Supporte `ALLOW_INSERT/UPDATE/DELETE/DDL_OPERATION=false` natifs |
| Permissions | Présente le SELECT comme outil `mysql_query(sql)` simple |

Alternatives évaluées :
- `mcp-server-mysql` (variantes communautaires) — moins maintenu.
- MCP server en Python custom — sur-engineered pour un wrap de connexion MySQL.
- MCP HTTPS distant déployé sur le VPS — option valide mais plus lourde, abandonnée pour usage perso local (cf. §6 ci-dessous).

---

## 3. Pourquoi le modèle « triple-couche read-only »

Aucune couche unique ne fait toute la sécurité. Trois protections indépendantes pour défense
en profondeur :

### Couche 1 — Vues `SQL SECURITY DEFINER`

Les vues dans la DB miroir s'exécutent avec les droits du **créateur** (root MariaDB qui a
exécuté le DDL), pas avec ceux du **caller** (`<nom>_readonly`). Conséquence :
- Le user `<nom>_readonly` peut lire les vues même s'il n'a aucun droit direct sur la base source.
- Les vues peuvent **filtrer** des lignes (ex. `WHERE group_id <> 5` pour exclure TwinL dans iafec) → filtre **structurel** non contournable par le caller.

### Couche 2 — `GRANT SELECT` only sur la DB miroir

Le user `<nom>_readonly` se voit accorder **uniquement** `SELECT` sur `<base>_readonly.*`. **Aucun**
droit sur la base source (`iafec`, `twinl`…), ni global, ni sur d'autres DB. Même si Claude
tentait `SELECT * FROM iafec.groups` directement (au lieu de `iafec_readonly.groups`), MariaDB
refuse côté serveur :

```
ERROR 1142 (42000): SELECT command denied to user '<nom>_readonly'@'...' for table 'iafec.groups'
```

(Vérifié empiriquement dans les logs cowork — c'est exactement ce qui s'est produit quand
Cowork a essayé de lire `twinl_readonly.bdd` via le mauvais MCP `iafec-readonly` avant qu'on
remette `MCP_Projea` en config.)

### Couche 3 — Flags du serveur MCP `ALLOW_*_OPERATION=false`

Côté client MCP (Node), le wrapper `@benborla29/mcp-server-mysql` parse le SQL et **refuse**
les `INSERT/UPDATE/DELETE/DDL` même avant de les envoyer à MariaDB. Filet de sécurité
applicatif.

### Pourquoi 3 couches plutôt qu'une seule

Chaque couche peut faillir indépendamment :
- Couche 1 ratée (vue trop permissive) → couches 2 et 3 bloquent quand même.
- Couche 2 ratée (grant trop large par erreur d'admin) → couches 1 et 3 bloquent.
- Couche 3 ratée (bug du serveur MCP, version compromise) → couches 1 et 2 bloquent.

C'est l'équivalent de "ceinture + bretelles + parachute". Coût ~zéro à mettre en place.

---

## 4. Pourquoi une DB miroir plutôt que des vues dans la base source ?

**Option A — Vues dans la base source** : créer des vues `iafec.mcp_<table>` ou similaire,
filtrer là, et grant SELECT sur ces vues uniquement.

**Option B — DB miroir** : créer une DB `iafec_readonly` qui contient les vues. (← choisi)

### Pourquoi B

| Critère | Option A | Option B (choisi) |
|---|---|---|
| Le user RO voit les tables sources ? | **Oui** (avec ou sans grant — la base est visible) | **Non** — la DB source n'est pas dans son périmètre de visibilité |
| Risque de requête sur la table source au lieu de la vue | **Élevé** — Claude pourrait écrire `SELECT * FROM iafec.groups` (table) au lieu de `iafec.mcp_groups` (vue) sans s'en rendre compte. Le grant côté MariaDB bloquerait, mais l'erreur serait obscure pour Claude. | **Faible** — pour Claude, les tables sources n'existent pas (différentes DB). Il ne peut écrire que sur les vues qu'il voit. |
| Confusion possible | Oui, la DB iafec se peuple de vues `mcp_*` mélangées aux vraies tables | Non, DB séparée |
| Coût de mise en place | Identique | Identique (un `CREATE DATABASE` de plus) |
| Coût d'admin futur | Identique | Identique |
| Coût en perf / stockage | Vues = pas de coût stockage | Idem (les vues ne copient pas les données) |

Le seul argument pour A : `iaFEC_admin` pouvait créer les vues sans passer par root (privilège
local à `iafec.*`). Mais la création d'un user MariaDB RO nécessite root **de toute façon**,
donc l'argument tombe — on passe par root, autant créer la DB miroir aussi.

---

## 5. Pourquoi `SQL SECURITY DEFINER` (et pas `INVOKER`) ?

C'est le défaut MariaDB pour `CREATE VIEW`, mais ça mérite d'être conscientisé.

| Mode | Qui « exécute » la vue | Le user RO doit avoir SELECT sur les tables sources ? |
|---|---|---|
| `SQL SECURITY DEFINER` (défaut) | Le créateur (root) | **Non** — root a tout |
| `SQL SECURITY INVOKER` | Le caller (`<nom>_readonly`) | **Oui** — il faudrait grant SELECT sur les tables sources, ce qu'on veut éviter |

`INVOKER` nous obligerait à donner SELECT au user RO sur les tables sources, ce qui détruirait
l'isolation. Donc `DEFINER` est le bon choix ici.

**Implication sécu** : si un attaquant compromet le user RO et trouve une faille dans une
vue (rare avec `SELECT *`, plus probable avec des sous-requêtes complexes), il agit avec
les droits root pour cette vue. Mais comme on n'expose que `SELECT * FROM <table>` ou avec
des WHERE simples, la surface d'attaque est minimale.

---

## 6. Pattern statique vs procédure dynamique pour les vues

Deux approches mises en œuvre dans nos 2 MCPs :

### Pattern A — Vues statiques explicites (`iafec`)

Une vue par table, chacune écrite à la main avec ses filtres/JOINs. **21 vues** pour iafec.

Avantages :
- Lisibilité directe : on voit dans le SQL ce qui est exposé et comment.
- Filtres riches possibles (chaînes de JOINs pour exclure des données liées indirectement).
- Évolution contrôlée : ajouter une nouvelle vue est une décision consciente.

Inconvénients :
- Verbeux quand il y a beaucoup de tables à exposer.
- Une nouvelle table dans la base source n'est PAS automatiquement exposée → nécessite un edit
  du SQL et une ré-exécution. **C'est volontaire** pour iafec (évite les fuites involontaires).

### Pattern B — Procédure dynamique sur `INFORMATION_SCHEMA` (`projea`)

Une procédure stockée qui boucle sur les tables de la base source, génère une vue par
table sauf une liste fixe d'exclusions. **70 vues** pour projea, générées en boucle.

Avantages :
- Concis pour les bases larges (70+ tables) où la majorité est à exposer.
- Nouvelle table dans la base source = automatiquement exposée à la prochaine ré-exécution
  du SQL. Utile si la base évolue souvent.
- Liste d'exclusions explicite et facile à éditer.

Inconvénients :
- Tout est exposé par défaut (sauf exclusions). Une nouvelle table ajoutée à la base source
  apparaîtra dans le MCP au prochain refresh, même si elle contient des données qu'on ne veut
  pas exposer. **L'auteur du schéma doit être conscient** que toute nouvelle table est exposée
  par défaut.
- Moins lisible (DDL généré dynamiquement, pas visible directement dans le SQL committé).

### Critère de choix

- Base data-warehouse / analytique avec relations riches (chaînes de JOIN, filtres tenants) → **Pattern A**.
- Base ERP / catalogue avec nombreuses tables indépendantes et exclusions par liste → **Pattern B**.

---

## 7. Pourquoi stdio local et pas un serveur MCP HTTPS sur le VPS ?

À la première mention de besoin, deux architectures étaient possibles :

### Stdio local (choisi)

```
Claude Desktop → npx mcp-server-mysql (local, ephemeral)
                  → TCP MySQL → VPS MariaDB
```

- **Pro** : ~zéro setup infra (juste un edit JSON), pas de port HTTPS à exposer, pas d'OAuth,
  pas de gestion de certificat, pas de service à maintenir sur le VPS.
- **Contre** : ne fonctionne que sur la machine où Claude Desktop est installé. Pas
  utilisable depuis claude.ai web (qui n'accepte que les MCPs HTTPS distants avec OAuth).

### HTTPS distant sur le VPS

```
Claude.ai web / Claude Desktop → HTTPS → mcp-server-* hébergé sur VPS (Docker/systemd)
                                          → MariaDB locale
```

- **Pro** : utilisable depuis n'importe où, n'importe quel client Claude. Multi-machine.
- **Contre** : ~1 journée d'infra (déploiement container, reverse proxy, certificat,
  OAuth/token, rate limiting, monitoring), surface d'attaque réseau supplémentaire.

### Décision

Pour un usage **perso, local**, le stdio est largement suffisant et beaucoup plus simple.
Migration possible vers HTTPS si besoin d'usage multi-machine ou depuis le web — pas
prévu dans l'immédiat.

> **Mise à jour** : le besoin d'usage depuis **claude.ai (web)** s'est concrétisé → l'option HTTPS
> distant a été implémentée (serveur FastMCP en conteneur, OAuth 2.1 WorkOS AuthKit, façade Apache).
> Voir [`INSTALL_PROCEDURE_HTTPS.md`](INSTALL_PROCEDURE_HTTPS.md) et [`../mcps/`](../mcps/). Le stdio
> local **et** le HTTPS distant coexistent (clients différents). L'OAuth ajoute une **4ᵉ couche** de
> sécurité au modèle triple-couche du §3.

---

## 8. Pourquoi pas DXT (Desktop Extensions) ?

Claude Desktop introduit progressivement le format **DXT** (extensions empaquetées) géré
via une UI dédiée plutôt que le fichier JSON. Pourquoi on n'utilise pas DXT pour nos MCPs :

- Le format est en évolution, moins stable que le JSON brut.
- L'UI DXT cible plutôt les extensions tierces redistribuables (marketplace).
- Pour un MCP perso pointant sur une DB privée, l'édition JSON reste plus directe.

Migration possible plus tard si DXT devient le mécanisme principal et que l'édition JSON est
dépréciée. Le code MCP lui-même (server stdio) n'aurait pas à changer — juste l'empaquetage.

---

## 9. Pourquoi un repo par projet data + un repo central MCP ?

Décision prise en fin de session 1 :

- **Repos data** (`iafec`, `projea`) : code applicatif + skill_*.md (cartographie pour Claude)
  + SQL setup spécifique. La cartographie reste couplée à la donnée.
- **Repo central MCP** (`twinl_mcps`, ce repo) : procédure générique d'install, pièges,
  architecture, historique. À terme, toolkit Python générique (étape 2).

Alternative envisagée : tout dans un seul repo (`monorepo`). Rejetée car les repos data ont
leur propre cycle de vie et leur propre périmètre de droits ; mélanger l'outillage MCP créerait
de la complexité.

Alternative envisagée : tout dans les repos data, pas de central. Rejetée car
la duplication entre `mcp_*_verify.py` (90 % identiques) devient coûteuse au-delà de 2 MCPs.

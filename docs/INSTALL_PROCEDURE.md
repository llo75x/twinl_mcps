# Procédure d'installation d'un MCP read-only MariaDB

Procédure réutilisable pour exposer une base MariaDB du VPS à Claude Desktop via un serveur MCP
stdio. Appliquée 2 fois (iafec, projea) ; documentée ici pour reproduction sans friction.

## Lecture préalable obligatoire

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — comprendre le modèle triple-couche.
- [`PITFALLS.md`](PITFALLS.md) — éviter les pièges qui ont coûté du temps la 1ère fois.

## Prérequis

| Prérequis | Vérification |
|---|---|
| SSH vers le VPS via alias `vps` (compte `lolo`, groupe sudo) | `ssh vps "whoami"` → `lolo` |
| Mot de passe sudo de `lolo` connu (« llo_root » dans 1Password) | — |
| MariaDB native sur le VPS, version ≥ 10.3 (pour `CREATE OR REPLACE PROCEDURE`) | `ssh vps "mysql --version"` |
| Python 3.12 + pymysql + python-dotenv (venv local) | utilisé en phase 3 |
| Claude Desktop installé et configuré sur la machine | le fichier `%APPDATA%\Claude\claude_desktop_config.json` doit exister à l'app lancée |

## Vue d'ensemble — 5 phases

```
Phase 1  ── Écrire le SQL (DDL idempotent)                 [hors prod, local]
Phase 2  ── Exécuter le SQL en root MariaDB                [côté VPS, via SSH]
Phase 3  ── Vérifier + écrire claude_desktop_config.json   [côté local]
Phase 4  ── Activer côté Claude Desktop (CRITIQUE)         [côté local]
Phase 5  ── Tester                                          [Chat / Cowork]
```

---

## Phase 1 — Écrire le SQL (DDL idempotent)

Le SQL fait 4 choses :

1. **User MariaDB dédié** read-only : `<nom>_readonly@'%'` avec un mot de passe fort.
2. **DB miroir** : `<nom_base_source>_readonly` (ex. `iafec_readonly`, `twinl_readonly`).
3. **Vues** dans la DB miroir, une par table à exposer (en `SQL SECURITY DEFINER` par défaut).
4. **GRANT SELECT** uniquement sur `<nom_base_source>_readonly.*` au user.

Tous les statements doivent être **idempotents** (DROP USER IF EXISTS + CREATE USER, CREATE OR REPLACE VIEW, CREATE DATABASE IF NOT EXISTS) pour permettre la ré-exécution.

### Génération du password

Fort, 32+ caractères, alphabet sûr en shell (pas de `'`, `$`, `!` qui peuvent causer des soucis) :

```powershell
backend\.venv\Scripts\python.exe -c "import secrets, string; print(''.join(secrets.choice(string.ascii_letters + string.digits + '-_=+') for _ in range(40)))"
```

À ranger immédiatement dans 1Password (titre `MCP <nom> <user>_readonly`).

### Squelette du fichier `setup.sql`

```sql
-- =============================================================================
-- Setup MCP <nom> pour <base source>
-- À exécuter EN ROOT sur MariaDB du VPS. Idempotent.
-- AVANT EXÉCUTION : remplacer CHANGE_ME_STRONG_PASSWORD par le password fort.
-- =============================================================================

-- 1. User read-only
DROP USER IF EXISTS '<nom>_readonly'@'%';
CREATE USER '<nom>_readonly'@'%' IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD';

-- 2. Database miroir
CREATE DATABASE IF NOT EXISTS <base>_readonly
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- 3. Vues  → voir les 2 patterns ci-dessous

-- 4. Grants
REVOKE ALL PRIVILEGES, GRANT OPTION FROM '<nom>_readonly'@'%';
GRANT SELECT ON <base>_readonly.* TO '<nom>_readonly'@'%';
FLUSH PRIVILEGES;

-- 5. Vérif (affichée à la sortie de mysql)
SHOW GRANTS FOR '<nom>_readonly'@'%';
SELECT COUNT(*) AS nb_vues FROM information_schema.tables
  WHERE table_schema = '<base>_readonly' AND table_type = 'VIEW';
```

### Deux patterns pour les vues (§3 du squelette)

#### Pattern A — Vues statiques explicites (pour bases avec filtres riches)

Utilisé pour **iafec** car on filtre `groups.id=5` (TwinL) avec des JOINs sur la chaîne `groups → companies → exercises → fec_imports → accounting_entries`. Une vue par table à exposer, avec les JOINs nécessaires pour exclure les lignes liées au groupe filtré.

```sql
CREATE OR REPLACE VIEW <base>_readonly.companies AS
  SELECT * FROM <base>.companies WHERE group_id <> 5;

CREATE OR REPLACE VIEW <base>_readonly.exercises AS
  SELECT e.* FROM <base>.exercises e
  JOIN <base>.companies c ON c.id = e.company_id
  WHERE c.group_id <> 5;

-- ... etc, une vue par table à exposer
```

#### Pattern B — Procédure dynamique sur INFORMATION_SCHEMA (pour bases larges)

Utilisé pour **projea** (70 tables) où on expose tout `twinl.*` sauf une liste fixe de tables sensibles. La procédure boucle sur `INFORMATION_SCHEMA.TABLES`, génère une vue par table non exclue, puis se supprime.

```sql
DELIMITER //
CREATE OR REPLACE PROCEDURE <base>_readonly.build_views()
BEGIN
  DECLARE done INT DEFAULT FALSE;
  DECLARE tbl VARCHAR(255);
  DECLARE cur CURSOR FOR
    SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = '<base>'
      AND TABLE_TYPE = 'BASE TABLE'
      AND TABLE_NAME NOT IN (
        'table_sensible_1', 'table_sensible_2', '...'
      )
    ORDER BY TABLE_NAME;
  DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = TRUE;
  OPEN cur;
  read_loop: LOOP
    FETCH cur INTO tbl;
    IF done THEN LEAVE read_loop; END IF;
    SET @ddl = CONCAT(
      'CREATE OR REPLACE VIEW <base>_readonly.`', tbl,
      '` AS SELECT * FROM <base>.`', tbl, '`'
    );
    PREPARE stmt FROM @ddl;
    EXECUTE stmt;
    DEALLOCATE PREPARE stmt;
  END LOOP;
  CLOSE cur;
END //
DELIMITER ;
CALL <base>_readonly.build_views();
DROP PROCEDURE <base>_readonly.build_views;
```

⚠️ Limite : `CREATE OR REPLACE VIEW` ne supprime pas les vues d'anciennes tables disparues. Si une table est retirée de la base source, dropper manuellement la vue correspondante dans la DB miroir.

### Fichier local vs fichier commité

- **`<repo>/scripts/<nom>_setup.sql`** : version committable, garde le placeholder `CHANGE_ME_STRONG_PASSWORD`. Pas de password en clair.
- **`<repo>/scripts/<nom>_setup.local.sql`** : variante locale avec le vrai password substitué. **Gitignored.**

Patron de substitution en Python :

```python
import re
src = open('scripts/<nom>_setup.sql', encoding='utf-8').read()
out = src.replace("IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD'",
                  f"IDENTIFIED BY '{password}'")
open('scripts/<nom>_setup.local.sql', 'w', encoding='utf-8', newline='\n').write(out)
```

(Ne pas faire un `replace` simple sur `CHANGE_ME_STRONG_PASSWORD` seul — il apparaît aussi dans les commentaires en tête du fichier et serait substitué deux fois.)

---

## Phase 2 — Exécution sur le VPS

`iaFEC_admin` (et tout user applicatif analogue) **n'a pas** les privilèges globaux `CREATE USER` / `CREATE DATABASE`. La phase 2 nécessite **root MariaDB**, accessible via `sudo mysql` (auth socket) depuis le compte `lolo` du VPS.

**Compte SSH à utiliser** : `lolo` via l'alias `vps`. **Pas** `vps-ethan` (compte docker, réservé au déploiement applicatif).

### Commande de pipe (PowerShell local, à exécuter depuis le repo du MCP)

```powershell
cd C:\Users\Laurent\dev\<repo>
("<SUDO_PWD_LOLO>`n" + (Get-Content scripts/<nom>_setup.local.sql -Raw)) | ssh vps "sudo -S -p '' mysql"
```

Explication :
- `Get-Content -Raw` lit le SQL brut.
- La 1ère ligne du pipe est le sudo password ; `sudo -S` la consomme et la mange.
- Le reste est passé à `mysql` qui exécute le SQL.

### Sortie attendue (extraits)

```
Grants for <nom>_readonly@%
GRANT USAGE ON *.* TO `<nom>_readonly`@`%` IDENTIFIED BY PASSWORD '*...'
GRANT SELECT ON `<base>_readonly`.* TO `<nom>_readonly`@`%`
nb_vues
<N>
```

Si tu vois le `GRANT SELECT` et un `nb_vues` plausible, c'est bon.

### ⚠️ Le harness Claude Code bloque cette commande

Le harness refuse l'exécution car combinaison « modif prod + password en clair dans le shell » — c'est volontaire, c'est une garde. **C'est à l'utilisateur de lancer cette commande lui-même** dans son PowerShell. Claude ne touche pas à la prod MariaDB.

---

## Phase 3 — Vérification + écriture de `claude_desktop_config.json`

Script Python `mcp_<nom>_verify.py` qui :

1. Se connecte à `<base>_readonly` en tant que `<nom>_readonly` (depuis le poste local, via le port 3306 du VPS exposé).
2. Vérifie :
   - Nombre de vues présentes vs attendu.
   - Aucune table exclue n'est visible.
   - `INSERT` est refusé (sécurité MariaDB).
   - `SELECT` direct sur la base source est refusé.
3. Met à jour `%APPDATA%\Claude\claude_desktop_config.json` :
   - Lit la config actuelle (ne pas écraser les autres mcpServers ni les preferences).
   - Sauve un backup `.json.bak`.
   - Ajoute (ou met à jour) `mcpServers.<nom-mcp>`.

### Squelette du bloc à ajouter dans `mcpServers`

```json
"<nom-mcp>": {
  "command": "npx",
  "args": ["-y", "@benborla29/mcp-server-mysql"],
  "env": {
    "MYSQL_HOST": "vps-51f1b5c1.vps.ovh.net",
    "MYSQL_PORT": "3306",
    "MYSQL_USER": "<nom>_readonly",
    "MYSQL_PASS": "<password>",
    "MYSQL_DB": "<base>_readonly",
    "ALLOW_INSERT_OPERATION": "false",
    "ALLOW_UPDATE_OPERATION": "false",
    "ALLOW_DELETE_OPERATION": "false",
    "ALLOW_DDL_OPERATION": "false"
  }
}
```

### Lancement (Windows, PowerShell local)

```powershell
cd C:\Users\Laurent\dev\<repo>
$env:PYTHONIOENCODING="utf-8"
$env:MCP_<NOM>_PASSWORD="<password readonly>"
.venv\Scripts\python.exe scripts\mcp_<nom>_verify.py
```

Sortie attendue : ✅ sur les 4-5 checks + `🎉 Setup MCP <nom> OK`.

### Variante pour récupérer le password depuis le `.local.sql` (évite 1Password)

```powershell
$p = (Select-String -Path scripts\<nom>_setup.local.sql -Pattern "IDENTIFIED BY '([^']+)'").Matches[0].Groups[1].Value
$env:MCP_<NOM>_PASSWORD=$p
.venv\Scripts\python.exe scripts\mcp_<nom>_verify.py
```

---

## Phase 4 — Activation côté Claude Desktop (⚠️ CRITIQUE — lire PITFALLS.md d'abord)

C'est la phase qui a coûté le plus de temps la première fois. La règle d'or :

> **Édit du fichier pendant que CD tourne → quit IMMÉDIAT → restart. Sans toucher à AUCUN réglage UI entre l'édit et le redémarrage.**

### Pourquoi

Claude Desktop garde sa config MCP en mémoire et **réécrit `claude_desktop_config.json` depuis sa mémoire sur certains événements UI** (changement de sidebar, thème, etc.). Si tu changes un réglage entre l'écriture du fichier et le restart, CD écrit sa version en-mémoire (qui ne contient pas encore ton nouveau serveur) → ton edit est perdu.

L'astuce : éditer pendant que CD tourne (le fichier existe à ce moment-là) et faire quit+restart immédiats. Le restart force CD à relire le fichier au démarrage, à importer ton nouveau serveur, et à le mettre en mémoire → il devient durable.

### Procédure exacte

1. La phase 3 vient d'écrire le fichier (CD tournant pendant ce temps, c'est OK).
2. **NE TOUCHE À RIEN dans CD.** Pas de sidebar, pas de menu réglages, rien.
3. Clic droit sur l'icône CD dans le systray (zone notifications Windows, en bas à droite) → **Quit**.
4. Attends 2-3 secondes que CD soit complètement fermé. Vérifie optionnellement : `Get-Process claude* -ErrorAction SilentlyContinue` doit ne rien retourner.
5. Relance CD depuis le menu Démarrer ou le raccourci bureau.
6. Au démarrage, CD lit le fichier (ton nouveau serveur y est) → l'importe en mémoire → durable.

### Si ça ne marche pas du 1er coup

Vérifie l'état de la config :

```powershell
.venv\Scripts\python.exe -c "import json; from pathlib import Path; cfg = Path.home() / 'AppData/Roaming/Claude/claude_desktop_config.json'; print(list(json.loads(cfg.read_text(encoding='utf-8')).get('mcpServers',{}).keys()))"
```

Si ton nouveau serveur n'apparaît pas, il a été clobbé : recommencer phase 3 + phase 4 sans toucher aux réglages cette fois.

---

## Phase 5 — Test

### Dans Claude Desktop (Chat ou Cowork)

Demander une requête simple :

> Via `<nom-mcp>`, donne-moi <une requête métier sur les données>.

Claude doit appeler `mcp__<nom-mcp>__mysql_query(...)`. La permission peut être demandée à la 1ère utilisation — choisir « Allow always for this conversation ».

### Vérifications côté logs

Si problème, regarder en local :
- `%APPDATA%\Claude\logs\mcp-server-<nom-mcp>.log` — logs du serveur MCP lui-même.
- `%APPDATA%\Claude\logs\mcp.log` — logs du gestionnaire MCP de CD.
- `%APPDATA%\Claude\logs\cowork_host_loop_debug.log` — utile pour debug Cowork.

### Erreurs typiques

| Erreur | Cause probable |
|---|---|
| « MCP server <nom> not found » | Le serveur n'est pas dans la config courante de CD → clobber, recommencer phase 4. |
| `Access denied for user '<nom>_readonly'` | Mauvais password dans la config OU le user n'a pas été créé en phase 2. |
| `SELECT command denied to user '<nom>_readonly' for table '<base>.<tbl>'` | Claude essaie de lire la base **source** au lieu de la DB miroir → bug skill doc, ou serveur MCP mal pointé sur `MYSQL_DB`. |
| Le serveur démarre mais aucun outil n'apparaît | Vérifier que `npx -y @benborla29/mcp-server-mysql` est disponible et fonctionne hors CD. |

---

## Documentation à mettre à jour après installation

Pour chaque nouveau MCP :

1. **`twinl_mcps/README.md`** : ajouter la ligne dans le tableau « Inventaire des MCPs actifs ».
2. **Repo data correspondant** : créer `skill_MCP_<nom>.md` (cartographie pour Claude — voir les exemples existants `skill_MCP_iafec.md` et `skill_MCP_projea.md`).
3. **1Password** : entry `MCP <nom> <user>_readonly` avec le password.
4. **Si nouveau repo data** : ajouter au `.gitignore` les fichiers `scripts/<nom>_setup.local.sql` et `.mcp-<nom>-credentials.txt`.

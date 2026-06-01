# Pièges à connaître absolument

Les leçons apprises à la dure lors de la première session (27-29 mai 2026).
**Lire ce doc AVANT toute manipulation MCP** — chacun de ces pièges a coûté du temps.

---

## 1. ⚠️ Claude Desktop réécrit `claude_desktop_config.json` depuis sa mémoire

**Le piège le plus important.** CD garde sa config MCP en mémoire et **réécrit le fichier
sur certains événements UI** (changement de sidebar, thème, taille de fenêtre, redémarrage…),
en serialisant son état en-mémoire. Toute modification externe du fichier qui n'a PAS encore
été chargée en mémoire (= pas de quit+restart entre l'édit et l'événement) est **clobbée**.

### Comment ça s'est manifesté

- L'ajout de `iafec-readonly` a réussi car séquence : édit fichier → quit+restart immédiats → CD a lu le fichier au démarrage suivant, importé `iafec-readonly` en mémoire, et à partir de là il est durable.
- L'ajout de `MCP_Projea` a échoué la 1ère fois car séquence : édit fichier → l'utilisateur change un réglage UI (sidebarMode `epitaxy` → `chat`) AVANT de redémarrer → CD réécrit le fichier depuis sa mémoire (qui n'avait que `iafec-readonly`) → clobber de `MCP_Projea`.

### La règle d'or

> **Édit du fichier pendant que CD tourne → quit IMMÉDIAT → restart. AUCUN clic dans l'UI entre les deux.**

Le quit propre n'écrit pas le fichier (CD ne sérialise sur quit). Le démarrage suivant lit le
fichier, importe les nouveaux serveurs en mémoire, à partir de là c'est durable même face aux
événements UI.

### Diagnostic si ça arrive

Vérifier si le nouveau serveur est encore là après un événement suspect :

```powershell
.venv\Scripts\python.exe -c "import json; from pathlib import Path; print(list(json.loads((Path.home() / 'AppData/Roaming/Claude/claude_desktop_config.json').read_text(encoding='utf-8')).get('mcpServers',{}).keys()))"
```

Si le serveur a disparu → clobber. Recommencer phase 3+4 de la procédure d'install, sans
toucher aux réglages cette fois.

---

## 2. ⚠️ Le fichier `claude_desktop_config.json` est ABSENT quand CD est fermé

Comportement observé : quand CD est entièrement quitté (`Get-Process claude*` renvoie vide), le fichier `%APPDATA%\Claude\claude_desktop_config.json` **n'existe plus**. Au démarrage suivant CD, il réapparaît (vraisemblablement restauré depuis un store interne).

### Conséquence

La tentation logique « édit du fichier pendant que CD est quitté, puis restart » **ne marche pas** : le fichier n'existe pas pendant le quit. C'était notre première hypothèse de fix, qui a échoué.

### Méthode qui marche

Édit pendant que CD tourne (le fichier existe), puis quit+restart immédiats sans toucher aux
réglages — c'est exactement la méthode du piège §1.

---

## 3. ⚠️ `iaFEC_admin` n'a PAS le privilège `CREATE USER`

Le user applicatif `iaFEC_admin` a `ALL PRIVILEGES ON iafec.*`, **scope-limité à la DB `iafec`**.
Les privilèges globaux comme `CREATE USER`, `CREATE DATABASE`, `FILE` ne lui sont pas accordés
(par sécurité, cf. `iafec/OPS.md` §2.1-2.4).

### Conséquence

La création d'un user MariaDB `<nom>_readonly` ET d'une DB miroir `<nom_base>_readonly` **doit
passer par root MariaDB**, pas par `iaFEC_admin`.

### Comment passer en root MariaDB

Sur le VPS, root MariaDB est accessible via **auth socket** depuis le compte système `lolo`
(membre du groupe sudo). `sudo mysql` ouvre une session root MariaDB sans password applicatif.

```bash
ssh vps                  # via alias, compte lolo
sudo mysql               # demande le sudo password, puis ouvre mysql en root
```

---

## 4. ⚠️ `vps-ethan` vs `vps` — deux comptes, deux usages

Le VPS a deux comptes SSH configurés en `~/.ssh/config` :

| Alias | Compte VPS | Groupes | Usage |
|---|---|---|---|
| `vps` | `lolo` | sudo | **Admin système et MariaDB root** (procédure MCP, rotations password, etc.) |
| `vps-ethan` | `ethan` | docker | **Déploiement applicatif iaFEC** (docker compose, git pull, etc.) |

`ethan` n'est pas dans le groupe sudo et **ne peut pas** faire `sudo mysql`. `lolo` peut.
Pour la procédure d'install MCP, c'est `vps` (donc `lolo`) qu'il faut utiliser.

CLAUDE.md d'iafec indique « Ne PAS revenir au workflow `ssh lolo + su -` » — cette consigne
concerne **le déploiement docker** (qui doit passer par `ethan`/docker), pas l'admin MariaDB.

---

## 5. ⚠️ Le harness Claude Code bloque les commandes sensibles

Lors de la 1ère session, deux blocages ont été rencontrés :

### Blocage 1 — pipe d'un password sudo en clair vers SSH

```powershell
{ printf "<SUDO_PWD>\n"; cat scripts/setup.local.sql; } | ssh vps "sudo -S -p '' mysql"
```

Le harness refuse : « production infrastructure modification and credential handling without
preview, and the password appears in the shell command/transcript ». Légitime.

**Workaround** : c'est l'utilisateur qui lance cette commande dans son PowerShell. Claude ne
touche pas à la prod MariaDB en écriture root.

### Blocage 2 — push vers un repo GitHub non-pré-approuvé

```bash
git push -u origin master   # vers un nouveau repo non listé dans les sources de confiance
```

Le harness bloque (« data exfiltration ») pour les repos qui ne sont pas dans la liste des
sources git de confiance configurée.

**Workaround** : l'utilisateur fait le push lui-même la première fois. À terme, on peut
ajouter une règle de permission pour les repos personnels.

### Conséquence pour la procédure

Les phases 1 (écrire le SQL), 3 (vérif + config Claude Desktop) et 4 (restart CD) peuvent
être faites par Claude. La phase 2 (exécution SQL en root sur le VPS) **doit** être faite
par l'utilisateur. Procédure documentée en conséquence.

---

## 6. ⚠️ Sur Windows, `/tmp/` n'est pas le `/tmp` d'un shell Bash

Quand un script Python tourne sur Windows et qu'on lui passe un chemin `/tmp/foo` :
- depuis Git Bash, `cat /tmp/foo` fonctionne (Git Bash a son propre `/tmp`).
- depuis Python, `open('/tmp/foo')` échoue avec `FileNotFoundError` (Python interprète
  `/tmp/` comme un chemin absolu Windows = `C:\tmp\`).

### Workaround

Utiliser un chemin local au projet, gitignored :
```python
open('.mcp-readonly-credentials.txt', 'w', encoding='utf-8').write(pwd)
```

Ou utiliser `os.environ['TEMP']` qui pointe sur le bon répertoire temp Windows.

---

## 7. ⚠️ `getpass.getpass()` bloque en non-tty sur Windows

Si on lance un script Python qui appelle `getpass.getpass()` et qu'on pipe stdin (`echo X |
python script.py`), sur Windows getpass **lit directement depuis la console** (msvcrt), pas
depuis stdin. Le pipe est ignoré, le script attend une saisie console qui ne viendra jamais
→ deadlock.

### Workaround

Toujours prévoir une env var en alternative dans le script :

```python
password = os.environ.get("MCP_<NOM>_PASSWORD", "").strip()
if not password:
    password = getpass.getpass("  > ")   # fallback interactif si tty disponible
```

Et passer par env var en automation :

```powershell
$env:MCP_<NOM>_PASSWORD="..."
python script.py
```

---

## 8. ⚠️ Encodage Unicode dans Python Windows par défaut (cp1252)

Si un script Python sur Windows fait `print("✅ ...")`, il crashe avec
`UnicodeEncodeError: 'charmap' codec can't encode character '✅'` car stdout est en
cp1252 par défaut.

### Workaround

Forcer l'encodage UTF-8 avant de lancer :
```powershell
$env:PYTHONIOENCODING="utf-8"
.venv\Scripts\python.exe scripts\verify.py
```

---

## 9. ⚠️ Cowork sandboxe l'accès fichiers

Cowork (le mode agentique de Claude Desktop) **lit bien les MCPs stdio** comme Chat et Claude
Code (confirmé par `cowork_host_loop_debug.log`). Pas de problème de ce côté.

**Mais** Cowork **sandboxe les accès fichiers** : il refuse de lire un fichier hors des
« connected folders » de la session, avec :

```
permissionDecision: deny (reason: <path> is outside this session's connected folders,
so Read can't reach it. ... request it with the request_cowork_directory tool)
```

### Conséquence

Pour que Cowork utilise `skill_MCP_<nom>.md` (la cartographie), il faut soit :
- attacher le `.md` en knowledge à l'espace Cowork (équivalent Project),
- soit connecter le dossier via `request_cowork_directory` (l'utilisateur approuve).

Pas bloquant — le MCP fonctionne sans la cartographie, juste avec un SQL moins fin de la part de Claude.

---

## 10. ⚠️ Si le serveur MCP s'appelle avec underscore/majuscules, ça marche

À un moment on s'est demandé si `MCP_Projea` (Pascal + underscore) versus `iafec-readonly`
(lowercase + tiret) posait souci à Claude Desktop. **Réponse : non, les deux marchent.**
Le piège §1 (clobber) avait masqué ce diagnostic au début. Une fois la séquence d'activation
respectée, `MCP_Projea` fonctionne parfaitement.

Pour cohérence future, conventionner lowercase + tiret recommandé, mais pas obligatoire.

---

## 11. ⚠️ Mismatch entre password généré et password écrit dans .local.sql

Anecdote de la 1ère session : après un `replace` Python sur `'CHANGE_ME_STRONG_PASSWORD'`,
le `.local.sql` contenait un password différent de celui qu'on pensait. Cause : un précédent
run avait écrit le `.local.sql` avec un autre password, et le `replace` simple (sur la chaîne
`CHANGE_ME_STRONG_PASSWORD` sans le contexte `IDENTIFIED BY '...'`) n'avait remplacé qu'un
seul des deux placeholders du fichier source (header + ligne SQL).

### Workaround

Toujours faire le `replace` sur la **chaîne complète avec contexte** :

```python
out = src.replace("IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD'",
                  f"IDENTIFIED BY '{pwd}'")
```

Et toujours **vérifier après écriture** que le `.local.sql` contient bien le password attendu :

```bash
grep "IDENTIFIED BY" scripts/<nom>_setup.local.sql
```

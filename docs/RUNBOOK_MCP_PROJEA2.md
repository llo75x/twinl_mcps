# Runbook — déployer le MCP `projea2` (3ᵉ instance)

Les commandes exactes à lancer, dans l'ordre, pour exposer la base **`projea2`** (CRM/ERP M&A
PROJEA2) à claude.ai via une 3ᵉ instance du serveur MCP HTTPS.

> **Qui fait quoi** ([`PITFALLS.md`](PITFALLS.md) §5) : tout ce qui suit **modifie la prod** →
> c'est **Laurent** qui lance ces commandes. Claude a écrit le code, la config et cette procédure ;
> il ne touche ni à MariaDB, ni à Docker, ni à Apache, ni à WorkOS.

Procédure générique de référence : [`INSTALL_PROCEDURE_HTTPS.md`](INSTALL_PROCEDURE_HTTPS.md).
Ce runbook n'en est que l'**instanciation** pour projea2 (valeurs concrètes, rien à deviner).

## Ce qui est déjà fait (commité dans ce repo)

| Fichier | Rôle |
|---|---|
| [`../mcps/sql/projea2_setup.sql`](../mcps/sql/projea2_setup.sql) | DDL idempotent : user `projea2_mcp`, DB miroir `projea2_readonly`, 45 vues, purge des orphelines, vérifs anti-fuite |
| [`../mcps/instructions/projea2.md`](../mcps/instructions/projea2.md) | Règles métier + data model livrés au modèle (bloc `DIGEST` + référence complète) |
| [`../mcps/docker-compose.yml`](../mcps/docker-compose.yml) | Service `mcp-projea2`, port hôte `127.0.0.1:8083` |
| [`../mcps/deploy/apache-mcp.conf.example`](../mcps/deploy/apache-mcp.conf.example) | Vhost `mcp-projea2.twinl.fr` → `127.0.0.1:8083` |

## Valeurs de l'instance

| Paramètre | Valeur |
|---|---|
| Base source | `projea2` |
| DB miroir | `projea2_readonly` |
| User MariaDB | **`projea2_mcp`** — ⚠️ PAS `projea2_readonly`, déjà pris par l'application (voir §Pourquoi `projea2_mcp`) |
| `MCP_SERVER_NAME` | `mcp-projea2-readonly` |
| Conteneur | `mcp-projea2` |
| Port hôte | `127.0.0.1:8083` (interne : 8080, comme les autres) |
| Sous-domaine | `mcp-projea2.twinl.fr` |
| URL connecteur | `https://mcp-projea2.twinl.fr/mcp` |
| Fichier d'env (VPS) | `/opt/twinl_mcps/mcps/projea2.env` (chmod 600, gitignored) |

---

## Phase 1 — MariaDB : user + DB miroir + vues  *(`ssh vps`, root MariaDB)*

### 1.1 Générer le password read-only

```powershell
python -c "import secrets, string; print(''.join(secrets.choice(string.ascii_letters + string.digits + '-_=+') for _ in range(40)))"
```

À ranger **immédiatement** dans 1Password (titre `MCP projea2 projea2_mcp`).

### 1.2 Fabriquer la variante locale du SQL (avec le vrai password)

Le fichier commité garde le placeholder. La variante `*.local.sql` est **gitignorée**.

```powershell
cd C:\Users\Laurent\dev\twinl_mcps
python -c "import sys; p=sys.argv[1]; src=open('mcps/sql/projea2_setup.sql',encoding='utf-8').read(); open('mcps/sql/projea2_setup.local.sql','w',encoding='utf-8',newline='\n').write(src.replace(\"IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD'\", f\"IDENTIFIED BY '{p}'\"))" "<LE_PASSWORD>"
```

> Le `replace` porte sur la ligne `IDENTIFIED BY '...'` entière, pas sur le seul mot
> `CHANGE_ME_STRONG_PASSWORD` — qui apparaît aussi dans l'en-tête de commentaires.

### 1.3 Exécuter en root sur le VPS

```powershell
cd C:\Users\Laurent\dev\twinl_mcps
("<SUDO_PWD_LOLO>`n" + (Get-Content mcps/sql/projea2_setup.local.sql -Raw)) | ssh vps "sudo -S -p '' mysql"
```

**Sortie attendue** (les 4 dernières requêtes du script sont des contrôles) :

```
Grants for projea2_mcp@%
GRANT USAGE ON *.* TO `projea2_mcp`@`%` IDENTIFIED BY PASSWORD '*...'
GRANT SELECT ON `projea2_readonly`.* TO `projea2_mcp`@`%`

compte_app        hote_app
projea2_admin     127.0.0.1
projea2_app       127.0.0.1
projea2_mcp       %
projea2_readonly  127.0.0.1

nb_vues
45
```

et **aucune ligne** pour `fuite_vue_exclue`, `fuite_colonne` et `table_source_sans_vue`.

- `nb_vues` ≠ 45 → le schéma a bougé depuis l'écriture du script : regarder ce que renvoie
  `table_source_sans_vue` (table nouvelle → décider de l'exposer ou de l'exclure).
- une ligne dans `fuite_colonne` → **arrêter** : une colonne secrète est exposée.

### 1.4 Vérifier l'isolation depuis le VPS

Les 4 contrôles ci-dessous ont été **validés à blanc sur la MariaDB de dev** (2026-07-31) : ils
passent avec ce DDL. À rejouer sur le VPS pour confirmer l'état réel de la prod.

```bash
# 1. ne doit PAS lister `projea2` (base source). Attendu : projea2_readonly + information_schema
mysql -h 127.0.0.1 -u projea2_mcp -p -e 'SHOW DATABASES'
# 2. doit ÉCHOUER → ERROR 1142 ... SELECT command denied ... for table `projea2`.`users`
mysql -h 127.0.0.1 -u projea2_mcp -p -e 'SELECT COUNT(*) FROM projea2.users'
# 3. doit ÉCHOUER → ERROR 1142 ... UPDATE command denied
mysql -h 127.0.0.1 -u projea2_mcp -p projea2_readonly -e "UPDATE companies SET legal_name='x' WHERE id=1"
# 4. lecture OK, et DESCRIBE users NE DOIT PAS montrer password_hash
mysql -h 127.0.0.1 -u projea2_mcp -p projea2_readonly -e 'SELECT COUNT(*) FROM companies WHERE is_deleted=0; DESCRIBE users'
# 5. table exclue → ERROR 1146 Table 'projea2_readonly.password_tokens' doesn't exist
mysql -h 127.0.0.1 -u projea2_mcp -p projea2_readonly -e 'SELECT * FROM password_tokens LIMIT 1'
```

`-p` sans valeur : le mot de passe est demandé de façon interactive plutôt que laissé dans
l'historique du shell.

### 1.5 Pourquoi `projea2_mcp` et pas `projea2_readonly`

**L'application PROJEA2 possède déjà un compte MariaDB nommé `projea2_readonly`** (hôte
`127.0.0.1`, `GRANT SELECT ON projea2.*`), créé par `projea2/deploy/setup-vps.sh` : c'est lui qui
exécute le SQL brut des listes Expert/IA (`READONLY_DATABASE_URL`). Il n'a rien à voir avec le MCP,
et ses droits sont **plus larges** (il lit la base source, `users.password_hash` compris).

Réutiliser le nom aurait donné deux comptes homonymes ne se distinguant que par l'hôte
(`@127.0.0.1` pour l'app, `@'%'` pour le MCP), avec des mots de passe et des droits différents.
MariaDB s'en sort, mais pas les humains : un `DROP USER 'projea2_readonly'` ou un
`SET PASSWORD FOR 'projea2_readonly'` **sans hôte** vise `@'%'` par défaut — donc le compte du MCP.
Et un `setup-vps.sh` rejoué aurait pu, à une virgule près, réinitialiser le mauvais mot de passe.

Le contrôle `compte_app` du §1.3 sert exactement à ça : voir les deux comptes côte à côte et
constater qu'aucun `projea2_readonly@%` n'existe.

---

## Phase 2 — WorkOS : déclarer la nouvelle ressource  *(dashboard)*

Rien à créer : on réutilise le **même** projet AuthKit (donc le même `AUTHKIT_DOMAIN`) et
l'invite-only déjà en place.

1. **Connect → Configuration → MCP Auth → MCP resource indicators** : ajouter
   `https://mcp-projea2.twinl.fr/mcp` (et, sans risque, `https://mcp-projea2.twinl.fr`).
2. Ne rien changer d'autre (DCR + CIMD restent activés, « Sign up » reste désactivé).

> Si l'indicateur n'est pas ajouté, claude.ai reçoit `error=invalid_target` au moment du login.

---

## Phase 3 — DNS  *(zone `twinl.fr`, BIND sur le VPS)*

```
mcp-projea2  IN  A   54.38.35.104
```

Incrémenter le serial de la zone, puis :

```bash
sudo rndc reload twinl.fr
dig +short mcp-projea2.twinl.fr      # → 54.38.35.104
```

---

## Phase 4 — Conteneur  *(`ssh vps-ethan`, compte docker)*

```bash
cd /opt/twinl_mcps && git pull            # récupère le service + les instructions projea2

cd /opt/twinl_mcps/mcps
cp server/.env.example projea2.env
chmod 600 projea2.env
# éditer projea2.env (valeurs ci-dessous)

docker compose build mcp-projea2
docker compose up -d mcp-projea2
docker compose ps                          # attendre "healthy"
docker compose logs --tail=40 mcp-projea2  # doit logger "instructions loaded from file (N chars)"
                                           # et "tool digest extracted (N chars)"
```

Contenu de `projea2.env` — seules ces lignes changent par rapport au modèle :

```ini
MCP_SERVER_NAME=mcp-projea2-readonly
MCP_DB_USER=projea2_mcp
MCP_DB_PASS=<LE_PASSWORD de la phase 1.1>
MCP_DB_NAME=projea2_readonly
BASE_URL=https://mcp-projea2.twinl.fr
AUTHKIT_DOMAIN=<identique à iafec.env / projea.env>
```

`MCP_PORT` reste **8080** (interne au conteneur ; la différenciation se fait par le mapping hôte
`127.0.0.1:8083` déclaré dans `docker-compose.yml`).

**Approbation Slack** (optionnelle, cf. `.env.example`) : si tu réutilises la même app Slack que
`mcp-projea`, recopie `SLACK_WEBHOOK_URL` + `SLACK_SIGNING_SECRET` et ajoute
`https://mcp-projea2.twinl.fr/slack/action` comme **2ᵉ Request URL**… ce que Slack **ne permet
pas** (une seule Request URL d'interactivité par app). Deux options :

- laisser les variables Slack **vides** sur projea2 → aucune approbation demandée (comportement
  d'origine, plafonds lignes/octets toujours actifs) ;
- créer une **app Slack dédiée** « MCP projea2 » (webhook + signing secret propres, Request URL
  `https://mcp-projea2.twinl.fr/slack/action`). Le message Slack nomme désormais l'instance
  (`MCP_SERVER_NAME`), donc les deux apps ne se confondent pas.

Vérification locale (avant Apache) :

```bash
curl -s http://127.0.0.1:8083/health      # {"status":"ok"}
```

---

## Phase 5 — Apache + TLS  *(`ssh vps`, `lolo` + sudo)*

```bash
# le vhost projea2 est dans le même fichier d'exemple que les 2 autres
sudo cp /opt/twinl_mcps/mcps/deploy/apache-mcp.conf.example /etc/apache2/sites-available/mcp.conf

# certificat DÉDIÉ à projea2 (les chemins du vhost pointent /etc/letsencrypt/live/mcp-projea2.twinl.fr/)
sudo certbot --apache -d mcp-projea2.twinl.fr

sudo apachectl configtest      # → "Syntax OK"
sudo systemctl reload apache2
```

> ⚠️ Si tu recopies `mcp.conf` par-dessus l'existant, tu écrases les directives SSL que certbot
> avait injectées pour iafec/projea. Le plus sûr : **n'ajouter que le bloc `<VirtualHost *:443>` de
> projea2** (et l'alias `mcp-projea2.twinl.fr` sur le vhost `*:80`) dans le `mcp.conf` en place,
> plutôt que d'écraser le fichier. Après `certbot`, revérifier que `SetEnv no-gzip 1`,
> `proxy-sendchunked`, `flushpackets=on` et `ProxyTimeout` sont toujours là.

---

## Phase 6 — claude.ai : ajouter le connecteur

1. claude.ai → **Settings → Connectors → Add custom connector**.
2. URL : `https://mcp-projea2.twinl.fr/mcp` → dérouler le flux OAuth (login WorkOS, email de Laurent).
3. Tester par une question qui exige les règles métier, pas seulement la connexion :

> « Via le connecteur projea2, combien de sociétés vivantes, et combien ont au moins un décideur
> joignable ? »

Attendu : Claude appelle `get_data_model_reference` (ou s'appuie sur le digest), filtre
`is_deleted = 0`, `end_date IS NULL`, `COALESCE(canon.level, f.level) >= 6` et
`email_status = 'VALID'` — et **ne bricole aucun id de référentiel en dur**.
Ordre de grandeur au 2026-07-31 : ~58 900 sociétés.

---

## Vérification end-to-end

```bash
curl -s https://mcp-projea2.twinl.fr/.well-known/oauth-protected-resource | jq .
curl -s -o /dev/null -w "%{http_code}\n" https://mcp-projea2.twinl.fr/mcp        # 401 attendu
curl -s https://mcp-projea2.twinl.fr/health                                       # {"status":"ok"}
curl -s -H 'Accept-Encoding: gzip' -I https://mcp-projea2.twinl.fr/mcp | grep -i content-encoding || echo "pas de gzip — OK"
```

Tests fonctionnels (depuis claude.ai) :

- **Read-only** : `SELECT` passe ; `INSERT`/`UPDATE`/`DELETE`, `/* x */ DELETE …`,
  `SELECT 1; DELETE …`, `SELECT … INTO OUTFILE` sont **rejetés** ; un CTE `WITH … SELECT` passe.
- **Isolation** : `SELECT * FROM projea2.users` → refusé (base source hors périmètre).
- **Non-fuite** : `DESCRIBE users` ne montre **pas** `password_hash` ; `SELECT * FROM password_tokens`
  échoue (table inexistante côté miroir).
- **Plafond** : `SELECT * FROM companies` → ≤ `MCP_MAX_ROWS` lignes + marqueur « truncated ».
- **Étanchéité entre instances** : sur le connecteur projea2, une question sur `bdd`/`dirigeants`
  (tables `twinl`) doit échouer — et Claude doit renvoyer vers le connecteur `mcp-projea`.

---

## Maintenance

- **Après chaque migration Alembic de projea2** qui ajoute/retire une table ou une colonne :
  rejouer `mcps/sql/projea2_setup.local.sql` (phase 1.3). Une vue `SELECT *` **ne suit pas** le
  schéma ; une colonne ajoutée reste invisible, une colonne retirée casse la vue.
  ⚠️ Le script **réinitialise le password** de `projea2_mcp` (`DROP USER` + `CREATE USER`) : le
  regénérer et le resynchroniser avec `projea2.env`, ou remettre le même. Il ne touche **jamais**
  au `projea2_readonly` de l'application (§1.5) — le contrôle `compte_app` le confirme à chaque run.
- **Après une re-migration `twinl` → `projea2`** : rien à faire côté MCP (les vues suivent), mais
  les `id` des lignes migrées ont changé — ne pas se fier à un id noté lors d'une session passée.
- **Une nouvelle table portant un secret** doit être ajoutée à la liste d'exclusion du DDL **avant**
  de le rejouer : le pattern est « tout est exposé sauf exclusions ».
- **Mise à jour des règles métier** : éditer `mcps/instructions/projea2.md`, `git pull` sur le VPS,
  `docker compose up -d --build mcp-projea2`, puis reconnecter le connecteur côté claude.ai (le
  client relit le champ `instructions` à la connexion).

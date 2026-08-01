# Runbook — déployer le MCP `projea2`, puis fermer `mcp-projea`

Les commandes exactes à lancer, dans l'ordre, pour exposer la base **`projea2`** (CRM/ERP M&A
PROJEA2) à claude.ai via une 3ᵉ instance du serveur MCP HTTPS — **et remplacer** `mcp-projea`
(base legacy `twinl`), qui sera fermé une fois projea2 recetté (§Phase 7).

Le remplacement, et non la coexistence durable, décide de deux choses en cours de route :
l'**app Slack** de projea est *déménagée* vers projea2 plutôt que dupliquée (§Phase 4), et la
couche données de `twinl` est **conservée** alors que son MCP disparaît (§Phase 7).

> **Qui fait quoi** ([`PITFALLS.md`](PITFALLS.md) §5) : tout ce qui suit **modifie la prod** →
> c'est **Laurent** qui lance ces commandes. Claude a écrit le code, la config et cette procédure ;
> il ne touche ni à MariaDB, ni à Docker, ni à Apache, ni à WorkOS.

Procédure générique de référence : [`INSTALL_PROCEDURE_HTTPS.md`](INSTALL_PROCEDURE_HTTPS.md).
Ce runbook n'en est que l'**instanciation** pour projea2 (valeurs concrètes, rien à deviner).

## État du déploiement au 2026-08-01

**`https://mcp-projea2.twinl.fr` est en ligne.** Il ne reste que les consoles web.

| Phase | État | Par qui |
|---|---|---|
| 0. Code, DDL, instructions, doc | ✅ | Claude |
| 0bis. Code sur le VPS (`/opt/twinl_mcps` converti en clone git) | ✅ | Claude |
| 0ter. Pré-vol prod (50 tables, colonnes projetées, collision de nom) | ✅ | Claude |
| 1. MariaDB : user + DB miroir + 45 vues + `projea2.env` | ✅ 6 contrôles au vert | Claude |
| 3. DNS : `A` chez OVH | ✅ `mcp-projea2.twinl.fr → 54.38.35.104` | Laurent |
| 4. Conteneur | ✅ `Up (healthy)`, `127.0.0.1:8083` | Claude |
| 5. Apache + TLS | ✅ 5 contrôles au vert, cert jusqu'au **2026-10-30** | Claude |
| 2. WorkOS : resource indicator | ✅ fait (env. **Staging**) | Laurent |
| 6. Connecteur claude.ai + Slack Request URL | ⬜ **à faire** | Laurent |

Vérifié en ligne après la phase 5 :

```
mcp-iafec    /health 200   /mcp 401      ← inchangé
mcp-projea   /health 200   /mcp 401      ← inchangé
mcp-projea2  /health 200   /mcp 401      ← nouveau
projea2.twinl.fr → 200                   ← l'appli n'a pas bougé
HTTP → HTTPS 301 · pas de compression · resource = https://mcp-projea2.twinl.fr/mcp
```

Vues sur données réelles : **58 915** sociétés vivantes · 94 954 personnes · 86 436 rattachements
actifs · 66 543 events · 235 opérations.

### Les 3 gestes restants, tous en console web

| Où | Quoi |
|---|---|
| **WorkOS** → `Connect → Configuration → Resource Indicators` | ajouter `https://mcp-projea2.twinl.fr/mcp` — ⚠️ sur l'environnement **Staging** (`royal-lagoon-55-staging.authkit.app`), pas Production |
| **Slack** → app existante → `Interactivity & Shortcuts → Request URL` | remplacer par `https://mcp-projea2.twinl.fr/slack/action` (déménagement, cf. phase 4) |
| **claude.ai** → `Settings → Connectors → Add custom connector` | `https://mcp-projea2.twinl.fr/mcp` |

Dans cet ordre : sans le resource indicator, le login OAuth du connecteur échoue sur
`invalid_target`.

**Chaîne de découverte OAuth vérifiée le 2026-08-01** — c'est le parcours exact de claude.ai :

```
/mcp sans jeton → 401 + resource_metadata=…/.well-known/oauth-protected-resource/mcp
cette métadonnée → resource = https://mcp-projea2.twinl.fr/mcp
                   authorization_server = royal-lagoon-55-staging.authkit.app
métadonnée WorkOS → registration_endpoint présent ⇒ DCR actif
```

Les trois maillons tiennent. Reste le login lui-même, qui exige un navigateur.

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

## Phase 1 — MariaDB + `projea2.env` : un seul geste  *(`ssh vps`, root)*

Le mot de passe du user `projea2_mcp` est **généré sur le VPS**, injecté dans le SQL et dans
`projea2.env` sans jamais passer par le shell local, un fichier du repo, ni un presse-papier.
C'est plus sûr que la génération locale, et ça supprime l'étape « fabriquer le `.local.sql` ».

**Une seule commande**, depuis n'importe quel terminal du poste (PowerShell ou Git Bash) :

```bash
ssh vps "sudo -n bash /opt/twinl_mcps/mcps/sql/projea2_bootstrap.sh"
```

Elle exécute [`../mcps/sql/projea2_bootstrap.sh`](../mcps/sql/projea2_bootstrap.sh), versionné et
déjà présent sur le VPS, qui enchaîne : contrôles préalables → génération du mot de passe →
injection dans le DDL → exécution en root MariaDB → **vérification interprétée** (verdict lisible,
pas du SQL brut) → écriture de `projea2.env`. Il s'arrête net au premier contrôle rouge.

Sortie attendue, en fin de course :

```
=== 4. Vérification du résultat ===============================
  vues créées (attendu 45)                       45
  colonnes secrètes exposées (attendu 0)         0
  tables exclues exposées (attendu 0)            0
  tables source sans vue (attendu 0)             0
  collision projea2_readonly@% (attendu 0)       0
  compte applicatif intact (attendu 1)           1
OK — aucun secret exposé, compte applicatif intact.
...
✅ PHASE 1 TERMINÉE.
```

Le script est **rejouable** : en cas d'échec, corriger et relancer la même commande.
⚠️ Chaque exécution **réinitialise** le mot de passe de `projea2_mcp` **et** réécrit `projea2.env`
— les deux restent cohérents, mais il faut redémarrer le conteneur ensuite
(`docker compose up -d mcp-projea2`).

Le mot de passe reste lisible **en root** dans `/opt/twinl_mcps/mcps/projea2.env` — c'est de là
qu'il faut le recopier dans 1Password (titre `MCP projea2 projea2_mcp`), comme pour les autres
instances. `/root/projea2_setup.local.sql` le contient aussi (chmod 600, root) et sert aux
ré-exécutions.

> **Pourquoi ce n'est pas Claude qui l'a lancé** : le harness bloque les commandes qui combinent
> mutation de la prod et manipulation de secrets ([`PITFALLS.md`](PITFALLS.md) §5) — il a refusé
> même une simple inspection de `mysql.user` + des chemins de `.env`. C'est la garde attendue.

### Si un contrôle est au rouge

| Ligne | Sens | Geste |
|---|---|---|
| `vues créées` ≠ 45 | le schéma de `projea2` a bougé | regarder `tables source sans vue` : une table neuve doit être **exposée ou exclue** dans le DDL, puis relancer |
| `colonnes secrètes exposées` > 0 | 🛑 une vue laisse fuiter un secret | **ne pas continuer** — le DDL a été modifié à tort |
| `tables exclues exposées` > 0 | 🛑 une table interdite a une vue | idem |
| `collision projea2_readonly@%` > 0 | un compte homonyme du MCP existe | voir §1.5 — arbitrer avant d'aller plus loin |
| `compte applicatif intact` = 0 | le compte des listes Expert/IA a disparu | rien à voir avec le MCP, mais à traiter côté PROJEA2 |

### 1.4 Vérifier l'isolation depuis le VPS *(optionnel — le script a déjà tranché)*

Les 5 contrôles ci-dessous ont été **validés à blanc sur la MariaDB de dev** (2026-07-31), sorties
réelles à l'appui. À rejouer sur le VPS pour confirmer l'état de la prod.

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

Le contrôle `compte_app` de la phase 1 sert exactement à ça : voir les deux comptes côte à côte et
constater qu'aucun `projea2_readonly@%` n'existe.

**Confirmé sur la prod le 2026-07-31** (inspection read-only) : `projea2_readonly@127.0.0.1`,
`projea2_app@127.0.0.1` et `projea2_admin@127.0.0.1` existent bien, et aucun `projea2_mcp` — la
collision était réelle, le renommage n'est pas théorique.

---

## Phase 2 — WorkOS : déclarer la nouvelle ressource  *(dashboard)*

Rien à créer : on réutilise le **même** projet AuthKit (donc le même `AUTHKIT_DOMAIN`) et
l'invite-only déjà en place.

1. **Connect → Configuration → MCP Auth → MCP resource indicators** : ajouter
   `https://mcp-projea2.twinl.fr/mcp` (et, sans risque, `https://mcp-projea2.twinl.fr`).
2. Ne rien changer d'autre (DCR + CIMD restent activés, « Sign up » reste désactivé).

> Si l'indicateur n'est pas ajouté, claude.ai reçoit `error=invalid_target` au moment du login.

---

## Phase 3 — DNS  *(manager **OVH** — pas le VPS)*

⚠️ **Correction du 2026-07-31.** `INSTALL_PROCEDURE_HTTPS.md` disait « zone `twinl.fr` du BIND (VPS)
+ `rndc reload` ». C'est **faux** : vérifié sur le VPS, `bind9`/`named` sont **inactifs**,
`/etc/bind/named.conf.local` ne déclare aucune zone, et les NS autoritatifs de `twinl.fr` sont
**`ns111.ovh.net` / `dns111.ovh.net`** (SOA `dns111.ovh.net`). La zone est hébergée **chez OVH**.

Dans le **manager OVH** → *Noms de domaine* → `twinl.fr` → *Zone DNS* → **Ajouter une entrée** :

| Champ | Valeur |
|---|---|
| Type | `A` |
| Sous-domaine | `mcp-projea2` |
| Cible | `54.38.35.104` |
| TTL | défaut |

C'est la même opération que pour `mcp-iafec` et `mcp-projea`, qui résolvent déjà vers cette IP.

Vérifier (depuis le VPS ; `dig` n'est pas installé sur le poste Windows) :

```bash
ssh vps "getent hosts mcp-projea2.twinl.fr"     # → 54.38.35.104 mcp-projea2.twinl.fr
```

**Attendre que ça réponde avant la phase 5** : `certbot` valide le domaine par HTTP-01, donc le nom
doit résoudre publiquement, sinon l'émission du certificat échoue.

---

## Phase 4 — Conteneur  *(`ssh vps-ethan`, compte docker)*

> **Déjà fait le 2026-07-31** : le code est à jour sur le VPS. `/opt/twinl_mcps` **n'était pas un
> dépôt git** (fichiers copiés à la main, d'où l'inutilité du `git pull` que ce runbook prescrivait) ;
> il a été converti en **clone réel** de `origin/master` — sauvegarde préalable dans
> `/home/ethan/twinl_mcps_backup_<horodatage>.tar.gz`, les `.env` (non suivis) intacts. Les mises à
> jour suivantes sont donc bien des `git pull`.

```bash
cd /opt/twinl_mcps && git pull             # à jour = 7ad8f5e ou plus récent
cd /opt/twinl_mcps/mcps

# projea2.env est écrit par la phase 1 — ne pas le recréer ici.
docker compose build mcp-projea2
docker compose up -d mcp-projea2
docker compose ps                          # attendre "healthy"
docker compose logs --tail=40 mcp-projea2  # doit logger "instructions loaded from file (N chars)"
                                           # et "tool digest extracted (N chars)"
curl -s http://127.0.0.1:8083/health       # {"status":"ok"}
```

`MCP_PORT` reste **8080** (interne au conteneur ; la différenciation se fait par le mapping hôte
`127.0.0.1:8083` déclaré dans `docker-compose.yml`).

⚠️ **Tant que `projea2.env` n'existe pas** (donc tant que la phase 1 n'est pas jouée), un
`docker compose up -d` **sans nom de service** échoue :
`env file /opt/twinl_mcps/mcps/projea2.env not found`. Les commandes ciblées
(`docker compose up -d mcp-iafec`, `... ps`, `... logs`) fonctionnent normalement, et **les
conteneurs en service ne sont pas affectés** (vérifié : `mcp-iafec` et `mcp-projea` restent
`Up (healthy)`). Ordre à respecter : phase 1 d'abord.

**Approbation Slack — on REPREND l'app existante et on la déplace.** Puisque `mcp-projea` sera
fermé (§Phase 7), il n'y a jamais deux instances à servir durablement : l'app Slack de projea
**devient** celle de projea2. Rien à créer.

1. Recopier **à l'identique** dans `projea2.env`, depuis `projea.env` :

   ```ini
   SLACK_WEBHOOK_URL=<identique à projea.env>       # même canal
   SLACK_SIGNING_SECRET=<identique à projea.env>    # même app, donc même secret
   SLACK_NOTIFY_THRESHOLD=200
   SLACK_BYTES_THRESHOLD=50000
   SLACK_APPROVAL_TIMEOUT_S=120
   ```

2. Côté Slack (api.slack.com/apps → l'app existante → **Interactivity & Shortcuts**), remplacer la
   **Request URL** :

   ```
   avant :  https://mcp-projea.twinl.fr/slack/action
   après :  https://mcp-projea2.twinl.fr/slack/action
   ```

   Slack n'accepte **qu'une seule** Request URL par app : c'est un déménagement, pas un ajout.

⚠️ **Pendant la période de recouvrement** (projea2 en service, projea pas encore fermé),
l'approbation interactive ne fonctionne que pour **l'instance visée par la Request URL**. À faire
dans ce sens-là — projea2 d'abord — parce que c'est elle qui porte la donnée de référence. Ce que
devient projea entre-temps : ses demandes d'approbation partent bien dans Slack, mais le clic est
livré à projea2, qui ne connaît pas le jeton, le journalise (`unknown or expired request_id`) et
laisse projea attendre jusqu'à `SLACK_APPROVAL_TIMEOUT_S`. **Échec silencieux, pas fuite** : sans
approbation, `check_extraction_approval` ne rend jamais les données. Si un export legacy est
nécessaire pendant cette fenêtre, vider les 2 variables Slack de `projea.env` et redémarrer
`mcp-projea` : l'approbation est alors désactivée pour lui et les plafonds lignes/octets suffisent.

> Le message Slack nomme désormais l'instance (`MCP_SERVER_NAME`), donc si les deux postent dans le
> même canal pendant la transition, on voit tout de suite laquelle demande quoi.

Vérification locale (avant Apache) :

```bash
curl -s http://127.0.0.1:8083/health      # {"status":"ok"}
```

---

## Phase 5 — Apache + TLS  *(`ssh vps`, root)*

**Une seule commande**, comme la phase 1 :

```bash
ssh vps "sudo -n bash /opt/twinl_mcps/mcps/deploy/projea2_apache.sh"
```

Elle exécute [`../mcps/deploy/projea2_apache.sh`](../mcps/deploy/projea2_apache.sh), qui enchaîne
les trois étapes **dans l'ordre qui ne casse rien**, puis vérifie le résultat de bout en bout.

### 🛑 Pourquoi l'ordre compte

Apache **refuse de démarrer** sur un `SSLCertificateFile` introuvable. Poser le vhost `:443` avant
d'avoir le certificat couperait **tout le VPS** — qui sert aussi ISPConfig, la Dataroom et
`projea2.twinl.fr`. D'où : **alias `:80` → certificat → vhost `:443`**.

| Étape | Ce qu'elle fait |
|---|---|
| A | ajoute `ServerAlias mcp-projea2.twinl.fr` au vhost `*:80` → ouvre le challenge ACME |
| B | `certbot certonly --webroot` → émet le certificat, **sans toucher à aucun vhost** |
| C | ajoute le vhost `*:443`, **extrait des sentinelles** de `apache-mcp.conf.example` |
| D | vérifie : `/health` 200, `/mcp` 401, HTTP→HTTPS 301, pas de compression, `resource` annoncée |

`certonly --webroot` plutôt que `--apache` : ce dernier fabriquerait son propre vhost SSL, sans
`no-gzip` ni `flushpackets` — le streaming MCP serait cassé.

### Les filets

- **sauvegarde horodatée** de `mcp.conf` avant chaque modification (`mcp.conf.bak-<date>-A|C`) ;
- **`apachectl configtest` avant chaque reload** ; s'il échoue, la sauvegarde est **restaurée** et
  le script s'arrête **sans avoir rechargé** — une config cassée n'atteint jamais Apache ;
- **idempotent** : chaque étape se saute si elle est déjà faite. Rejouable sans risque.
- Le vhost n'est **pas recopié en dur** dans le script : il est extrait de
  [`apache-mcp.conf.example`](../mcps/deploy/apache-mcp.conf.example) entre ses sentinelles
  `# >>> BEGIN mcp-projea2` / `# <<< END mcp-projea2`, qui reste la source unique.

### Prérequis vérifiés par le script lui-même

Il refuse de démarrer si le DNS ne résout pas (phase 3) ou si le conteneur ne répond pas sur 8083
(phase 4) — inutile d'émettre un certificat pour un service absent.

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

## Phase 7 — Fermer `mcp-projea` (une fois projea2 recetté)

`mcp-projea` (base legacy `twinl`) n'a plus de raison d'être une fois projea2 en service : c'est
**la même donnée métier**, et deux connecteurs concurrents sur le même sujet invitent à mélanger
deux modèles incompatibles. À faire **après** la recette de projea2, pas pendant.

### 🛑 Ce qu'il ne faut PAS supprimer

Fermer le MCP ≠ démonter la couche données. Le user `projea_readonly` et la DB miroir
`twinl_readonly` sont **aussi** utilisés par le pipeline de migration de PROJEA2
(`LEGACY_DATABASE_URL` de `/opt/projea2/backend/.env`, cf. `projea2/deploy/setup-vps.sh`), et la
migration reste **rejouable** (`deploy/deploy.sh --reset-migration`).

| Objet | Sort |
|---|---|
| Connecteur `mcp-projea` dans claude.ai | **supprimer** |
| Conteneur `mcp-projea` + `projea.env` | **arrêter / retirer** |
| Vhost `mcp-projea.twinl.fr` + cert + DNS | **retirer** (ou laisser, inoffensif) |
| Resource indicator WorkOS `mcp-projea.twinl.fr/mcp` | **retirer** |
| User MariaDB `projea_readonly` | ⛔ **GARDER** — migration PROJEA2 |
| DB miroir `twinl_readonly` + ses vues | ⛔ **GARDER** — migration PROJEA2 |
| Base source `twinl` | ⛔ **GARDER** — archive (décision D29 : on n'y touche pas) |

### Ordre des gestes

```bash
# 1. claude.ai → Settings → Connectors → supprimer « mcp-projea »
#    (à faire EN PREMIER : sinon le connecteur reste listé et échoue en silence)

# 2. arrêter le conteneur   (ssh vps-ethan)
cd /opt/twinl_mcps/mcps
docker compose stop mcp-projea && docker compose rm -f mcp-projea

# 3. Apache   (ssh vps, sudo) — retirer le <VirtualHost *:443> de mcp-projea
#    et son ServerAlias sur le vhost *:80, puis :
sudo apachectl configtest && sudo systemctl reload apache2
```

Puis, dans ce repo : retirer le service `mcp-projea` de `docker-compose.yml`, son vhost de
`deploy/apache-mcp.conf.example`, et **archiver** `mcps/instructions/projea.md` plutôt que le
supprimer — il documente la sémantique de `twinl`, qui reste l'archive et la source de la migration.

> Si un besoin ponctuel de requête legacy réapparaît après la fermeture, tout est encore là côté
> MariaDB : il suffit de relancer le conteneur (`docker compose up -d mcp-projea`) et de recréer le
> connecteur. C'est précisément pour ça qu'on ne démonte pas la couche données.

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

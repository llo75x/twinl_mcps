# Projea / twinl — instructions du MCP (cartographie + règles métier)

> Ces instructions sont délivrées **automatiquement** par le serveur MCP `mcp-projea` (champ MCP
> standard `instructions`) à tout client : claude.ai web, Claude Desktop, Cowork, Claude Code.
> Tu interroges la base **Projea** (ERP, front MS Access ; données MariaDB `twinl`) via l'outil
> `mysql_query(sql)`, en **lecture seule** (tout `INSERT/UPDATE/DELETE/DDL` est refusé : GRANT
> SELECT only + vues `SQL SECURITY DEFINER` + garde SELECT-only côté serveur). Tu peux écrire
> n'importe quel `SELECT`.

<!-- DIGEST -->
Base **Projea** (ERP twinl), lecture seule. **NE DÉDUIS JAMAIS** une catégorie métier (secteur, ESN…) du code NAF ou du nom de la société : elle est définie **explicitement** via les qualifications.

Tables clés : `bdd` (sociétés, PK `idinterne`) · `dirigeants` (`id_bdd`→bdd ; email = `email_dirigeant`) · `tb_qualifications`↔`tb_qualifiants` (métiers) · `fonctions_cibles` (fonctions + `niveau`) · `tb_events` (interactions/mailings) · `tb_CodeStatut` (lookup universel — TOUJOURS joindre `AND Type='…'`).

Codes impératifs :
- Entreprise active : `bdd.selection IN (104,105,41,45)` (ignorer `43` = filiale).
- Email valide (emailing) : `dirigeants.statut_email IN (0,180)`.
- Décideur : `fonctions_cibles.niveau >= 6`. Dirigeant actif : `dirigeants.code_fonction <> 91`.
- Pays : France=`59`, Belgique=`17`. Genre : `1`=M., `2`=Mme.
- **Métier ESN/IT** = société qualifiée via `tb_qualifications`+`tb_qualifiants` avec `type=1 AND selection=2` (**PAS** via le NAF).
- Mailing envoyé = `tb_events.type_event=50`, code du mailing dans `tb_mailing_id_mailing`.

Pour le schéma complet, les patterns SQL et les règles de dédoublonnage emailing → appelle l'outil `get_data_model_reference`.
<!-- /DIGEST -->

---

## 0. À lire EN PREMIER

### ⚠️ Le modèle ci-dessous est PARTIEL

Sur les 70 tables exposées, **15 sont documentées en détail** ici (les plus centrales).
Les 55 autres sont listées en §10 mais non documentées sémantiquement.

### 🔄 Protocole d'auto-enrichissement (IMPORTANT)

Si l'utilisateur fait référence à une donnée / un champ **non documenté** ici :

1. **NE PAS deviner** le nom de colonne ou la table. C'est la source d'erreur n°1.
2. **D'abord, t'aider toi-même** : introspecte le schéma réel via
   ```sql
   SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
   FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = 'twinl_readonly'
     AND (COLUMN_NAME LIKE '%<mot-clé>%' OR TABLE_NAME LIKE '%<mot-clé>%')
   ORDER BY TABLE_NAME, ORDINAL_POSITION;
   ```
3. **Si l'ambiguïté persiste, DEMANDER à l'utilisateur** : « À quelle table/colonne de twinl fais-tu référence pour `<concept>` ? »
4. **Après sa réponse** : traite la requête ET **propose d'ajouter la clarification à ce référentiel d'instructions** (fichier `mcps/instructions/projea.md` du repo `twinl_mcps`) pour enrichir le modèle au fil de l'eau.

### ⚠️ `idinterne` est la clé pivot de tout l'ERP

`bdd.idinterne` (l'ID d'une société) se retrouve dans **presque toutes les tables** sous des
noms variés : `id_bdd`, `bdd_idinterne`, `tb_bdd_idinterne`, `maison_mere`. Repère toujours
quelle colonne d'une table pointe vers `bdd.idinterne`.

### ⚠️ `tb_CodeStatut` est la table de lookup universelle

Quasiment tous les codes de l'ERP (statuts, types, langues, régions…) se résolvent via
`tb_CodeStatut`, **discriminée par sa colonne `Type`**. Voir §3.2 et §4.

---

## 1. Périmètre (mai 2026)

| Entité | Table | Lignes |
|---|---|--:|
| Sociétés (entités juridiques) | `bdd` | 59 483 |
| Dirigeants | `dirigeants` | 96 805 |
| Projets | `tb_projets` | 233 |
| Cibles (société × projet) | `tb_cibles` | 22 532 |
| Cibles-dirigeants | `tb_cibles_dirigeants` | 21 941 |
| Events (interactions) | `tb_events` | 183 981 |
| Mailings | `tb_mailing` | 253 |
| Historique mailing | `tb_historic_mailing` | 32 634 |
| Qualifiants (métiers — référentiel) | `tb_qualifiants` | 499 |
| Qualifications (société × métier) | `tb_qualifications` | 42 645 |
| Codes (lookup universel) | `tb_CodeStatut` | 702 |
| Codes postaux | `code_postaux` | 38 869 |
| Pays | `country_list` | 192 |
| Codes NAF | `codification_naf` | 1 299 |
| Fonctions dirigeants (référentiel) | `fonctions_cibles` | 117 |

---

## 2. Architecture entité-relation (cœur)

```
                          ┌──────────────────────┐
                          │        bdd           │  ← TABLE CENTRALE (société = entité juridique)
                          │ idinterne (PK)       │
                          │ raison_sociale       │
                          │ selection, taille_ca │
                          │ maison_mere ─────────┼──┐ (auto-référence : société mère du groupe)
                          └───┬──────────────────┘  │
            ┌─────────────────┼─────────────────────┘
            │                 │
   id_bdd   │        ┌────────┼──────────────┬────────────────┬─────────────────┐
            ▼        │ bdd_   │ bdd_          │ tb_bdd_        │ bdd_            │
     ┌────────────┐  │ idint. │ idinterne     │ idinterne      │ idinterne      │
     │ dirigeants │  ▼        ▼               ▼                ▼                │
     │ id_dirigeant│ ┌──────────────┐  ┌────────────┐  ┌──────────────┐  ┌──────────────────┐
     └─────┬──────┘ │tb_qualifica- │  │ tb_cibles  │  │  tb_events   │  │ tb_historic_     │
           │        │tions         │  │ id_tb_cible│  │  id_event    │  │ mailing          │
           │        │(société×     │  │ (société×  │  │ (interactions│  │ (société×        │
           │        │ métier)      │  │  projet)   │  │  société)    │  │  dirigeant×      │
           │        └──────┬───────┘  └─────┬──────┘  └──────────────┘  │  mailing)        │
           │     tb_qualif-│ id      proj_id │ cible_id                  └──────────────────┘
           │     iants_id  ▼                 │
           │        ┌──────────────┐  ┌──────▼───────────┐    ┌──────────────┐
           │        │ tb_qualifiants│ │ tb_projets       │    │ tb_mailing   │
           │        │ id_qualifiant │ │ id_projet (PK)   │    │ id_mailing   │
           │        │ (métiers)     │ │ codeprojet       │    └──────────────┘
           │        └──────────────┘  └──────────────────┘
           │                                 ▲
           │  ┌───────────────────────┐      │ proj_id
           └─►│ tb_cibles_dirigeants  │──────┘
              │ (dirigeant référencé  │ cible_id → tb_cibles.id_tb_cible
              │  sur un projet)       │ dir_id   → dirigeants.id_dirigeant
              └───────────────────────┘

  Lookups transverses :
    bdd.pays         → country_list.id_country         (France = 59 ; Belgique = 17)
    bdd.nafnum       → codification_naf.id_naf
    bdd.id_codepostal→ code_postaux.id_codepostal
    dirigeants.code_fonction → fonctions_cibles.id_fonction
    <très nombreux codes>    → tb_CodeStatut (id_statut + Type discriminant)
```

---

## 3. Tables centrales — détail

### 3.1 `bdd` — sociétés (entité juridique pivot)

PK : `idinterne` (mediumint). 59 483 lignes. Nom société = `raison_sociale` (+ `raison_sociale2`).

| Colonne | Type | Sens |
|---|---|---|
| `idinterne` | mediumint | **PK — clé pivot de tout l'ERP** |
| `raison_sociale`, `raison_sociale2` | char | Dénomination sociale |
| `adresse`, `adresse2`, `codepostal`, `ville` | char | Adresse postale (texte libre) |
| `id_codepostal` | mediumint | → `code_postaux.id_codepostal` (code postal normalisé) |
| `pays` | smallint | → `country_list.id_country` (**France = 59 ; Belgique = 17**) |
| `nafnum` | smallint | → `codification_naf.id_naf` (code activité NAF) |
| `rcs` | char | N° RCS (unique mais souvent vide pour sociétés étrangères) |
| `selection` | smallint | **Statut marketing** → `tb_CodeStatut.id_statut` (Type=`'selection'`). Voir §4 (codes détaillés). |
| `taille_ca` | int | **CA en K€** |
| `type_CA` | tinyint | Type du `taille_ca` : `0`=CA comptable vérifié, `1`=CA consolidé ou non vérifié, `2`=CA estimé |
| `annee_ca` | varchar | Année de référence du CA |
| `commentaires` | mediumtext | Texte libre + **tags `§`** qualifiant activités/spécificités (ex. `§foncier`, `§saas`). **Donnée essentielle pour les analyses bien que peu remplie.** Rechercher via `commentaires LIKE '%§<tag>%'` |
| `maison_mere` | int | → `bdd.idinterne` de la **société mère** (groupe). Auto-référence. |
| `groupe`, `typesociete`, `website`, `telephone`, `annee_creation`, `sources`, `selection_mailing` | divers | Métadonnées secondaires |

> **Travailler au niveau groupe** : pour ne garder que les têtes de groupe, exclure les `selection=43`
> et/ou filtrer `maison_mere IS NULL OR maison_mere = idinterne`.

### 3.2 `tb_CodeStatut` — lookup universel ⭐

PK : `id_statut` (smallint). 702 lignes. **Table polymorphe** : un même `id_statut` peut exister
pour plusieurs `Type` différents → **toujours joindre AVEC le `Type`**, jamais sur `id_statut` seul.

| Colonne | Type | Sens |
|---|---|---|
| `id_statut` | smallint | Code (PK dans le contexte d'un Type) |
| `Type` | varchar | **Discriminant** : `'selection'`, `'langue'`, `'statut_ema'`, `'Type_proj'`, `'Projet1'`, `'Projet'`, `'E'`, `'Métropole'`, `'Région'`, … |
| `Libelle` | varchar | Libellé lisible du code |
| `Tri` | tinyint | Ordre d'affichage |
| `code_NZ`, `p1_num`, `p2_string` | divers | Paramètres additionnels selon le type |

**Correspondances Type → colonne source** (connues à ce jour) :

| `tb_CodeStatut.Type` | Colonne qui référence | Sens |
|---|---|---|
| `selection` | `bdd.selection` | Statut marketing société |
| `langue` | `dirigeants.langue` | Langue parlée |
| `statut_ema` | `dirigeants.statut_email` | Statut email (validé, désinscrit…) |
| `Type_proj` | `tb_projets.typeprojet` | Type de projet |
| `Projet1` | `tb_projets.statut_projet` | Statut du projet |
| `Projet` | `tb_cibles.statut_cible` | Statut d'une cible dans un projet |
| `E` | `tb_events.type_event` | Type d'event/interaction |
| `Métropole` | `code_postaux.CodeMetropole` | Métropole |
| `Région` | `code_postaux.CodeRégion` | Région (⚠️ colonne **`CodeRégion`** avec accent → backticks) |

### 3.3 `dirigeants` — dirigeants (données nominatives)

PK : `id_dirigeant` (mediumint). 96 805 lignes. Lien société : `id_bdd` → `bdd.idinterne`.

| Colonne | Type | Sens |
|---|---|---|
| `id_dirigeant` | mediumint | PK |
| `id_bdd` | mediumint | → `bdd.idinterne` |
| `genre` | tinyint | `1`=M., `2`=Mme |
| `prenom_dirigeant`, `nom_dirigeant` | char | Identité |
| `annee_naissance` | smallint | |
| `code_fonction` | tinyint | Fonction **normalisée** → `fonctions_cibles.id_fonction`. `91` = fonction inactive (dirigeant inactif). |
| `fonction_claire` | char | Fonction **réelle** (texte libre) |
| `email_dirigeant` | char | **Email du dirigeant** (⚠️ PAS `email`). La table `dirigeants_email` est dépréciée. |
| `statut_email` | smallint | → `tb_CodeStatut` (Type=`'statut_ema'`). Codes en §4. |
| `langue` | tinyint | → `tb_CodeStatut` (Type=`'langue'`) |
| `telephone`, `miseajour`, `external_id`, `selection_mailing` | divers | |

### 3.4 `tb_projets` — projets

PK : `id_projet` (smallint). 233 lignes. `id_bdd` → `bdd.idinterne` (société liée au projet, ex. mandant).

| Colonne | Type | Sens |
|---|---|---|
| `id_projet` | smallint | PK |
| `id_bdd` | mediumint | → `bdd.idinterne` |
| `codeprojet` | char | Nom de code du projet |
| `nomprojet` | char | Nom du projet |
| `typeprojet` | tinyint | → `tb_CodeStatut` (Type=`'Type_proj'`) |
| `statut_projet` | tinyint | → `tb_CodeStatut` (Type=`'Projet1'`) |
| `date_debut`, `DateOffreIndicative` | date | Jalons |
| `projet_comment`, `teaser`, `fnct_speciale`, `open_webservices` | divers | |

### 3.5 `tb_cibles` — affectation société × projet (M:N)

PK : `id_tb_cible` (mediumint). 22 532 lignes. ⚠️ La PK est `id_tb_cible`, **pas** `id_cible`.

| Colonne | Type | Sens |
|---|---|---|
| `id_tb_cible` | mediumint | PK |
| `bdd_idinterne` | mediumint | → `bdd.idinterne` (la société ciblée) |
| `tb_projets_id_projet` | mediumint | → `tb_projets.id_projet` (le projet) |
| `statut_cible` | tinyint | → `tb_CodeStatut` (Type=`'Projet'`) |
| `prioritaire`, `dataroom` | tinyint | Flags |
| `commentaire`, `teaser`, `date_ajout`, `date_update`, `DateOffreIndicative` | divers | |

### 3.6 `tb_cibles_dirigeants` — dirigeants référencés sur un projet

PK : `id_CibleDirigeant`. 21 941 lignes. Quand une société est cible d'un projet, 1..n de ses dirigeants y sont rattachés.

| Colonne | Type | Sens |
|---|---|---|
| `id_CibleDirigeant` | mediumint | PK |
| `tb_cibles_id_tb_cible` | smallint | → `tb_cibles.id_tb_cible` |
| `dirigeants_id_dirigeant` | mediumint | → `dirigeants.id_dirigeant` |
| `tb_projets_id_projet` | mediumint | → `tb_projets.id_projet` |
| `qualif` | tinyint | Qualification du rôle |

### 3.7 `tb_events` — interactions / actualités (dont mailings)

PK : `id_event` (mediumint). 183 981 lignes. Toujours lié à une société ; optionnellement à un dirigeant, un projet, une cible.

| Colonne | Type | Sens |
|---|---|---|
| `id_event` | mediumint | PK |
| `tb_bdd_idinterne` | mediumint | → `bdd.idinterne` (société concernée — **toujours présent**) |
| `dirigeants_id_dirigeant` | mediumint | → `dirigeants.id_dirigeant` (si l'event concerne un dirigeant) |
| `tb_projets_id_projet` | mediumint | → `tb_projets.id_projet` (si lié à un projet) |
| `tb_cibles_IDcible` | smallint | → `tb_cibles.id_tb_cible` (si la société est cible de ce projet) |
| `type_event` | smallint | → `tb_CodeStatut` (Type=`'E'`). **`type_event = 50` = envoi de mailing.** |
| `tb_mailing_id_mailing` | smallint | → `tb_mailing.id_mailing` (code du mailing, si l'event est un mailing) |
| `event_comment` | varchar | Texte de l'interaction |
| `Udate_event`, `udate_saisie` | int/timestamp | Dates (epoch Unix → `FROM_UNIXTIME`) |

### 3.8 `tb_mailing` + `tb_historic_mailing` — campagnes mailing

`tb_mailing` (253) = référentiel des campagnes. `tb_historic_mailing` (32 634) = envois individuels.

| `tb_historic_mailing` | Type | Sens |
|---|---|---|
| `id_historic_mailing` | int | PK |
| `id_dirigeant` | int | → `dirigeants.id_dirigeant` |
| `bdd_idinterne` | mediumint | → `bdd.idinterne` |
| `id_mailing` | tinyint | → `tb_mailing.id_mailing` |
| `id_resultat_mailing` | tinyint | Résultat de l'envoi |
| `date_retour` | date | |
| `id_email_dirigeant` | int | **Déprécié** |

`tb_mailing` : `id_mailing` (PK), `libelle_mailing`, `codemailing`, `date_envoi`, `nombre_courrier`, `type_mailing`.

> Deux traces du mailing coexistent : l'**event** `tb_events` (`type_event=50`, `tb_mailing_id_mailing`)
> et l'**historique** `tb_historic_mailing`. Pour l'exclusion « déjà contacté » d'un ciblage, voir §6.

### 3.9 `tb_qualifiants` + `tb_qualifications` — métiers des sociétés

`tb_qualifiants` (499) = référentiel des métiers. `tb_qualifications` (42 645) = société × métier (M:N).

| `tb_qualifiants` | Type | Sens |
|---|---|---|
| `id_qualifiant` | smallint | PK |
| `Libelle` | char | **Nom du métier** (⚠️ `Libelle` majuscule, sans accent) |
| `type` | tinyint | Type de qualifiant. **Métier d'entreprise = `type = 1`.** |
| `selection` | tinyint | Famille de regroupement. **Famille ESN / IT = `selection = 2`** (éditeurs logiciels, SSII, infogérance, SAAS, datacenter… regroupe « ESN - … » et « IT - … »). |

| `tb_qualifications` | Type | Sens |
|---|---|---|
| `id_qualification` | mediumint | PK |
| `bdd_idinterne` | mediumint | → `bdd.idinterne` |
| `tb_qualifiants_id_qualifiant` | smallint | → `tb_qualifiants.id_qualifiant` |
| `opportunite` | tinyint | |

### 3.10 Référentiels transverses

| Table | PK | Colonnes clés |
|---|---|---|
| `country_list` | `id_country` (smallint) | `french_name`, `english_name`, `code`. **France = 59 ; Belgique = 17** |
| `code_postaux` | `id_codepostal` (int) | `CodePostal`, `NomVille`, `CodeINSEE`, `CodeRégion` (→tb_CodeStatut Type='Région'), `CodeMetropole` (→Type='Métropole'), `Vacances` (zone de vacances **française**) |
| `codification_naf` | `id_naf` (int) | `code_naf`, `libelle` |
| `fonctions_cibles` | `id_fonction` (smallint) | `fonction`, `niveau`, `selection_email`, `selection_fonds_invest`. **`niveau` = niveau de décision** (cf. §4). |

---

## 4. Codes / énumérations connus

| Champ | Valeurs | Résolution |
|---|---|---|
| `bdd.type_CA` | `0`=comptable vérifié, `1`=consolidé/non vérifié, `2`=estimé | en dur |
| `bdd.pays` | France = `59` · Belgique = `17` | `country_list.id_country` |
| `dirigeants.genre` | `1`=M., `2`=Mme | en dur |
| `dirigeants.code_fonction` | `91` = fonction inactive (**dirigeant inactif** → `<> 91`) | `fonctions_cibles.id_fonction` |
| Tous les autres codes | — | `tb_CodeStatut` via le bon `Type` (cf. §3.2) |

### 4.1 `bdd.selection` — statut marketing société (Type=`'selection'`)

| Code | Libellé | Usage |
|--:|---|---|
| `104` | Suspect Cession | **entreprise active** |
| `105` | Suspect Acquisition | **entreprise active** |
| `41` | Suspect Cession & Acquisition | **entreprise active** |
| `45` | Filiale groupe étranger | **entreprise active** |
| `42` | Client | utile selon contexte |
| `43` | Filiale (d'une autre société Projea) | **à IGNORER** : on travaille au niveau du groupe le plus élevé |
| `47` | Liquidée | exclure des cibles |
| `101` | RJ / Sauvegarde | contexte difficulté |
| `106` | Hors-cible | exclure |

> **Entreprise « active » (cible exploitable)** : `selection IN (104, 105, 41, 45)`.
> Pour le libellé lisible : joindre `tb_CodeStatut … AND Type='selection'`.

### 4.2 `dirigeants.statut_email` — statut e-mail (Type=`'statut_ema'`)

| Code | Sens | Emailing |
|--:|---|---|
| `0` | non qualifié (pas de libellé) | ✅ **valide** |
| `180` | Email validé | ✅ **valide** |
| `181` | Désinscrit | ❌ exclure |
| `182` | Erreur email | ❌ exclure |
| `183` | Blacklisté | ❌ exclure |

> **Règle emailing** : `statut_email IN (0, 180)`.

### 4.3 `fonctions_cibles.niveau` — niveau de décision

Plus le `niveau` est élevé, plus la fonction est décisionnaire. **Convention de ciblage : ne
contacter que les dirigeants dont la fonction a `niveau >= 6`** (décideurs).

---

## 5. Patterns de requête courants

### 5.1 Résoudre un code via `tb_CodeStatut` (pattern fondamental)

```sql
SELECT b.idinterne, b.raison_sociale, b.selection, cs.Libelle AS statut_marketing
FROM bdd b
LEFT JOIN tb_CodeStatut cs
       ON cs.id_statut = b.selection AND cs.Type = 'selection'
WHERE b.idinterne = ?;
```

> **Toujours inclure `AND cs.Type = '<type>'`** dans le JOIN, sinon collision entre types.

### 5.2 Sociétés d'un statut marketing donné, avec CA et localisation

```sql
SELECT b.idinterne, b.raison_sociale, b.taille_ca AS ca_keur, b.type_CA,
       cp.NomVille, c.french_name AS pays, naf.libelle AS activite
FROM bdd b
LEFT JOIN code_postaux cp     ON cp.id_codepostal = b.id_codepostal
LEFT JOIN country_list c      ON c.id_country     = b.pays
LEFT JOIN codification_naf naf ON naf.id_naf       = b.nafnum
WHERE b.selection IN (41, 45, 104, 105)
ORDER BY b.taille_ca DESC;
```

### 5.3 Têtes de groupe uniquement (exclure filiales)

```sql
SELECT idinterne, raison_sociale, taille_ca
FROM bdd
WHERE (maison_mere IS NULL OR maison_mere = 0 OR maison_mere = idinterne)
  AND selection <> 43
ORDER BY taille_ca DESC;
```

### 5.4 Dirigeants d'une société avec fonction lisible

```sql
SELECT d.id_dirigeant, d.genre, d.prenom_dirigeant, d.nom_dirigeant,
       d.fonction_claire, fc.fonction AS fonction_normalisee, d.email_dirigeant
FROM dirigeants d
LEFT JOIN fonctions_cibles fc ON fc.id_fonction = d.code_fonction
WHERE d.id_bdd = ?;
```

### 5.5 Sociétés cibles d'un projet (avec leur statut dans le projet)

```sql
SELECT p.codeprojet, p.nomprojet, b.idinterne, b.raison_sociale,
       cs.Libelle AS statut_cible
FROM tb_projets p
JOIN tb_cibles t       ON t.tb_projets_id_projet = p.id_projet
JOIN bdd b             ON b.idinterne = t.bdd_idinterne
LEFT JOIN tb_CodeStatut cs ON cs.id_statut = t.statut_cible AND cs.Type = 'Projet'
WHERE p.codeprojet = ?
ORDER BY b.raison_sociale;
```

### 5.6 Historique des interactions (events) d'une société

```sql
SELECT e.id_event, FROM_UNIXTIME(e.Udate_event) AS date_event,
       cs.Libelle AS type_interaction, e.event_comment, d.nom_dirigeant, p.codeprojet
FROM tb_events e
LEFT JOIN tb_CodeStatut cs ON cs.id_statut = e.type_event AND cs.Type = 'E'
LEFT JOIN dirigeants d     ON d.id_dirigeant = e.dirigeants_id_dirigeant
LEFT JOIN tb_projets p     ON p.id_projet = e.tb_projets_id_projet
WHERE e.tb_bdd_idinterne = ?
ORDER BY e.Udate_event DESC;
```

### 5.7 Métiers (qualifications) d'une société

```sql
SELECT b.raison_sociale, q.Libelle AS metier
FROM tb_qualifications tq
JOIN bdd b           ON b.idinterne = tq.bdd_idinterne
JOIN tb_qualifiants q ON q.id_qualifiant = tq.tb_qualifiants_id_qualifiant
WHERE tq.bdd_idinterne = ?
ORDER BY q.Libelle;
```

### 5.8 Recherche par tag `§` dans les commentaires

```sql
SELECT idinterne, raison_sociale, taille_ca, commentaires
FROM bdd
WHERE commentaires LIKE '%§saas%'      -- remplacer par le tag cherché
ORDER BY taille_ca DESC;
```

### 5.9 Historique mailing d'un dirigeant

```sql
SELECT m.libelle_mailing, m.date_envoi, hm.date_retour, hm.id_resultat_mailing
FROM tb_historic_mailing hm
JOIN tb_mailing m ON m.id_mailing = hm.id_mailing
WHERE hm.id_dirigeant = ?
ORDER BY m.date_envoi DESC;
```

---

## 6. Ciblage emailing — règles métier (sélection d'un mailing)

Cas d'usage récurrent : constituer la liste des contacts éligibles à un mailing donné, sur un
métier (ex. ESN), entreprises actives, décideurs actifs, en excluant ceux déjà contactés.

### 6.1 Critères d'éligibilité standard

- **Email valide** : `dirigeants.statut_email IN (0, 180)` (cf. §4.2) ; ignorer emails NULL/vides.
- **Entreprise active** : `bdd.selection IN (104, 105, 41, 45)` (cf. §4.1).
- **Décideur** : `fonctions_cibles.niveau >= 6` (cf. §4.3).
- **Dirigeant actif** : `dirigeants.code_fonction <> 91`.
- **Géographie** (ex. France + Belgique) : `bdd.pays IN (59, 17)`.
- **Métier ESN** : la société a une qualification dans la famille ESN/IT → existe une ligne
  `tb_qualifications` jointe `tb_qualifiants` avec `type = 1 AND selection = 2`.

### 6.2 Règles de dédoublonnage (impératives)

1. **Email unique** : une adresse `email_dirigeant` n'est sélectionnée qu'une seule fois.
2. **Entreprise multi-métiers** : une société à plusieurs métiers ESN ne compte que pour **un
   contact** (ne pas multiplier par le nombre de qualifications → utiliser `EXISTS`, pas un `JOIN`).
3. **Contact multi-entreprises** : un même contact présent dans plusieurs sociétés n'est pris qu'une fois.
4. **Ventilation par métier** (si on répartit le total par métier) : affecter chaque email à un seul
   métier — convention retenue : le qualifiant ESN d'`id_qualifiant` le plus petit. Convention
   **arbitraire**, faute de champ « métier principal » ; à fixer si une telle règle existe côté métier.
5. Ignorer les emails vides/NULL.

### 6.3 Exclusion « déjà contacté » par un mailing — sur les DEUX clés

Pour ne pas recontacter une cible déjà servie par le mailing `<code>` : exclure **à la fois**
le `id_dirigeant` qui a déjà l'event ET l'`email_dirigeant` qui correspond à un dirigeant ayant
déjà reçu ce mailing (le même email peut exister sous plusieurs `id_dirigeant`).

### 6.4 Requête type — mailing ESN (France + Belgique), hors déjà-contactés (mailing 254)

```sql
SELECT DISTINCT d.email_dirigeant
FROM dirigeants d
JOIN bdd b               ON b.idinterne   = d.id_bdd
JOIN fonctions_cibles fc ON fc.id_fonction = d.code_fonction
WHERE d.statut_email IN (0, 180)               -- email valide
  AND b.pays IN (59, 17)                       -- France ou Belgique
  AND fc.niveau >= 6                           -- décideur
  AND d.code_fonction <> 91                    -- dirigeant actif
  AND b.selection IN (104, 105, 41, 45)        -- entreprise active
  AND d.email_dirigeant IS NOT NULL AND d.email_dirigeant <> ''
  -- métier ESN (EXISTS → pas de multiplication par qualification) :
  AND EXISTS (
        SELECT 1 FROM tb_qualifications q
        JOIN tb_qualifiants qf ON qf.id_qualifiant = q.tb_qualifiants_id_qualifiant
        WHERE q.bdd_idinterne = b.idinterne
          AND qf.type = 1 AND qf.selection = 2
      )
  -- exclure déjà-reçu mailing 254, sur id_dirigeant ET sur email :
  AND NOT EXISTS (
        SELECT 1 FROM tb_events e
        WHERE e.dirigeants_id_dirigeant = d.id_dirigeant
          AND e.type_event = 50 AND e.tb_mailing_id_mailing = 254
      )
  AND d.email_dirigeant NOT IN (
        SELECT d2.email_dirigeant FROM dirigeants d2
        JOIN tb_events e ON e.dirigeants_id_dirigeant = d2.id_dirigeant
        WHERE e.type_event = 50 AND e.tb_mailing_id_mailing = 254
          AND d2.email_dirigeant IS NOT NULL AND d2.email_dirigeant <> ''
      );
```

> Géographie fine (région / zone de vacances) : joindre `code_postaux` via
> `bdd.id_codepostal = code_postaux.id_codepostal` en **LEFT JOIN** (certaines sociétés, notamment
> hors France, n'ont pas de code postal rattaché). Les zones `code_postaux.Vacances` sont
> **françaises** → non pertinentes pour les sociétés belges (valeur vide possible).

---

## 7. Pièges (gotchas)

1. **Noms de colonnes contre-intuitifs** : `dirigeants.email_dirigeant` (pas `email`), `tb_cibles.id_tb_cible` (pas `id_cible`), `tb_qualifiants.Libelle` (majuscule), `code_postaux.CodeRégion` (accent → backticks). En cas de doute → introspecter `INFORMATION_SCHEMA.COLUMNS`.
2. **`tb_CodeStatut` polymorphe** : ne JAMAIS joindre sur `id_statut` seul. Toujours `AND Type = '<type>'`.
3. **`idinterne` sous des noms variés** : `id_bdd` (dirigeants), `bdd_idinterne` (tb_cibles, tb_qualifications, tb_historic_mailing), `tb_bdd_idinterne` (tb_events), `maison_mere` (auto-réf). Bien identifier la colonne avant de joindre.
4. **FK cibles incohérentes en nom** : la PK est `tb_cibles.id_tb_cible`, mais référencée comme `tb_cibles_id_tb_cible` (tb_cibles_dirigeants) et `tb_cibles_IDcible` (tb_events). Toutes pointent vers `id_tb_cible`.
5. **Dates en epoch** : `tb_events.Udate_event`, `tb_cibles.date_ajout`, `bdd.update_time` sont des **timestamps Unix (int)** → `FROM_UNIXTIME(col)` pour les lire.
6. **`taille_ca` est en K€**, pas en €. Sa fiabilité dépend de `type_CA` (0 vérifié > 1 > 2 estimé).
7. **Multiplication par qualification** : pour compter des sociétés/contacts « d'un métier », utiliser `EXISTS` (pas un `JOIN` sur `tb_qualifications`) sinon une société multi-métiers est comptée plusieurs fois (cf. §6.2).
8. **Tables dépréciées** : `dirigeants_email`, `tb_historic_mailing.id_email_dirigeant`. Ne pas les utiliser.
9. **Tables exclues du MCP** (invisibles, données sensibles) : `tb_users`, `tb_factures`, `tb_elements_facture`, `tb_paiement`, `tb_achats`, `tb_compta_achats`, `sys_droits`. Si une question porte dessus → répondre qu'elles ne sont pas accessibles.

---

## 8. Bonnes pratiques d'analyse

1. **Identifier la cible** : société (`idinterne`) ? projet (`codeprojet`) ? dirigeant ? Demander si flou.
2. **Filtrer le bruit marketing** : pour des analyses « groupe », exclure `selection=43` et privilégier les têtes de groupe (§5.3).
3. **Toujours résoudre les codes** via `tb_CodeStatut` pour des résultats lisibles, plutôt que d'afficher des id bruts.
4. **Tags `§`** : quand l'utilisateur parle de « sociétés du secteur X » ou « avec telle spécificité », penser à chercher dans `bdd.commentaires` (§5.8) en plus du code NAF.
5. **Nuancer le CA** : toujours mentionner `type_CA` (vérifié / consolidé / estimé) quand tu présentes `taille_ca`.
6. **Volumes** : `tb_events` (184k), `dirigeants` (97k), `bdd` (59k) sont volumineuses → filtrer par `idinterne`/projet/date plutôt que scanner toute la table.

---

## 9. Tables exposées non encore documentées (55)

Présentes dans le MCP mais sans sémantique documentée — **demander à l'utilisateur avant de les
utiliser** (ou introspecter leurs colonnes). Regroupées par nature probable :

- **Autres entités métier** : `tb_opportunites`, `tb_contrats`, `tb_contrats_elements`, `tb_commande`, `tb_commande_elements`, `tb_deals_it`, `tb_saas`, `tb_holding`, `tb_EntiteJuridique`, `tb_Partenariat`, `tb_GestionProjet_old`, `tb_GestionActivite`, `tb_FraisKM`, `tb_recherche_cibles`, `tb_bourse`, `tb_pappers`, `tb_groups`, `tb_user_groups`, `tb_reseaux_sociaux`, `tb_rs`, `tb_accesrapide`, `tb_selection_id`, `tb_projets_stats`, `tb_vdst_historic`, `tb_compta_sage`
- **Variantes / archives de bdd & dirigeants** : `bdd_chinois`, `bdd_Twix`, `dirigeants_cn`, `dirigeants_tmp`, `dirigeants_Twix`, `dirigeants_email` (déprécié), `code_postaux_old`
- **Imports / fichiers** : `file_NegoceMat`, `file_orias`, `file_rcs`, `brevoresultstoimportinprojea`, `restructuration_adresse`, `AspireAnnuaire`, `grasp_smes`, `ideals_smes`
- **Système** : `sys_FusionWord`, `sys_libelles`, `sys_logs`, `sys_segment`, `sys_SqlQueries`, `sys_stats`, `sys_tables`, `sys_tempo`
- **Web / tracking** : `w_emailing`, `w_visiteur`, `w_visiteur_arc`
- **Temporaires** : `tmp_resultats`, `tmp_saas_france`, `tmp_tracking`, `tmp_vdst`

> Note : ces tables contiennent potentiellement de la PII (`w_visiteur`, `w_emailing`, `dirigeants_email`).

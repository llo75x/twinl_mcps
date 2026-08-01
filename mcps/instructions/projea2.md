# PROJEA2 — instructions du MCP (cartographie + règles métier)

> Ces instructions sont délivrées **automatiquement** par le serveur MCP `mcp-projea2` (champ MCP
> standard `instructions`) à tout client : claude.ai web, Claude Desktop, Cowork, Claude Code.
> Tu interroges la base **PROJEA2** (CRM/ERP M&A de TwinL, application `projea2.twinl.fr`) via
> l'outil `mysql_query(sql)`, en **lecture seule** (tout `INSERT/UPDATE/DELETE/DDL` est refusé :
> GRANT SELECT only + vues `SQL SECURITY DEFINER` + garde SELECT-only côté serveur). Tu peux écrire
> n'importe quel `SELECT`.
>
> ✅ **« Projea », c'est ici.** Le MCP legacy `mcp-projea` (base `twinl`, Projea1 / MS Access) a
> été **fermé le 2026-08-01** : conteneur, vhost et connecteur retirés. Il n'y a plus qu'un seul
> MCP Projea, celui-ci. Quelle que soit la formulation de l'utilisateur — « avec projea », « dans
> projea », « en utilisant projea », « le CRM », « projea2 » — **c'est cette base qu'il désigne**,
> et tu réponds sans poser de question de désambiguïsation.
>
> `twinl` reste l'archive et la source du pipeline de migration, mais elle n'est plus interrogeable
> par MCP. Si une question porte explicitement sur l'ancien modèle (`bdd`, `dirigeants`,
> `tb_CodeStatut`…), dis que ce connecteur est fermé et propose l'équivalent PROJEA2 — les
> colonnes `legacy_*` (§5.10) font le pont.

<!-- DIGEST -->
**« Projea » = CETTE base.** « projea », « Projea », « le CRM », « la base Projea », « projea2 »,
« PROJEA2 » désignent tous le même endroit : ici. Le MCP legacy `mcp-projea` (base `twinl`) est
**fermé depuis le 2026-08-01** — il n'existe plus qu'un seul MCP Projea. Ne demande donc **jamais**
à l'utilisateur de choisir entre « projea » et « projea2 », et ne signale pas l'ambiguïté : elle
n'existe plus. Réponds directement sur cette base.

Base **projea2** (CRM/ERP M&A TwinL, réécriture de la base legacy `twinl`), lecture seule.

**Aucun entier magique** : tous les statuts/types se résolvent par **CODE**, via
`reference_values` (`code`) + `reference_types` (`code`) — toujours joindre les DEUX.

Modèle **personne / rattachement** : `companies` (sociétés ET personnes physiques) ↔
`company_contacts` (le **rattachement**) ↔ `contacts` (la personne). **Email, fonction, statut
email et téléphone sont portés par le RATTACHEMENT**, jamais par la personne. Les `events` et
`mail_deliveries` pointent le rattachement (`company_contact_id`).

Filtres impératifs :
- Société vivante : `companies.is_deleted = 0`.
- Personne vivante : `contacts.merged_into_contact_id IS NULL` (sinon fiche absorbée par fusion).
- Rattachement **actif** : `company_contacts.end_date IS NULL`.
- **Décideur** : `COALESCE(canon.level, f.level) >= 6` sur `functions` (`canonical_function_id`).
- **Email exploitable** : `email_status` = code `VALID`.
- **Hors sélections** : `crm_status` = code `NON_DECISIONNAIRE` (réservé aux actes juridiques).
- Deux axes de statut distincts : `crm_status_id` (commercial) et `legal_status_id` (juridique :
  `HOLDING`/`GROUP_HEAD`/`SUBSIDIARY`/`FOREIGN_GROUP_SUBSIDIARY`, NULL = indépendante).

**NE DÉDUIS JAMAIS** un métier/secteur du code NAF ni du nom : il est porté explicitement par
`company_tags` → `tags` → `tag_families` (`kind='BUSINESS'`, familles `sector`, `esn_it`,
`deal_ecosystem` ; `kind='HASHTAG'` = hashtags libres).

`events` = **interactions humaines** (timeline métier). `mail_deliveries` = **délivrabilité**
emailing (SENT/OPENED/CLICKED/…). Ne cherche pas les ouvertures/bounces dans `events`.

Les colonnes `legacy_*` (`legacy_idinterne`, `legacy_id_dirigeant`, `legacy_id_event`…) font le
pont avec la base `twinl` du MCP `mcp-projea`.

Pour le schéma complet, les référentiels et les patterns SQL → appelle `get_data_model_reference`.
<!-- /DIGEST -->

---

## 0. À lire EN PREMIER

### ⚠️ Les codes ne sont JAMAIS des entiers en dur

Contrairement à `twinl` (où `selection=104` signifiait quelque chose), PROJEA2 n'expose **aucun
entier métier**. Tout statut, type, langue ou genre est une ligne de `reference_values`, identifiée
par un **`code` texte stable** (ex. `'VALID'`, `'NON_DECISIONNAIRE'`, `'EMAIL_SENT'`), unique dans
son `reference_type`. Les `id` numériques de `reference_values` sont **des id de base, pas des
codes métier** : ils changent d'un environnement à l'autre (et d'une re-migration à l'autre).

> **Règle absolue** : ne filtre **jamais** sur un `*_id` de référentiel en dur.
> Filtre sur `rv.code` + `rt.code`. Voir le pattern §5.1.

### 🔄 Protocole d'auto-enrichissement (IMPORTANT)

Si l'utilisateur fait référence à une donnée / un champ **non documenté** ici :

1. **NE PAS deviner** le nom de colonne ou la table. C'est la source d'erreur n°1.
2. **D'abord, t'aider toi-même** : introspecte le schéma réel via
   ```sql
   SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
   FROM INFORMATION_SCHEMA.COLUMNS
   WHERE TABLE_SCHEMA = 'projea2_readonly'
     AND (COLUMN_NAME LIKE '%<mot-clé>%' OR TABLE_NAME LIKE '%<mot-clé>%')
   ORDER BY TABLE_NAME, ORDINAL_POSITION;
   ```
   Et la liste des valeurs d'un référentiel via le pattern §5.2.
3. **Si l'ambiguïté persiste, DEMANDER à l'utilisateur** : « À quel objet de PROJEA2 fais-tu
   référence pour `<concept>` ? »
4. **Après sa réponse** : traite la requête ET **propose d'ajouter la clarification à ce
   référentiel** (fichier `mcps/instructions/projea2.md` du repo `twinl_mcps`).

### ⚠️ Le rattachement (`company_contacts`) est la clé pivot du CRM

C'est **l'erreur la plus coûteuse** sur cette base. Une personne (`contacts`) ne porte que son
identité stable : genre, prénom, nom, année de naissance, langue. **Tout ce qui meurt quand elle
change de poste vit sur le rattachement** : fonction, email, statut d'email, téléphone,
`is_primary`, dates de début/fin.

Conséquences :
- « l'email d'un contact » = `company_contacts.email`, **jamais** `contacts.email` (n'existe pas) ;
- un contact peut avoir **plusieurs rattachements actifs** (multi-mandats de groupe) — un seul
  marqué `is_primary` ;
- « ancien contact » = rattachement **clos** (`end_date IS NOT NULL`), la personne n'est pas
  supprimée ;
- les `events` et `mail_deliveries` pendent au **rattachement**, donc à la société où
  l'interaction a eu lieu (les filiales gardent leurs events, rien ne remonte à la tête de groupe).

### ⚠️ `companies` contient aussi les personnes physiques

`entity_kind` = `COMPANY` ou `INDIVIDUAL` (repreneurs individuels). Une personne physique
« cliente/cible » est donc une ligne de `companies`, pas de `contacts`.

### ⚠️ Suppressions logiques et fusions

- `companies.is_deleted = 1` → société supprimée : **à exclure** de tout comptage.
- `contacts.merged_into_contact_id IS NOT NULL` → fiche **absorbée** par une fusion de doublons :
  elle n'a plus de rattachement et ne doit **jamais** apparaître dans un dénombrement de personnes.

---

## 1. Périmètre — volumétrie de référence (bascule définitive du 2026-07-31, prod v1.0.x)

| Entité | Table | Lignes |
|---|---|--:|
| Sociétés (et personnes physiques) | `companies` | 58 912 |
| Personnes | `contacts` | 94 953 |
| Rattachements personne × société | `company_contacts` | 95 453 |
| Events (interactions humaines) | `events` | 66 531 |
| Délivrabilité emailing | `mail_deliveries` | 104 164 |
| Opérations M&A | `operations` | 235 |
| Cibles (société × opération) | `operation_targets` | 23 294 |
| Contacts de cible | `operation_target_contacts` | 21 767 |
| Tags (métiers + hashtags) | `tags` | 550 |
| Liaisons société × tag | `company_tags` | 43 844 |
| Utilisateurs | `users` | 35 (2 actifs) |
| Codes postaux | `postal_codes` | ~38 900 |
| Pays | `countries` | 192 |
| Fonctions (référentiel) | `functions` | 117 |

> Ces nombres **bougent** : la base vit (création de sociétés, imports data.gouv, campagnes). Ne
> les cite pas comme un état courant — recompte.

---

## 2. Architecture entité-relation (cœur)

```
                    ┌──────────────────────────────┐
                    │         companies            │  ← sociétés ET personnes physiques
                    │ id (PK)                      │
                    │ legal_name, siren, revenue_* │
                    │ entity_kind_id  ──┐          │
                    │ crm_status_id   ──┼─► reference_values  (statut COMMERCIAL)
                    │ legal_status_id ──┘          │  (statut JURIDIQUE dans le groupe)
                    │ parent_company_id ───────────┼──┐ auto-référence : maison mère
                    └───┬────────────┬─────────────┘  │
                        │            │                │
        ┌───────────────┘            └────────────┐   │
        ▼                                          ▼   │
┌───────────────────────┐                  ┌──────────────────┐
│   company_contacts    │ ◄─ LE RATTACHEMENT│   company_tags   │
│ company_id, contact_id│                  │ company_id,tag_id│
│ function_id ─► functions                  └────────┬─────────┘
│ email, email_status_id ─► reference_values          ▼
│ is_primary, start/end_date                    ┌──────────┐   ┌───────────────┐
│ via_company_id ─► companies (lien INDIRECT)   │   tags   │──►│ tag_families  │
└──────┬─────────────┬──────────────┘           └──────────┘   │ kind BUSINESS │
       │             │                                          │ /HASHTAG      │
       ▼             ▼                                          └───────────────┘
┌────────────┐  ┌──────────────────┐
│  contacts  │  │      events      │  ← interactions HUMAINES (timeline)
│ (personne) │  │ company_id NOT N.│
│ identité   │  │ company_contact_id, operation_id, operation_target_id
│ seulement  │  │ event_type_id ─► reference_values
└────────────┘  └──────────────────┘

┌──────────────┐   ┌────────────────────┐   ┌───────────────────────────┐
│  operations  │──►│ operation_targets  │──►│ operation_target_contacts │
│ (mandats M&A)│   │ (société × opé)    │   │ (interlocuteurs de cible) │
└──────────────┘   └────────────────────┘   └───────────────────────────┘

┌──────────────────┐   ┌──────────────────┐      ┌─────────────────┐
│ email_campaigns  │──►│ mail_deliveries  │      │ email_messages  │ (envois unitaires)
│ (dont 258 legacy)│   │ (DÉLIVRABILITÉ)  │      │ + tracking_links│ (clics D33)
└──────────────────┘   └──────────────────┘      └─────────────────┘

┌────────────────┐   ┌────────────┐   ┌────────────────┐
│ dynamic_lists  │──►│  segments  │──►│ segment_items  │  (listes → figées → export)
└────────────────┘   └────────────┘   └────────────────┘
```

---

## 3. Tables centrales — détail

### 3.1 `companies` — sociétés et personnes physiques (table pivot)

| Colonne | Sens |
|---|---|
| `id` | PK. **L'id métier** de la société dans PROJEA2. |
| `legacy_idinterne` | ancien `twinl.bdd.idinterne` — pont avec le MCP `mcp-projea`. |
| `entity_kind_id` | → `reference_values` type `entity_kind` : `COMPANY` \| `INDIVIDUAL`. |
| `legal_name`, `secondary_name` | raison sociale (+ nom secondaire). |
| `crm_status_id` | **statut commercial** → type `crm_status` (§4.1). NOT NULL. |
| `legal_status_id` | **statut juridique** dans le groupe → type `legal_status`. NULL = indépendante. |
| `parent_company_id` | maison mère (auto-référence, arbre multi-niveaux réel). |
| `siren` | 9 car., France. **Pas** de SIRET (pas de gestion d'établissement). |
| `registration_number` | registre local pour les sociétés étrangères. |
| `revenue_k_eur`, `revenue_year` | CA en **K€**, année pleine (4 chiffres en base). |
| `revenue_type_id` | → type `revenue_type` : `VERIFIED` (data.gouv) \| `CONSOLIDATED` \| `ESTIMATED`. |
| `postal_code`, `city`, `postal_code_id`, `country_id` | `city` en MAJUSCULES ; `postal_code_id` résolu pour la France (région/zone vacances lues là). |
| `website_url` / `website_host` | `website_host` = **clé canonique** de recherche par site (minuscules, sans protocole ni `www.`). |
| `naf_code_id`, `legal_form_code` | → `naf_codes`, `legal_forms` (nomenclature INSEE). |
| `phone` / `phone_e164` | brut / normalisé. |
| `notes` | commentaires libres (les hashtags `§` legacy ont été extraits en vrais tags). |
| `is_deleted` | **suppression logique — filtrer `= 0`**. |
| `updated_by_user_id`, `created_at`, `updated_at` | traçabilité. |

### 3.2 `reference_types` + `reference_values` — le lookup universel ⭐

Remplace `twinl.tb_CodeStatut`. Deux niveaux :

- `reference_types (id, code, label, is_system)` — le **domaine** (`crm_status`, `event_type`…) ;
- `reference_values (id, reference_type_id, code, label, sort_order, color, is_terminal,
  is_active, legacy_id_statut, metadata_json)` — la **valeur**, unique par (type, code).

`legacy_id_statut` conserve l'ancien `tb_CodeStatut.id_statut` : c'est le pont avec `twinl`.

> **Toujours joindre le type** : `code` n'est unique QUE dans son `reference_type`
> (ex. `SIGNED` existe dans `op_status` **et** `op_target_status`). Voir §5.1.

### 3.3 `contacts` — la personne (identité stable UNIQUEMENT)

`id`, `legacy_id_dirigeant`, `gender_id` (→ `gender` : `M`/`MME`), `first_name`, `last_name`,
`birth_year`, `language_id` (→ `language` : `FR`/`EN`/`ZH`/`DE`/`ES`), `linkedin_url`,
`merged_into_contact_id` (fiche absorbée par fusion — **à exclure**), `updated_by_user_id`.

**Il n'y a ni email, ni fonction, ni téléphone ici.** Ils sont sur le rattachement.

### 3.4 `company_contacts` — LE RATTACHEMENT (personne × société)

| Colonne | Sens |
|---|---|
| `company_id`, `contact_id` | le couple. Unique avec `function_id`. |
| `job_title_raw` | fonction telle qu'écrite (texte libre, milliers de variantes). |
| `function_id` | → `functions` : la **fonction type** normalisée (§3.5). |
| `email`, `email_normalized` | l'adresse ; `email_normalized` (minuscules/trim) = clé de recherche. |
| `email_status_id` | → type `email_status` (§4.2). **C'est ici que vit l'opt-in/opt-out.** |
| `phone`, `phone_e164` | |
| `is_primary` | rattachement **principal** de la personne (celui utilisé par défaut). |
| `start_date`, `end_date` | `end_date IS NULL` ⇒ **rattachement actif**. |
| `end_reason_id` | → type `attachment_end_reason` : `DEPARTURE`/`RETIREMENT`/`MANDATE_END`. |
| `via_company_id` | rattachement **INDIRECT** : la personne est mandataire de *cette* structure (holding), et rattachée à la société d'entrée « via » elle. NULL = lien direct. |
| `legacy_id_dirigeant` | pont avec `twinl.dirigeants`. |

### 3.5 `functions` — référentiel des fonctions dirigeants

`id`, `legacy_id_fonction`, `label`, `status`, `canonical_function_id`, `level`,
`email_targetable`, `is_active`.

- `status` : `APPROVED` \| `OFFICER` (mandataire social : Gérant, Président, PDG, DG…) \|
  `ALIAS` (renvoie vers `canonical_function_id`) \| `REJECTED` (fonction connue mais dont on
  n'importe pas les contacts).
- `level` : niveau hiérarchique **0–9**, porté par la fonction **canonique** ; un alias peut
  l'avoir à NULL. ⇒ **toujours** `COALESCE(canon.level, f.level)`.
- **Règle décideur : niveau ≥ 6.**
- `email_targetable` : la fonction est ciblable en emailing (14 fonctions sur 117).

### 3.6 `events` — interactions humaines (la timeline métier)

`id`, `legacy_id_event`, `company_id` (NOT NULL), `company_contact_id`, `operation_id`,
`operation_target_id`, `event_type_id` (→ type `event_type`, §4.3), `occurred_at`,
`legacy_occurred_at_raw`, `summary`, `body`, `direction` (`INBOUND`/`OUTBOUND`/`INTERNAL`),
`channel`, `is_public_comment`, `source` (`MANUAL`/`WORKFLOW`/`IMPORT`/`API`), `is_highlighted`,
`created_by_user_id`.

⚠️ `occurred_at` peut être **NULL** (dates legacy inexploitables : epoch 0 / 1970, brut conservé
dans `legacy_occurred_at_raw`). Pour une série temporelle, filtre `occurred_at IS NOT NULL` et
dis-le dans la réponse.

⚠️ **Les traces de délivrabilité ne sont PAS ici** (elles représentaient 63 % des events legacy) :
elles sont dans `mail_deliveries`. Un `EMAIL_OPENED` **existe** dans `events`, mais uniquement
pour un **clic tracké humain** sur un envoi unitaire (D33) — pas pour les campagnes de masse.

### 3.7 `email_campaigns` + `mail_deliveries` — emailing et délivrabilité

- `email_campaigns` : **table unique** des campagnes. Porte les campagnes natives ET les **258
  campagnes historiques** reprises de `twinl.tb_mailing` (repérées par `legacy_id_mailing`,
  `is_archived = 1`). `operation_id` la rattache éventuellement à une opération.
- `mail_deliveries` : une ligne par (campagne × contact) : `campaign_id`, `company_id`,
  `company_contact_id`, `email_snapshot`, `sent_at`, `status`, `status_at`.
  `status` ∈ `SENT` \| `OPENED` \| `CLICKED` \| `SOFT_BOUNCE` \| `HARD_BOUNCE` \| `UNSUBSCRIBED`
  (**varchar, pas un référentiel** — filtre sur la chaîne).

### 3.8 `email_messages`, `email_templates`, `tracking_*` — envois unitaires et clics (D33)

- `email_messages` : un email **unitaire** envoyé par la plateforme (`from_email`, `to_email`,
  `subject`, `body_html`, `status` `SENT`/`FAILED`, `sent_by_user_id`, `email_type_id`,
  `operation_id`, `opened_at`, `unsubscribed_at`, `bounced_at`, `bounce_kind` `HARD`/`SOFT`).
  Lié à l'event correspondant par `event_id`.
  ⚠️ `body_html` est volumineux : ne le sélectionne que si on te le demande explicitement.
- `email_templates` : modèles (`campaign_id` obligatoire, `email_type_id`, `language_id`,
  `target_category_id` pour les teasers, `visibility` `PRIVATE`/`PUBLIC`, `notify_clicks_slack`).
- `tracking_links` (1 par lien tracké d'un email) + `tracking_hits` (journal brut des clics,
  `kind` `CLICK`/`UNSUB`, `is_bot`). Le **premier clic humain** produit l'event `EMAIL_OPENED`.
  Les **jetons** (`token`, `unsubscribe_token`) ne sont **pas exposés** par le MCP.

### 3.9 `tags`, `tag_families`, `company_tags` — métiers et hashtags

Un seul modèle unifié (dans `twinl` il y avait deux systèmes parallèles non reliés).

| Famille (`tag_families.code`) | `kind` | Sens | Création libre |
|---|---|---|---|
| `sector` | `BUSINESS` | Secteur / métier | non (liste fermée) |
| `deal_ecosystem` | `BUSINESS` | Écosystème deal (investisseurs, conseils, notaires…) | non |
| `esn_it` | `BUSINESS` | **Métier ESN / IT** — la règle métier officielle | non |
| `company_hashtag` | `HASHTAG` | Hashtag entreprise (dont `#Client`) | **oui** |
| `acq_criteria` | `ACQ_CRITERIA` | Critère d'acquisition (module II) | non |

`tags` : `tag_family_id`, `label`, `slug`, `color`, `legacy_id_qualifiant`, `is_active`.
`company_tags` : `company_id`, `tag_id`, `created_by_user_id` (unique sur le couple).

> **Le métier d'une société n'est JAMAIS déduit du NAF ni du nom.** Il est qualifié
> explicitement par un tag de famille `BUSINESS`. Le `#Client` (ex-statut CRM « Client » +
> ex-métier « Client », **fusionnés en un seul tag** le 2026-07-31) est un **hashtag**.

### 3.10 Module II — opérations M&A

- `operations` : le mandat. `name`, `code` (ex. « SOJEO »), `client_company_id`, `nature_id`
  (→ `op_type`), `status_id` (→ `op_status`), `lead_user_id`, `start_date`, `close_date`,
  `is_active`, `slack_channel`, `legacy_id_projet`.
- `operation_targets` : une société **cible** dans une opération. `status_process_id`
  (→ `op_target_status`, **19 valeurs** — le statut d'avancement), `category_id`
  (→ `op_target_category`), `role_id` (→ `op_target_role` : `AUTONOMOUS`/`ACCOMPANIED`/`LEADER`/
  `ADVISOR`), `leader_target_id` (cible qui mène le groupe), `indicative_offer_date`, `nbo_date`,
  `dossier_url`, `legacy_id_tb_cible`.
  ⚠️ **`op_target_status` (avancement) ≠ `op_target_role` (rôle dans le groupe)** — piège de
  nommage hérité du legacy.
- `operation_target_contacts` : les interlocuteurs d'une cible. `company_contact_id` (le
  rattachement !), `role` `PRINCIPAL`/`SECONDARY`, `is_active`, `language_id`, `email_used`,
  `dataroom_access`.
- `operation_members` : qui a accès à l'opération (accès **binaire**, pas permission par
  permission).
- `reporting_stage` (référentiel) : regroupement des 19 statuts process en **9 stades de funnel**
  (`PANEL`, `CONTACTED`, `TEASER_READ`, `NDA_IN_PROGRESS`, `DOSSIER`, `ACTIVE`, `REFUSAL`,
  `SUSPENDED`, `OUT_OF_PLAY`). La correspondance statut → stade vit dans
  `reference_values.metadata_json` du statut.

### 3.11 Listes, segments, exports

- `dynamic_lists` : requête **vivante** — `entity_type` `COMPANY`/`CONTACT`, `builder_json`
  (requêteur graphique), `sql_text` (mode expert, admin), `mode`
  `GRAPHICAL`/`EXPERT`/`EXTERNAL`, `visibility` `PRIVATE`/`SHARED`.
- `segments` + `segment_items` : la liste **figée** à un instant (avec snapshots nom/email).
- `export_templates` (colonnes d'export) et `export_approvals` (demande d'approbation admin d'un
  export volumineux : `status` `PENDING`/`APPROVED`/`REFUSED`/`EXPIRED`/`COMPLETED`).

### 3.12 Utilisateurs, droits, audit

- `users` : identité TwinL partagée (`email`, `display_name`, `is_active`, `office_id`).
  Le **hash de mot de passe n'est pas exposé** par le MCP.
- `groups` (`ADMIN`/`PREMIUM`/`BASIC`/`EXTERNAL`), `permissions` (codes atomiques
  `crm.company.read`, `crm.list.expert_sql`, `operation.nda`…), `group_permissions`,
  `user_group_memberships`.
- `audit_events` : journal **append-only** de toutes les écritures métier (`user_id`, `action`,
  `entity_type`, `entity_id`, `payload_json`, `created_at`).
- `notifications` : cloche in-app (`kind` `EMAIL_OPENED`/`EMAIL_UNSUBSCRIBED`/`EMAIL_BOUNCED`).

### 3.13 Référentiels annexes

`countries` (192, ISO-2/ISO-3, `selection` = pays mis en avant), `postal_codes` (CP, ville, INSEE,
`region`, `metropolitan_area`, `vacation_zone`, lat/long, `is_active`), `naf_codes` (NAF rév. 2 +
`mailing_category`), `legal_forms` (INSEE niveau 3, PK = `code` char(4)), `sender_offices`,
`external_registry_sources` / `external_search_templates` (URLs de recherche externes),
`email_variables`, `nda_templates`, `process_letter_templates`, `email_attachments`,
`email_template_attachments`.

---

## 4. Référentiels — les codes qui portent une règle métier

Liste des `reference_types` et de leurs `code` de valeur. **Filtre toujours sur ces codes**, jamais
sur des `id`.

### 4.1 `crm_status` — statut COMMERCIAL de la société (15 valeurs)

`SUSPECT_CESSION` · `SUSPECT_ACQUISITION` · `SUSPECT_CESSION_ACQUISITION` (**le défaut**) ·
`INDIVIDUAL_BUYER` · **`NON_DECISIONNAIRE`** · `PARTNER` · `LIQUIDATED` · `RJ_SAUVEGARDE` ·
`OFF_TARGET` · `INTERNAL` · `CLIENT` · `FOREIGN_GROUP_SUBSIDIARY` · `SUBSIDIARY` · `RESERVE` ·
`DUPLICATE`.

- **`NON_DECISIONNAIRE` = exclu des sélections** (listes, segments, emailing) ; la société n'existe
  que pour des actes juridiques (NDA). C'est le statut posé sur les filiales liées et sur les
  holdings créées par la remontée data.gouv.
- « Décisionnaire » = tout autre statut commercial.
- Les codes `CLIENT`, `SUBSIDIARY`, `FOREIGN_GROUP_SUBSIDIARY`, `RESERVE`, `DUPLICATE` sont des
  **résidus de référentiel** : le modèle cible les a déplacés sur `legal_status` (filiales) ou sur
  un hashtag (`#Client`). Vérifie leur usage réel avant d'en tirer une conclusion.

### 4.2 `email_status` — statut de l'adresse, porté par le RATTACHEMENT (6 valeurs)

| Code | Sens | Ciblable ? |
|---|---|:-:|
| `VALID` | opt-in valide | ✅ |
| `NOT_TESTED` | jamais testée | ⚠️ selon le besoin |
| `OPT_OUT` | désinscrit | ❌ |
| `SOFT_BOUNCE` | échec temporaire | ❌ |
| `HARD_BOUNCE` | adresse morte / NPAI | ❌ |
| `BLACKLISTED` | blacklistée | ❌ |

Règles : **une seule adresse opt-in par personne**, tous rattachements confondus ; modifier une
adresse remet son statut à `VALID` (l'opt-out porte sur l'adresse, pas sur la personne).

### 4.3 `event_type` — types d'interaction (17 valeurs)

`NEWS` · `NOTE` · `CALL` · `PHONE_MEETING` · `MEETING` · `LETTER_RECEIVED` · `EMAIL_SENT` ·
`TEASER_SENT` · `DOC_ACK` · `NDA_SENT` · `NDA_RECEIVED` · `FOLLOW_UP` · `INTRODUCTION` ·
`DOSSIER_SENT` · `OFF_TARGET_MA` · `EMAIL_OPENED` · `CONTACT_MERGED`.

### 4.4 Autres référentiels (code des valeurs)

| `reference_types.code` | Valeurs |
|---|---|
| `entity_kind` | `COMPANY`, `INDIVIDUAL` |
| `legal_status` | `HOLDING`, `GROUP_HEAD`, `SUBSIDIARY`, `FOREIGN_GROUP_SUBSIDIARY` |
| `revenue_type` | `VERIFIED`, `CONSOLIDATED`, `ESTIMATED` |
| `gender` | `M`, `MME` |
| `language` | `FR`, `EN`, `ZH`, `DE`, `ES` |
| `attachment_end_reason` | `DEPARTURE`, `RETIREMENT`, `MANDATE_END` |
| `email_type` | `PROSPECTION`, `COMMUNIQUE`, `TEASER`, `TEASER_RELANCE`, `TEASER_READ_RELANCE`, `NDA`, `NDA_RELANCE`, `DOSSIER`, `DOSSIER_RELANCE`, `INFOS_COMPLEMENTAIRES` |
| `op_type` | `CESSION`, `ACQUISITION`, `FUNDRAISING`, `PV_FINANCING`, `ALLIANCE_PARTNERSHIP`, `PV_DEVELOPMENT`, `ADMIN`, `OPPORTUNITIES` |
| `op_status` | `SIGNED`, `IN_PRODUCTION`, `LOI`, `SUSPENDED`, `MEETING_TO_SCHEDULE`, `TO_CONTACT`, `CANCELLED`, `FAILURE`, `SUCCESS`, `INTERNAL` |
| `op_target_status` | `SIGNED`, `LOI`, `SHORT_LIST`, `DISCUSSION`, `IN_PROGRESS`, `NDA_SIGNED`, `NDA_SENT`, `PRE_NDA_CONTACT`, `TEASER_READ`, `CONTACTED`, `TARGET`, `STAND_BY`, `TARGET_TO_QUALIFY`, `REFUSAL_POST_NDA`, `REFUSAL_ON_TEASER`, `SUSPENDED`, `FINISHED`, `OFF_TARGET`, `EXTERNAL_TARGET` |
| `op_target_category` | `NORMAL`, `PRIORITY`, `ADVISOR`, `RECOMMENDED`, `MBI`, `CLIENT_TO_VALIDATE`, `FUND` |
| `op_target_role` | `AUTONOMOUS`, `ACCOMPANIED`, `LEADER`, `ADVISOR` |
| `teaser_category` | `BUYER`, `ADVISOR`, `FUND` |
| `reporting_stage` | `PANEL`, `CONTACTED`, `TEASER_READ`, `NDA_IN_PROGRESS`, `DOSSIER`, `ACTIVE`, `REFUSAL`, `SUSPENDED`, `OUT_OF_PLAY` |
| `mailing_return` | `EMAIL_OPENED`, `EMAIL_CLICKED`, `INBOUND_CALL`, `UNSUBSCRIBED`, `BOUNCED`, `NPAI`, `LEAD` |
| `qualification_status` | `RECALL`, `TO_PROCESS`, `SUSPENDED`, `REFUSAL`, `CANCELLED`, `MEETING`, `OFF_TARGET` |
| `opportunity_source` | `FUSACQ`, `MA_TRANSNATIONAL`, `TRANSPME`, `DEALSUITE`, `CCI_IDF`, `AURIGIN`, `INTERNAL` |
| `external_provider` | `CFNEWS`, `LATKA`, `NBELLION`, `COGNISM`, `IMPORT_ITALY_MECH`, `IMPORT_AGRI_BIO` |
| `event_channel` | `EMAIL`, `PHONE`, `MEETING`, `SIGNATURE`, `DATAROOM`, `INTERNAL_NOTE` |
| `direction`, `event_source`, `campaign_channel`, `social_network`, `data_source` | cf. §3.6 / §3.7 |

### 4.5 Statuts en varchar (PAS des référentiels)

Certaines colonnes portent un code **directement en varchar** — filtre sur la chaîne, sans JOIN :
`mail_deliveries.status`, `email_messages.status` (+ `bounce_kind`), `events.direction`,
`events.source`, `events.channel`, `functions.status`, `tag_families.kind`,
`dynamic_lists.entity_type`/`mode`/`visibility`, `email_templates.visibility`,
`operation_target_contacts.role`, `export_approvals.status`/`source_type`/`format`,
`tracking_hits.kind`, `notifications.kind`, `groups.code`, `permissions.code`.

---

## 5. Patterns de requête courants

> Les exemples sont écrits sans préfixe de base : la connexion pointe déjà la base miroir
> `projea2_readonly`. Le préfixe `projea2_readonly.` est accepté ; `projea2.` est **refusé**
> (le user read-only n'a aucun droit sur la base source).

### 5.1 Résoudre un code de référentiel (pattern FONDAMENTAL)

```sql
SELECT c.id, c.legal_name,
       crm.code  AS crm_status_code,  crm.label AS crm_status,
       leg.code  AS legal_status_code, leg.label AS legal_status
FROM companies c
JOIN      reference_values crm  ON crm.id = c.crm_status_id
JOIN      reference_types  crmt ON crmt.id = crm.reference_type_id
                               AND crmt.code = 'crm_status'
LEFT JOIN reference_values leg  ON leg.id = c.legal_status_id
LEFT JOIN reference_types  legt ON legt.id = leg.reference_type_id
                               AND legt.code = 'legal_status'
WHERE c.is_deleted = 0
  AND c.id = ?;
```

Et pour **filtrer** sur un code (jamais sur un id en dur) :

```sql
WHERE c.crm_status_id IN (
  SELECT rv.id FROM reference_values rv
  JOIN reference_types rt ON rt.id = rv.reference_type_id
  WHERE rt.code = 'crm_status'
    AND rv.code IN ('SUSPECT_CESSION','SUSPECT_ACQUISITION','SUSPECT_CESSION_ACQUISITION')
)
```

### 5.2 Explorer un référentiel (à faire AVANT de deviner un code)

```sql
SELECT rt.code AS type_code, rv.code, rv.label, rv.sort_order,
       rv.is_active, rv.legacy_id_statut
FROM reference_values rv
JOIN reference_types rt ON rt.id = rv.reference_type_id
WHERE rt.code = 'op_target_status'      -- remplacer par le type cherché
ORDER BY rv.sort_order, rv.code;
```

### 5.3 Contacts actifs d'une société, avec fonction et statut d'email

```sql
SELECT ct.id AS contact_id, ct.first_name, ct.last_name,
       cc.job_title_raw, f.label AS fonction_type,
       COALESCE(canon.level, f.level) AS niveau,
       cc.email, es.code AS email_status, cc.is_primary,
       via.legal_name AS via_societe
FROM company_contacts cc
JOIN      contacts  ct   ON ct.id = cc.contact_id
                        AND ct.merged_into_contact_id IS NULL
LEFT JOIN functions f    ON f.id = cc.function_id
LEFT JOIN functions canon ON canon.id = f.canonical_function_id
LEFT JOIN reference_values es ON es.id = cc.email_status_id
LEFT JOIN companies via  ON via.id = cc.via_company_id
WHERE cc.company_id = ?
  AND cc.end_date IS NULL              -- rattachement ACTIF
ORDER BY cc.is_primary DESC, niveau DESC;
```

### 5.4 Décideurs joignables (le pattern de ciblage)

```sql
SELECT c.id AS company_id, c.legal_name, ct.first_name, ct.last_name,
       cc.email, f.label AS fonction, COALESCE(canon.level, f.level) AS niveau
FROM companies c
JOIN company_contacts cc  ON cc.company_id = c.id AND cc.end_date IS NULL
JOIN contacts ct          ON ct.id = cc.contact_id
                         AND ct.merged_into_contact_id IS NULL
JOIN functions f          ON f.id = cc.function_id
LEFT JOIN functions canon ON canon.id = f.canonical_function_id
JOIN reference_values es  ON es.id = cc.email_status_id
JOIN reference_types  est ON est.id = es.reference_type_id AND est.code = 'email_status'
JOIN reference_values crm ON crm.id = c.crm_status_id
JOIN reference_types  crmt ON crmt.id = crm.reference_type_id AND crmt.code = 'crm_status'
WHERE c.is_deleted = 0
  AND crm.code <> 'NON_DECISIONNAIRE'          -- hors sélections
  AND es.code = 'VALID'                         -- email opt-in
  AND COALESCE(canon.level, f.level) >= 6       -- décideur
  AND cc.email IS NOT NULL AND cc.email <> ''
ORDER BY c.legal_name;
```

### 5.5 Sociétés d'un métier (tag BUSINESS) — ex. ESN / IT

```sql
SELECT c.id, c.legal_name, c.revenue_k_eur, c.revenue_year, c.city, t.label AS metier
FROM companies c
JOIN company_tags ctg ON ctg.company_id = c.id
JOIN tags t           ON t.id = ctg.tag_id AND t.is_active = 1
JOIN tag_families tf  ON tf.id = t.tag_family_id
WHERE tf.code = 'esn_it'          -- 'sector' | 'deal_ecosystem' pour les autres métiers
  AND c.is_deleted = 0
ORDER BY c.revenue_k_eur DESC;
```

Tous les tags d'une société (métiers **et** hashtags) :

```sql
SELECT tf.kind, tf.label AS famille, t.label AS tag
FROM company_tags ctg
JOIN tags t          ON t.id = ctg.tag_id
JOIN tag_families tf ON tf.id = t.tag_family_id
WHERE ctg.company_id = ?
ORDER BY tf.kind, t.label;
```

### 5.6 Timeline d'une société (events lisibles)

```sql
SELECT e.occurred_at, et.code AS type_code, et.label AS type_event,
       e.summary, e.direction, e.channel,
       CONCAT_WS(' ', ct.first_name, ct.last_name) AS contact,
       o.code AS operation, u.display_name AS auteur
FROM events e
JOIN      reference_values et ON et.id = e.event_type_id
JOIN      reference_types  ett ON ett.id = et.reference_type_id AND ett.code = 'event_type'
LEFT JOIN company_contacts cc ON cc.id = e.company_contact_id
LEFT JOIN contacts ct         ON ct.id = cc.contact_id
LEFT JOIN operations o        ON o.id = e.operation_id
LEFT JOIN users u             ON u.id = e.created_by_user_id
WHERE e.company_id = ?
ORDER BY e.occurred_at DESC, e.id DESC;
```

### 5.7 Groupe : maison mère et filiales

```sql
-- filiales directes
SELECT id, legal_name, city FROM companies
WHERE parent_company_id = ? AND is_deleted = 0 ORDER BY legal_name;

-- têtes de groupe (statut juridique, plus un calcul)
SELECT c.id, c.legal_name
FROM companies c
JOIN reference_values lg ON lg.id = c.legal_status_id
JOIN reference_types  lt ON lt.id = lg.reference_type_id AND lt.code = 'legal_status'
WHERE lg.code = 'GROUP_HEAD' AND c.is_deleted = 0;
```

### 5.8 Historique de délivrabilité d'un contact / d'une campagne

```sql
-- par contact (via ses rattachements)
SELECT ec.name AS campagne, md.status, md.sent_at, md.status_at, md.email_snapshot
FROM mail_deliveries md
JOIN email_campaigns ec  ON ec.id = md.campaign_id
JOIN company_contacts cc ON cc.id = md.company_contact_id
WHERE cc.contact_id = ?
ORDER BY md.sent_at DESC;

-- performance d'une campagne
SELECT md.status, COUNT(*) AS n
FROM mail_deliveries md
WHERE md.campaign_id = ?
GROUP BY md.status ORDER BY n DESC;
```

### 5.9 Pipeline d'une opération M&A

```sql
SELECT o.code, o.name, c.legal_name AS cible,
       sp.code AS statut_process, sp.label AS statut,
       cat.code AS categorie, rol.code AS role,
       ot.indicative_offer_date, ot.nbo_date
FROM operations o
JOIN      operation_targets ot ON ot.operation_id = o.id
JOIN      companies c          ON c.id = ot.company_id
JOIN      reference_values sp  ON sp.id = ot.status_process_id
JOIN      reference_types  spt ON spt.id = sp.reference_type_id
                              AND spt.code = 'op_target_status'
LEFT JOIN reference_values cat ON cat.id = ot.category_id
LEFT JOIN reference_values rol ON rol.id = ot.role_id
WHERE o.code = ?
ORDER BY sp.sort_order, c.legal_name;
```

### 5.10 Correspondance avec la base legacy `twinl`

```sql
-- retrouver dans PROJEA2 une société connue par son idinterne twinl
SELECT id, legal_name FROM companies WHERE legacy_idinterne = ?;

-- et l'inverse (pour interroger ensuite le MCP mcp-projea)
SELECT legacy_idinterne FROM companies WHERE id = ?;
```

Autres ponts : `contacts.legacy_id_dirigeant`, `company_contacts.legacy_id_dirigeant`,
`events.legacy_id_event`, `tags.legacy_id_qualifiant`, `functions.legacy_id_fonction`,
`operations.legacy_id_projet`, `operation_targets.legacy_id_tb_cible`,
`operation_target_contacts.legacy_id_cible_dirigeant`, `email_campaigns.legacy_id_mailing`,
`reference_values.legacy_id_statut`.

---

## 6. Ciblage / sélection — règles métier

Cas d'usage récurrent : constituer une liste de sociétés ou de contacts éligibles.

### 6.1 Critères d'éligibilité standard

- **Société vivante** : `companies.is_deleted = 0`.
- **Dans les sélections** : `crm_status` ≠ `NON_DECISIONNAIRE`.
- **Rattachement actif** : `company_contacts.end_date IS NULL`.
- **Personne vivante** : `contacts.merged_into_contact_id IS NULL`.
- **Décideur** : `COALESCE(canon.level, f.level) >= 6`.
- **Email exploitable** : `email_status` = `VALID` et `cc.email` non vide.
- **Métier** : via `company_tags` → `tags` → `tag_families` (`kind='BUSINESS'`), **jamais** via le
  NAF.
- **Géographie** : `companies.country_id` → `countries.iso2` (`'FR'`, `'BE'`…) ; région / zone de
  vacances via `postal_codes` (`companies.postal_code_id`).

### 6.2 Dédoublonnage

- **Une ligne par personne** : une personne peut avoir plusieurs rattachements actifs. Pour un
  envoi, prendre le rattachement **`is_primary = 1`** (à défaut, un seul par `contact_id`).
- **Une ligne par adresse** : `email_normalized` peut se répéter entre rattachements → dédoublonner
  dessus pour un envoi réel.
- **Une ligne par société** si la cible est la société (`GROUP BY c.id`).

### 6.3 Exclure les « déjà contactés » d'une campagne

```sql
AND NOT EXISTS (
  SELECT 1 FROM mail_deliveries md
  WHERE md.company_contact_id = cc.id
    AND md.campaign_id = ?          -- la campagne dont on veut exclure les destinataires
)
```

Pour exclure sur **plusieurs** campagnes, remplacer par `md.campaign_id IN (…)`. Pour exclure les
adresses mortes toutes campagnes confondues, se fier au `email_status` du rattachement (les
bounces l'ont déjà mis à jour).

---

## 7. Pièges (gotchas)

1. **Ne jamais filtrer sur un id de référentiel en dur.** Les `reference_values.id` ne sont pas
   des codes métier et changent d'un environnement à l'autre.
2. **`code` n'est unique que dans son type.** `SIGNED` existe dans `op_status` ET
   `op_target_status` ; `SUSPENDED`, `ADVISOR`, `FUND`, `TEASER_READ`, `CONTACTED` aussi.
   Toujours joindre `reference_types`.
3. **L'email est sur le rattachement, pas sur la personne.** Chercher `contacts.email` est l'erreur
   n°1 sur cette base.
4. **`end_date IS NULL` = actif.** Il n'y a plus de hack « _sorti » / code 91 : un contact parti a
   un rattachement clos, et son email est conservé (traçabilité de l'opt-in).
5. **`events.occurred_at` peut être NULL** (dates legacy irrécupérables) — l'exclure fausse un
   comptage, l'inclure fausse une série temporelle. Choisir explicitement et le dire.
6. **Ouvertures/bounces ≠ events.** Ils sont dans `mail_deliveries` (campagnes) et sur
   `email_messages` (`opened_at`, `bounced_at`, envois unitaires).
7. **`crm_status` vs `legal_status`** : « filiale » est un statut **juridique**, pas commercial.
   Une filiale liée est en plus `NON_DECISIONNAIRE`, donc hors sélections.
8. **`op_target_status` (avancement) vs `op_target_role` (rôle dans le groupe)** : deux
   référentiels aux libellés voisins. Piège hérité du legacy.
9. **`legacy_*` ne sont pas des clés courantes** : elles servent uniquement à recouper avec
   `twinl`. Une ligne créée dans PROJEA2 les a à NULL.
10. **Les nombres bougent** : la base est vivante et la migration depuis `twinl` est rejouable
    (les `id` de lignes migrées changent d'un run à l'autre). Ne mémorise pas un id migré ;
    recompte plutôt que de citer un chiffre de ce document.
11. **Colonnes volumineuses** : `email_messages.body_html`, `email_templates.body_html`,
    `events.body`, `companies.notes`, `*_json`. Un `SELECT *` dessus fait exploser le plafond
    d'octets — sélectionne les colonnes utiles.
12. **Colonnes non exposées** (retirées des vues du MCP, ne les cherche pas) :
    `users.password_hash`, `email_messages.unsubscribe_token`, `tracking_links.token`,
    `export_approvals.token`, `tracking_hits.ip`. Tables non exposées : `password_tokens`,
    `alembic_version`, `migration_id_map`, `migration_runs`, `migration_watermarks`.

---

## 8. Bonnes pratiques d'analyse

- **Agréger plutôt que lister.** La réponse est plafonnée en lignes et en octets : préférer
  `COUNT`/`GROUP BY` puis descendre dans le détail à la demande.
- **Compter avant d'extraire** : un `SELECT COUNT(*)` avec les mêmes `WHERE` dit tout de suite si
  l'extraction tient dans le plafond.
- **`export_format`** : renseigner ce paramètre de `mysql_query` **uniquement** quand le résultat
  est destiné à un fichier téléchargeable (Excel/CSV) — il déclenche l'approbation Slack.
- **Dire ses hypothèses** : si tu as choisi d'exclure les `occurred_at IS NULL`, les rattachements
  clos ou les `NON_DECISIONNAIRE`, écris-le dans la réponse. Un comptage sans son périmètre n'est
  pas exploitable.
- **Ne jamais improviser un référentiel** : lister ses valeurs (§5.2) coûte une requête et évite
  une réponse fausse.

---

## 9. Catalogue des tables exposées (45 vues)

**CRM cœur** : `companies` · `contacts` · `company_contacts` · `events` · `company_tags` ·
`tags` · `tag_families` · `functions`

**Référentiels** : `reference_types` · `reference_values` · `countries` · `postal_codes` ·
`naf_codes` · `legal_forms` · `external_registry_sources` · `external_search_templates` ·
`sender_offices` · `email_variables`

**Emailing / tracking** : `email_campaigns` · `mail_deliveries` · `email_messages` ·
`email_templates` · `email_attachments` · `email_template_attachments` · `tracking_links` ·
`tracking_hits` · `tracking_link_exclusions`

**Listes & exports** : `dynamic_lists` · `segments` · `segment_items` · `export_templates` ·
`export_approvals`

**Module II — opérations M&A** : `operations` · `operation_targets` ·
`operation_target_contacts` · `operation_members` · `nda_templates` ·
`process_letter_templates`

**Utilisateurs / droits / traces** : `users` · `groups` · `permissions` · `group_permissions` ·
`user_group_memberships` · `audit_events` · `notifications`

> Une table absente de cette liste (ou une colonne absente d'une vue) n'est **pas** interrogeable :
> le MCP l'exclut volontairement (§7.12). Le catalogue réel s'obtient par
> `SHOW TABLES` / `INFORMATION_SCHEMA` — c'est lui qui fait foi.

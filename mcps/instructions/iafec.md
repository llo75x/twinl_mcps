# iaFEC — instructions du MCP (cartographie + règles métier)

> Ces instructions sont délivrées **automatiquement** par le serveur MCP `mcp-iafec` (champ MCP
> standard `instructions`) à tout client : claude.ai web, Claude Desktop, Cowork, Claude Code.
> Tu interroges la base **iaFEC** (analyse de liasses FEC) via l'outil `mysql_query(sql)`, en
> **lecture seule** (tout `INSERT/UPDATE/DELETE/DDL` est refusé : GRANT SELECT only + vues
> `SQL SECURITY DEFINER` + garde SELECT-only côté serveur). Tu peux écrire n'importe quel `SELECT`.
> Logique métier détaillée (PCG, R&R, consolidation, plan analytique, bugs résolus) : `skill_FEC.md`
> dans le repo `iafec`.

<!-- DIGEST -->
Base **iaFEC** (analyse de liasses FEC), lecture seule.

Conventions critiques :
- Signe : `net = debit_amount − credit_amount`. Un compte de passif / produit est **négatif** en base (capital social 100k → `-100000`) : c'est NORMAL, ne « corrige » pas le signe.
- Comptes tiers 40x (fournisseurs) / 41x (clients) : clé d'agrégation **composite** `(account_number, aux_account_number)`, jamais `account_number` seul.
- Groupe TwinL (`id=5`) masqué par les vues.
- Exercices exploitables : `exercises.status IN ('IMPORTED','VALIDATED')`.

Privilégie les tables **pré-calculées** : `statement_lines` (bilan/CR, `net_amount` déjà signé), `financial_metrics` (KPI via `metric_code` : CA, EBE, BFR, CAF…), `account_balances` (soldes par compte/tiers) — plutôt que d'agréger `accounting_entries` (611k lignes ; toujours filtrer par exercice/import).

Pour le schéma complet (21 vues), les codes KPI, les préfixes PCG et les patterns SQL → appelle l'outil `get_data_model_reference`.
<!-- /DIGEST -->

---

## 0. À lire EN PREMIER — les 3 conventions à ne JAMAIS oublier

### ⚠️ Convention de signe

```
net = debit_amount − credit_amount
```

- **Positif** : comptes d'actif (classe 2, 3, 4 débiteurs, 5 débiteurs), comptes de charges (6)
- **Négatif** : comptes de passif / capitaux propres (classe 1, 4 créditeurs, 5 créditeurs), comptes de produits (7)

> Le frontend iaFEC inverse le signe à l'affichage pour montrer des valeurs positives partout.
> En base, **un capital social de 100k apparaît comme `-100000`**.
> **Ne JAMAIS « corriger » un signe sans comprendre.** Cf. skill_FEC.md §1.

### ⚠️ Comptes tiers 40x / 41x — clé composite

Les comptes **fournisseurs (40x)** et **clients (41x)** ont plusieurs tiers avec le même `account_number`.
La clé d'agrégation correcte est `(account_number, aux_account_number)`, pas `account_number` seul.

Sinon : un fournisseur débiteur et un fournisseur créditeur se compensent → solde faux.
La table `account_balances` respecte déjà cette clé composite (ligne par tiers).

### ⚠️ Groupe TwinL invisible

Le groupe `id = 5` (TwinL) est **filtré au niveau des vues SQL SECURITY DEFINER**.
Si on demande des stats globales, c'est sur les 7 groupes restants.

---

## 1. Périmètre actuel (mai 2026)

| Entité | Compte |
|---|--:|
| Groupes de sociétés | 7 |
| Sociétés | 10 |
| Exercices comptables | 29 |
| Imports FEC | 25 |
| Écritures comptables | 611 206 |
| Soldes agrégés (`account_balances`) | 9 798 |
| Lignes bilan/CR (`statement_lines`) | 1 277 |
| KPI calculés (`financial_metrics`) | 460 |
| Période couverte | 2022-02-27 → 2027-12-30 |

Liste des **groupes** : SMES, Newtech, Régle de 3, Synten, Camargues Production, 01 System, Christophe Roussel.
(Le groupe TwinL — `id=5` — est volontairement masqué.)

---

## 2. Architecture entité-relation

```
                           ┌─────────────┐
                           │   groups    │
                           │ id, name    │
                           └──────┬──────┘
                                  │ 1..N
                           ┌──────▼──────┐
                           │  companies  │
                           │ id, name,   │
                           │ siren, group_id, fiscal_year_end_month
                           └──────┬──────┘
                                  │ 1..N
                           ┌──────▼──────┐
                           │  exercises  │
                           │ id, end_date,│  ← date de clôture (pas l'année civile !)
                           │ status      │
                           └──────┬──────┘
                                  │ 1..N
                           ┌──────▼──────┐
                           │ fec_imports │
                           │ id, filename,│
                           │ status      │
                           └──────┬──────┘
                                  │ 1..N
                           ┌──────▼──────────┐
                           │ accounting_entries │ ← lignes FEC brutes
                           │ account_number,    │   (611k+ lignes au total)
                           │ aux_account_number,│
                           │ debit_amount,      │
                           │ credit_amount,     │
                           │ entry_date         │
                           └────────────────────┘

Et indépendamment, attaché à exercises :
  account_balances    ← soldes agrégés par (compte, tiers) — pré-calculé
  statement_lines     ← lignes du bilan et du CR — pré-calculé
  financial_metrics   ← KPI (CA, EBE, BFR, CAF…) — pré-calculé
  bilan_overrides     ← reclassifications manuelles (v2.6, liasse fiscale)

Et les modules avancés :
  restatement_versions/operations/movements    ← R&R par exercice
  elimination_versions/operations/movements    ← Éliminations intra-groupe (par group)
  analytic_codes/mappings + analytic_entry_*   ← Plan analytique (par group/company)
```

---

## 3. Tables / vues exposées (21)

Toutes accessibles via `SELECT * FROM <nom>` — pas besoin de préfixer par `iafec_readonly.`,
le MCP est connecté directement à cette DB.

### 3.1 Référentiel

| Vue | Colonnes clés | À quoi ça sert |
|---|---|---|
| `groups` | `id, name, analytic_enabled, is_active` | Groupes de sociétés (TwinL invisible) |
| `companies` | `id, group_id, name, siren, fiscal_year_end_month` | Sociétés. `fiscal_year_end_month=12` par défaut (clôture déc.) ; certains à `02` (févr.) ou `09` (sept.) |
| `exercises` | `id, company_id, end_date, status` | Un exercice = une `end_date`. `status ∈ {PENDING, IMPORTED, VALIDATED, ERROR}` |

### 3.2 FEC bruts (gros volume)

| Vue | Colonnes clés | À quoi ça sert |
|---|---|---|
| `fec_imports` | `id, exercise_id, filename, total_lines_imported, status, total_balance_error` | Métadonnées d'un import FEC. `status=VALID` = utilisable. `total_balance_error` doit être ≈ 0 |
| `accounting_entries` | `id, fec_import_id, account_number, aux_account_number, debit_amount, credit_amount, entry_date, reference, description` | Lignes brutes du FEC. 611k+ lignes — **toujours filtrer par `fec_import_id` ou `entry_date` quand possible** |
| `fec_import_errors` | `id, fec_import_id, error_type, error_message, severity` | Erreurs / warnings rencontrés à l'import |

### 3.3 Pré-calculé (à privilégier pour les analyses)

| Vue | Colonnes clés | À quoi ça sert |
|---|---|---|
| `account_balances` | `id, exercise_id, account_number, aux_account_number, account_label, debit_total, credit_total` | Solde par (exercice, compte PCG, tiers). **Source à utiliser pour la balance**, plus rapide que d'agréger `accounting_entries` |
| `statement_lines` | `id, exercise_id, statement_type, position, line_code, line_label, gross_amount, depreciation_amount, net_amount` | Lignes du **bilan** (`statement_type='BALANCE_SHEET'`) et du **CR** (`statement_type='INCOME_STATEMENT'`). `position ∈ {ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE, SUBTOTAL}` |
| `financial_metrics` | `id, exercise_id, metric_code, metric_label, metric_value, calculation_formula, inputs_json` | KPI calculés. `metric_code` = code court (CA, EBE, BFR, CAF…) |
| `bilan_overrides` | `id, exercise_id, account_prefix, target_line, mode, comment, auto_detected, stale, validated_at` | v2.6+ : reclassifications manuelles d'un préfixe de compte sur le bilan (alignement CERFA 2050) |

### 3.4 Reclassements & Retraitements (R&R) — modifient bilan/CR retraité

| Vue | Colonnes clés | Sens |
|---|---|---|
| `restatement_versions` | `id, exercise_id, label, is_active, created_at` | Une version R&R par exercice (une seule active à la fois). |
| `restatement_operations` | `id, version_id, operation_type ('RC'/'RT'), label, category` | **Rc** = Reclassement (partie double, Σdb=Σcr), peut toucher bilan ou CR. **Rt** = Retraitement (CR uniquement, classes 6/7, peut être déséquilibré). |
| `restatement_movements` | `id, operation_id, account_number, account_label, debit_amount, credit_amount, comment, position` | Mouvements Db/Cr d'une opération R&R. Un mouvement n'a JAMAIS Db ET Cr simultanément. |

### 3.5 Éliminations intra-groupe (v2.5 L3)

| Vue | Colonnes clés | Sens |
|---|---|---|
| `elimination_versions` | `id, group_id, period_label, label, is_active` | Une version par (groupe, millésime). Neutralise les transactions intra-groupe en consolidation. |
| `elimination_operations` | `id, version_id, label, category, comment` | Une opération inter-sociétés. Σ Db = Σ Cr stricte. |
| `elimination_movements` | `id, operation_id, company_id, account_number, debit_amount, credit_amount` | Mouvement sur un compte d'une société du groupe. `company_id` obligatoire (différence avec R&R). |

### 3.6 Plan analytique (v2.5 L4-L6)

Visible uniquement si `groups.analytic_enabled = TRUE`.

| Vue | Colonnes clés | Sens |
|---|---|---|
| `analytic_codes` | `id, group_id, code, label, section, position ('REVENU'/'EXPENSE'/'HORS_PERIMETRE'), display_order` | Référentiel analytique partagé par groupe. `section` = MetaCatégorie (= `line_code`). |
| `analytic_mappings` | `id, company_id, account_number, analytic_code_id` | Mapping `compte PCG (6x/7x uniquement) → code analytique` par société. |
| `analytic_entry_versions` | `id, company_id, label, is_active` | Versions d'écritures analytiques (EA). Une seule active par société. N'affecte QUE la vue analytique (CRA), pas bilan/CR/KPI. |
| `analytic_entry_operations` | `id, version_id, label, category` | Opération EA. Σ Db = Σ Cr stricte. |
| `analytic_entry_movements` | `id, operation_id, account_number, debit_amount, credit_amount, comment` | Mouvement EA sur un compte existant de la société. |

---

## 4. Valeurs énumérées importantes

### 4.1 `exercises.status`

```
PENDING | IMPORTED | VALIDATED | ERROR
```

Pour les analyses « propres », filtrer sur `status = 'VALIDATED'` (ou `IN ('IMPORTED','VALIDATED')`).

### 4.2 `statement_lines.statement_type` × `position`

| Type | Positions possibles | Sens |
|---|---|---|
| `BALANCE_SHEET` | `ASSET` (291), `LIABILITY` (203), `EQUITY` (116) | Lignes du bilan |
| `INCOME_STATEMENT` | `REVENUE` (174), `EXPENSE` (290), `SUBTOTAL` (203) | Lignes du compte de résultat. `SUBTOTAL` = totaux intermédiaires (Marge, VA, EBE, RBE, RCAI, RN, etc.) |

### 4.3 `financial_metrics.metric_code` (23 codes principaux)

| Code | Label | Sens comptable |
|---|---|---|
| `CA` / `CA_HT` | Chiffre d'Affaires | Production vendue (70-) — HT |
| `MARGE_BRUTE` | Marge brute | CA + production stockée − Achats consommés (60 uniquement, **pas 61**) |
| `VA` | Valeur Ajoutée | Marge brute − Services extérieurs (61+62) |
| `EBE` | Excédent Brut d'Exploitation | VA + subventions − impôts/taxes − charges de personnel |
| `RESULTAT_EXPL` / `RESULTAT_EXPLOIT` | Résultat d'exploitation | EBE − dotations + autres produits − autres charges |
| `RESULTAT_COURANT` | Résultat Courant Avant Impôts | Résultat expl + résultat financier |
| `RESULTAT_NET` | Résultat Net | Résultat courant + exceptionnel − IS − participation |
| `CAF` | Capacité d'Autofinancement | Résultat net + dotations − reprises + VNC cessions − produits cessions |
| `BFR` | Besoin en Fonds de Roulement | Stocks + créances exploit − dettes exploit |
| `TRESORERIE_NETTE` | Trésorerie Nette | Disponibilités − concours bancaires court terme |
| `TAUX_EBE` | Taux d'EBE | EBE / CA × 100 |
| `TAUX_MARGE_NETTE` | Taux marge nette | RN / CA × 100 |
| `RATIO_ENDETTEMENT` | Ratio d'endettement | Dettes financières / Capitaux propres × 100 |
| `AUTONOMIE_FINANCIERE` | Autonomie financière | Capitaux propres / (capitaux propres + dettes) × 100 |
| `ROTATION_STOCKS` | Rotation stocks | (Stock moyen / CA) × jours_exercice |
| `DELAI_PAIEMENT_CLIENT` | Délai règlement clients (j) | (Créances clients TTC / CA TTC) × jours_exercice |
| `DELAI_REGLEMENT_FOURNISSEUR` | Délai règlement fournisseurs (j) | (Dettes fournisseurs TTC / Achats TTC) × jours_exercice |

> Note : 2 codes ont chacun 2 labels coexistants (`AUTONOMIE_FINANCIERE`, `DELAI_*`, `RATIO_ENDETTEMENT`, `RESULTAT_EXPL`/`RESULTAT_EXPLOIT`) suite à une transition naming en v2.x. Utiliser `metric_code` pour grouper sûrement.

### 4.4 PCG — préfixes de compte clés

| Préfixe | Sens |
|---|---|
| `1` | Capitaux (capital social, réserves, résultat, emprunts) — passif |
| `2` | Immobilisations (incorp., corp., financières) — actif |
| `3` | Stocks et en-cours — actif |
| `40x` | Fournisseurs (**clé composite avec `aux_account_number`**) — passif si Cr, actif si Db (avoirs) |
| `41x` | Clients (**clé composite**) — actif si Db, passif si Cr (419 avances) |
| `42` | Personnel (rémunérations dues, oppositions) — passif |
| `43` | Sécurité sociale et autres organismes sociaux — passif |
| `44` | État (TVA, IS, taxes) — passif ou actif selon |
| `45` | Comptes courants associés / groupe — actif ou passif |
| `5` | Trésorerie (banques, caisse, VMP) — actif (sauf découverts → passif) |
| `60` | Achats consommés (marchandises, matières) — charges |
| `61, 62` | Services extérieurs — charges |
| `63, 64` | Impôts, taxes, charges de personnel — charges |
| `65, 66, 67` | Autres charges, financières, exceptionnelles — charges |
| `68` | Dotations aux amortissements et provisions — charges |
| `69` | Participation, IS — charges |
| `70` | Production vendue (CA HT) — produits |
| `71, 72, 73` | Production stockée, immobilisée, subventions — produits |
| `74, 75, 76, 77` | Subventions, autres produits, financiers, exceptionnels — produits |
| `78` | Reprises sur amortissements et provisions — produits |
| `79` | Transferts de charges — produits |

---

## 5. Patterns de requête courants

### 5.1 « Liste des sociétés avec leur dernier exercice validé »

```sql
SELECT g.name AS groupe, c.name AS societe, c.siren,
       e.end_date AS dernier_exercice, e.status
FROM groups g
JOIN companies c ON c.group_id = g.id
JOIN exercises e ON e.company_id = c.id
WHERE (c.id, e.end_date) IN (
  SELECT company_id, MAX(end_date)
  FROM exercises
  WHERE status IN ('IMPORTED','VALIDATED')
  GROUP BY company_id
)
ORDER BY g.name, c.name;
```

### 5.2 « Bilan résumé d'un exercice » (à partir de `statement_lines`)

```sql
SELECT line_code, line_label, position, net_amount
FROM statement_lines
WHERE exercise_id = ?
  AND statement_type = 'BALANCE_SHEET'
ORDER BY position, line_code;
```

Pour vérifier l'équilibre : `SUM(net_amount WHERE position='ASSET') + SUM(net_amount WHERE position IN ('LIABILITY','EQUITY'))` doit être ≈ 0 (rappel signe).

### 5.3 « Compte de résultat condensé »

```sql
SELECT line_code, line_label, position, net_amount
FROM statement_lines
WHERE exercise_id = ?
  AND statement_type = 'INCOME_STATEMENT'
  AND position = 'SUBTOTAL'   -- juste les soldes intermédiaires
ORDER BY line_code;
```

Sans le `position='SUBTOTAL'` : tu obtiens toutes les lignes détaillées.

### 5.4 « KPI principaux multi-exercices d'une société »

```sql
SELECT e.end_date,
       MAX(CASE WHEN fm.metric_code IN ('CA','CA_HT')    THEN fm.metric_value END) AS ca,
       MAX(CASE WHEN fm.metric_code = 'EBE'              THEN fm.metric_value END) AS ebe,
       MAX(CASE WHEN fm.metric_code = 'RESULTAT_NET'     THEN fm.metric_value END) AS rn,
       MAX(CASE WHEN fm.metric_code = 'BFR'              THEN fm.metric_value END) AS bfr,
       MAX(CASE WHEN fm.metric_code = 'CAF'              THEN fm.metric_value END) AS caf,
       MAX(CASE WHEN fm.metric_code = 'TRESORERIE_NETTE' THEN fm.metric_value END) AS tn
FROM exercises e
JOIN financial_metrics fm ON fm.exercise_id = e.id
WHERE e.company_id = ?
GROUP BY e.id, e.end_date
ORDER BY e.end_date;
```

### 5.5 « Top 20 plus gros mouvements sur le compte 401 (fournisseurs) d'un exercice »

```sql
SELECT ae.entry_date, ae.account_number, ae.aux_account_number,
       ae.account_label, ae.debit_amount, ae.credit_amount, ae.reference, ae.description
FROM accounting_entries ae
JOIN fec_imports fi ON fi.id = ae.fec_import_id
WHERE fi.exercise_id = ?
  AND ae.account_number LIKE '401%'
ORDER BY GREATEST(ae.debit_amount, ae.credit_amount) DESC
LIMIT 20;
```

### 5.6 « Soldes agrégés par compte (vue rapide d'une balance) »

```sql
SELECT ab.account_number, ab.aux_account_number, ab.account_label,
       ab.debit_total, ab.credit_total, (ab.debit_total - ab.credit_total) AS net
FROM account_balances ab
WHERE ab.exercise_id = ?
ORDER BY ab.account_number, ab.aux_account_number;
```

**Préférer cette table à l'agrégation de `accounting_entries`** — pré-calculé, beaucoup plus rapide.

### 5.7 « Vérifier l'équilibre Σ Débit = Σ Crédit d'un import »

```sql
SELECT fi.id, fi.filename,
       SUM(ae.debit_amount) AS total_db,
       SUM(ae.credit_amount) AS total_cr,
       SUM(ae.debit_amount) - SUM(ae.credit_amount) AS ecart,
       fi.total_balance_error AS ecart_enregistre
FROM fec_imports fi
JOIN accounting_entries ae ON ae.fec_import_id = fi.id
WHERE fi.exercise_id = ?
GROUP BY fi.id;
```

`ecart` doit être 0 (ou très proche, vu les arrondis). Sinon, l'import est cassé.

### 5.8 « R&R actifs d'un exercice »

```sql
SELECT rv.label AS version, ro.operation_type, ro.label AS operation,
       ro.category, rm.account_number, rm.account_label,
       rm.debit_amount, rm.credit_amount, rm.comment
FROM restatement_versions rv
JOIN restatement_operations ro ON ro.version_id = rv.id
JOIN restatement_movements rm ON rm.operation_id = ro.id
WHERE rv.exercise_id = ?
  AND rv.is_active = TRUE
ORDER BY ro.operation_type, ro.id, rm.position;
```

### 5.9 « Sociétés d'un groupe avec analytique activé »

```sql
SELECT g.id, g.name AS groupe, g.analytic_enabled,
       COUNT(DISTINCT c.id) AS nb_societes_avec_mapping
FROM groups g
LEFT JOIN companies c ON c.group_id = g.id
LEFT JOIN analytic_mappings am ON am.company_id = c.id
WHERE g.analytic_enabled = TRUE
GROUP BY g.id, g.name;
```

---

## 6. Pièges (gotchas) — à NE PAS faire

### 6.1 Confondre `accounting_entries` et `account_balances`

- `accounting_entries` = lignes FEC brutes, **une ligne par écriture**. 611k+ lignes.
- `account_balances` = pré-agrégé par (exercice, compte, tiers).

→ Pour un solde sur une période : utiliser `account_balances`.
→ Pour des mouvements détaillés : `accounting_entries` mais **toujours filtrer par exercice/import**, sinon timeout.

### 6.2 Sommer `debit - credit` sans connaître le signe attendu

Un capital social s'affiche `-100000` en base car c'est un compte de classe 1 (passif).
Si on demande « le capital de Synten en 2024 », **rappeler que la valeur négative est normale**.

→ Le mieux : utiliser `statement_lines.net_amount` (déjà signé selon la convention iaFEC) ou
`financial_metrics.metric_value` (déjà dans le sens lisible : CA positif, BFR signé, etc.).

### 6.3 Oublier `aux_account_number` sur 40x/41x

```sql
-- ❌ FAUX (compense les tiers entre eux)
SELECT account_number, SUM(debit_total - credit_total) AS solde
FROM account_balances
WHERE exercise_id = ? AND account_number LIKE '401%'
GROUP BY account_number;

-- ✅ JUSTE (un solde par tiers)
SELECT account_number, aux_account_number, debit_total - credit_total AS solde
FROM account_balances
WHERE exercise_id = ? AND account_number LIKE '401%'
ORDER BY account_number, aux_account_number;
```

### 6.4 Mélanger `exercises.end_date` et année civile

`end_date` est la **date de clôture**, pas l'année. Une société à clôture février aura des
exercices se terminant le 27/02/2022, 27/02/2023, etc. Le « millésime » courant correspond à
`YEAR(end_date)` mais pas toujours à l'année où l'activité s'est passée.

### 6.5 Considérer un exercice `PENDING` ou `ERROR` comme exploitable

```sql
WHERE e.status IN ('IMPORTED','VALIDATED')   -- toujours filtrer
```

### 6.6 Croiser plusieurs versions actives R&R / Éliminations / EA

Pour chaque (exercice, groupe, société selon le cas) une SEULE version R&R / Élim / EA est
`is_active = TRUE`. Pour les analyses « courantes », toujours filtrer `WHERE is_active = TRUE`.

### 6.7 Tenter d'écrire

Tout `INSERT/UPDATE/DELETE/DROP/ALTER/CREATE` est refusé (GRANT SELECT only côté MariaDB + garde
SELECT-only côté serveur MCP). Inutile d'essayer.

---

## 7. Données invisibles / hors périmètre

- Le **groupe TwinL** (`id=5`) et **toutes ses données** sont filtrés par les vues. Si une question
  porte sur TwinL : répondre que les données ne sont pas accessibles via ce MCP.
- La table `user_group_access` **n'est PAS exposée**.
- Les tables `twinl.*` (auth, projets, factures…) **ne sont pas dans ce MCP**. Pour ces données,
  le MCP `mcp-projea` existe (application **totalement distincte** — aucun lien avec iaFEC).

---

## 8. Conseils pour des analyses solides

1. **Toujours commencer par identifier la cible** : groupe ? société ? exercice ? période ? Demander si flou.
2. **Privilégier les tables pré-calculées** (`statement_lines`, `financial_metrics`, `account_balances`) sur les agrégations brutes de `accounting_entries` — sauf pour les drill-downs détaillés.
3. **Cross-checker les chiffres** : un CA calculé depuis `accounting_entries` (Σ comptes 70) doit matcher `financial_metrics WHERE metric_code='CA'`. Écart > 1 % = signal d'alerte (R&R appliqué, ou bug d'import).
4. **Mentionner les hypothèses** quand tu calcules un KPI à la main — la formule iaFEC peut différer (cf. `financial_metrics.calculation_formula`).
5. **Signaler** quand un import a un `total_balance_error` non nul ou un statut `ERROR` — chiffres non fiables.
6. **Format des dates** : MariaDB renvoie en ISO 8601 (`2024-09-29T22:00:00.000Z`). Pour l'utilisateur, formater en `JJ/MM/YYYY` ou « clôture 09/2024 ».
7. **Précision Decimal** : montants en `DECIMAL(18,2)`. Pour des K€/M€, arrondir explicitement et indiquer l'unité.

---

## 9. Pour aller plus loin (repo `iafec`)

- **Logique métier détaillée** (mapping PCG → bilan/CR, règles R&R Rc/Rt, plan analytique CRA, formules de KPI exactes, bugs historiques) → `skill_FEC.md`.
- **Infrastructure et sécurité** → `OPS.md` (les analyses utilisent le user `iaFEC_readonly`).
- **Modèle de données complet** (SQLAlchemy) → `backend/app/models/models.py`.
- **Calculs des indicateurs** → `backend/app/services/financial_statement_service.py` et `kpi_analysis_service.py`.

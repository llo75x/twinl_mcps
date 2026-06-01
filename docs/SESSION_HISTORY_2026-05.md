# Historique de session — 27-29 mai 2026 : création des MCPs iafec + projea

Document frozen — historique de référence. Pour la procédure à appliquer aujourd'hui, voir
[`INSTALL_PROCEDURE.md`](INSTALL_PROCEDURE.md). Pour les pièges, voir [`PITFALLS.md`](PITFALLS.md).

---

## Contexte initial

L'utilisateur souhaitait que Claude (Desktop / Chat / Cowork) puisse interroger directement les
bases iaFEC (analyses financières comptables) et twinl (ERP Projea), sans avoir à uploader
manuellement des fichiers FEC ou copier-coller des résultats.

Architecture initiale considérée :
- **Option A — MCP HTTPS distant hébergé sur le VPS** (OAuth, port public). Utilisable depuis claude.ai web. ~1 journée d'infra.
- **Option B — MCP stdio local** lancé par Claude Desktop, qui se connecte à MariaDB sur le port 3306 du VPS. ~15 min de setup mais usage limité à la machine locale.

Choix : **Option B** (stdio local) pour le test initial. Si besoin futur de multi-machine, migration possible vers A.

---

## Réalisations

### MCP `iafec-readonly` (27 mai)

- DB miroir `iafec_readonly` créée sur MariaDB.
- 21 vues `SQL SECURITY DEFINER` couvrant les tables iafec, filtrant le groupe TwinL (id=5).
- User `iaFEC_readonly@'%'` avec `GRANT SELECT` uniquement sur `iafec_readonly.*`.
- Entrée `iafec-readonly` ajoutée à `%APPDATA%\Claude\claude_desktop_config.json`.
- Validé end-to-end : 7 groupes, 10 sociétés, 29 exercices, 611 206 écritures comptables accessibles.

Pattern utilisé : **vues statiques explicites**, une par table iafec, avec JOINs filtrants
quand nécessaire (ex. `JOIN companies WHERE group_id <> 5`).

### MCP `MCP_Projea` (27-28 mai)

- DB miroir `twinl_readonly` créée sur MariaDB.
- 70 vues générées via **procédure stockée dynamique** sur `INFORMATION_SCHEMA.TABLES`, avec
  liste fixe d'exclusions (`tb_users`, `tb_factures`, `tb_elements_facture`, `tb_paiement`,
  `tb_achats`, `tb_compta_achats`, `sys_droits`).
- User `projea_readonly@'%'` avec `GRANT SELECT` uniquement sur `twinl_readonly.*`.
- Entrée `MCP_Projea` ajoutée à `claude_desktop_config.json`.

Pattern utilisé : **procédure dynamique** car twinl a 70+ tables, dont la majorité est exposable
sauf une liste explicite de sensibles.

### Cartographies pour Claude (28 mai)

Deux fichiers `skill_MCP_*.md` créés pour servir de **knowledge attaché aux Projects Claude
Desktop** :

- **`skill_MCP_iafec.md`** (~480 lignes) — 21 vues documentées, conventions de signe (`net = debit - credit`,
  négatif pour passif), 23 KPI référencés, 9 patterns SQL prêts à l'emploi, 7 gotchas
  (confusion `accounting_entries` vs `account_balances`, oubli `aux_account_number`, etc.).
  Reste dans le repo iafec (couplé à la sémantique iafec).

- **`skill_MCP_projea.md`** (~460 lignes) — 15 tables ERP twinl documentées sur 70, avec colonnes,
  relations, codes connus, tb_CodeStatut polymorphe, 9 patterns SQL, 8 gotchas. Inclut un
  **protocole d'auto-enrichissement** : quand Claude rencontre une table non documentée, il
  introspecte `INFORMATION_SCHEMA`, demande à l'utilisateur si ambigu, puis propose un texte
  à ajouter au doc.

### Refactor Projea en repo autonome (28 mai)

Initialement les fichiers MCP projea étaient dans le repo iafec. Décision : Projea doit avoir
son propre repo (autonomie, pas de couplage avec iafec).

- Création du repo `dev/projea` (sur GitHub `llo75x/projea`, privé) avec `README.md`, `.env`,
  `.gitignore`, `scripts/` et venv local.
- Déplacement des fichiers projea hors d'iafec, commit nettoyage dans iafec.
- Push initial du repo projea sur GitHub (commit `47a1d4c`).

### Pousse et déploiement v2.6 iafec (28 mai)

En parallèle, la session v2.6 iaFEC (autre conversation) a poussé et déployé sa version sur le
VPS. Nos commits MCP iafec ont été poussés à ce moment-là (au-dessus des commits v2.6).

---

## Le grand piège — réécriture de `claude_desktop_config.json` par CD (29 mai)

C'est le point qui a coûté le plus de temps. Documenté en détail dans
[`PITFALLS.md`](PITFALLS.md) §1.

### Symptôme

Après ajout de `MCP_Projea` et redémarrage de Claude Desktop, le serveur n'apparaissait pas
dans Cowork. Le log Cowork montrait Claude essayant d'interroger `twinl_readonly.bdd` via le
MCP `iafec-readonly` (le seul disponible) → refus MariaDB attendu (`SELECT command denied`).

### Diagnostic

Inspection de `claude_desktop_config.json` : `MCP_Projea` avait disparu, seul `iafec-readonly`
restait. Le `.bak` (sauvegardé par le verify script avant son écriture) ne contenait aussi
qu'`iafec-readonly`. Observation déterminante : la clé `"sidebarMode"` était passée de
`"epitaxy"` à `"chat"` — l'utilisateur avait changé un réglage UI **entre** l'écriture du
fichier par le verify et le redémarrage.

### Conclusion

Claude Desktop garde sa config MCP **en mémoire** et **réécrit le fichier** sur certains
événements UI (changement de réglage). À ce moment, CD a réécrit `claude_desktop_config.json`
depuis sa mémoire qui ne contenait que `iafec-readonly` (l'unique entrée chargée au précédent
démarrage), **clobbant** `MCP_Projea` que le verify script venait d'ajouter mais qui n'avait
pas encore été chargé en mémoire (faute de restart).

### Tentative de fix « édit pendant que CD est quitté »

Logique : si CD est complètement fermé, il ne peut pas réécrire. Tentative : quitter CD, exécuter
le verify script. Résultat : `FileNotFoundError: claude_desktop_config.json introuvable`.

**Découverte** : quand CD est quitté, le fichier `claude_desktop_config.json` **n'existe pas
sur disque**. Il est restauré au démarrage suivant de CD (depuis un store interne). Donc
éditer en mode « CD fermé » est impossible.

### Solution qui marche

Reproduction de la séquence qui avait réussi pour `iafec-readonly` :

1. Édit du fichier **pendant que CD tourne** (le fichier existe).
2. **Quit immédiat** de CD via systray (clean quit ne réécrit pas le fichier).
3. **Restart immédiat** de CD (lit le fichier → importe `MCP_Projea` en mémoire).
4. **Ne toucher à aucun réglage UI** entre les étapes 1, 2, 3.

`iafec-readonly` avait survécu à la 1ère install via exactement cette séquence (rapide,
sans changement de réglage entre). `MCP_Projea` avait échoué parce que l'utilisateur avait
été distrait entre l'écriture et le restart, et avait changé un réglage entre-temps.

Après application correcte de la procédure le 29 mai, `MCP_Projea` est devenu opérationnel
en Chat, Cowork et Claude Code.

---

## Création du repo central `twinl_mcps` (29 mai, ce repo)

Constat en fin de session : 2 MCPs ⇒ 2 copies quasi-identiques du verify Python, des
procédures dupliquées, des pièges (notamment le clobber) documentés deux fois.

Décision : créer un repo central **étape 1 — documentaire uniquement**. Le toolkit Python
générique attendra le 3e MCP. Contenu initial :

- `README.md` — vue d'ensemble, inventaire des MCPs actifs, roadmap.
- `docs/INSTALL_PROCEDURE.md` — procédure complète des 5 phases d'install, réutilisable.
- `docs/PITFALLS.md` — tous les pièges connus avec workarounds.
- `docs/ARCHITECTURE.md` — choix de conception (triple-couche RO, DB miroir, SECURITY DEFINER, statique vs dynamique).
- `docs/SESSION_HISTORY_2026-05.md` — ce document (historique frozen).
- `mcps/README.md` — placeholder pour la phase 2.

---

## État final au 29 mai 2026

| Élément | État |
|---|---|
| MCP `iafec-readonly` | ✅ Opérationnel en Chat, Cowork, Claude Code |
| MCP `MCP_Projea` | ✅ Opérationnel en Chat, Cowork, Claude Code |
| Repo `llo75x/iafec` | À jour, MCP iafec inclus, commits poussés via session v2.6 |
| Repo `llo75x/projea` | Nouveau, créé, poussé sur GitHub, autonome |
| Repo `llo75x/twinl_mcps` | Nouveau (ce repo), à pousser après création GitHub |
| Skill docs Claude | `skill_MCP_iafec.md` dans iafec, `skill_MCP_projea.md` dans projea |
| Passwords | Dans 1Password (« iaFEC MCP iaFEC_readonly », « MCP Projea projea_readonly ») + en clair dans les `*.local.sql` gitignored + dans `claude_desktop_config.json` |

## Décisions architecturales clés (datées)

| Date | Décision | Rationale |
|---|---|---|
| 27 mai | Stdio local plutôt que MCP HTTPS distant | Setup minimal pour un usage perso |
| 27 mai | DB miroir + vues `SECURITY DEFINER` plutôt que vues dans la base source | Le user RO ne voit pas du tout les tables sources, isolation forte |
| 27 mai | Triple-couche RO (vues + GRANT + flags MCP) plutôt que single layer | Défense en profondeur, coût ~nul |
| 28 mai | Pattern statique pour iafec, dynamique pour projea | iafec = filtres riches, projea = beaucoup de tables avec liste d'exclusions |
| 28 mai | Skill docs `.md` dans les repos data (pas dans un repo central MCP) | Cartographie couplée à la sémantique de la donnée |
| 28 mai | Projea = repo autonome (pas dans iafec) | Domaines séparés, cycles de vie différents |
| 29 mai | Procédure « édit pendant que CD tourne + quit/restart immédiats sans toucher aux réglages » | Seule séquence qui survit au mécanisme de réécriture de CD |
| 29 mai | Repo `twinl_mcps` central documentaire (étape 1) | Centraliser la procédure et les pièges. Refactor Python générique reporté au 3e MCP. |

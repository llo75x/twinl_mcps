-- =============================================================================
-- Setup MCP projea2 — base miroir read-only de `projea2` (CRM/ERP M&A PROJEA2)
--
-- À exécuter EN ROOT sur MariaDB du VPS. IDEMPOTENT (rejouable).
-- AVANT EXÉCUTION : remplacer CHANGE_ME_STRONG_PASSWORD par le password fort
--                   (voir docs/INSTALL_PROCEDURE.md §Génération du password).
--
-- Ce que fait ce script (modèle triple-couche, cf. docs/ARCHITECTURE.md §3) :
--   1. user MariaDB `projea2_mcp`@'%' (aucun droit sur la base source)
--   2. DB miroir `projea2_readonly`
--   3. vues `SQL SECURITY DEFINER` :
--        - une par table de `projea2`, en SELECT *, générées en boucle (pattern B)
--        - SAUF les tables sensibles/techniques exclues (jamais de vue)
--        - SAUF 5 tables exposées en COLONNES PROJETÉES (pattern A) : le secret
--          n'est pas dans la vue, donc il est inatteignable même en SELECT *
--   4. purge des vues orphelines (table source disparue)
--   5. GRANT SELECT only sur `projea2_readonly.*`
--
-- ⚠️ Pattern B = « tout est exposé sauf exclusions » : toute NOUVELLE table de
--    `projea2` sera exposée à la prochaine exécution. Si une future table porte
--    un secret (jeton, hash, clé d'API), l'ajouter à la liste d'exclusion (b) du §3b
--    AVANT de rejouer ce script. Rejouer après chaque migration Alembic qui ajoute
--    ou retire une table/colonne (une vue ne suit pas le schéma tout seule).
-- =============================================================================

-- ── 1. User read-only ────────────────────────────────────────────────────────
-- ⚠️ NOM : `projea2_mcp`, PAS `projea2_readonly` — malgré la convention des 2 autres
--    MCPs. L'application PROJEA2 possède DÉJÀ un compte `projea2_readonly`@'127.0.0.1'
--    (GRANT SELECT sur `projea2.*`, cf. projea2/deploy/setup-vps.sh) qui exécute le SQL
--    brut des listes Expert/IA. Deux comptes homonymes à hôtes différents, aux droits et
--    aux mots de passe distincts, c'est l'accident garanti : un `DROP USER
--    'projea2_readonly'` ou un `SET PASSWORD FOR 'projea2_readonly'` SANS hôte visent
--    `@'%'` par défaut et casseraient l'un ou l'autre. Les deux comptes restent donc
--    distinguables au nom, pas seulement à l'hôte.
--
-- DROP + CREATE : le password est réinitialisé à chaque exécution. Le tenir
-- synchronisé avec MCP_DB_PASS de /opt/twinl_mcps/mcps/projea2.env.
DROP USER IF EXISTS 'projea2_mcp'@'%';
CREATE USER 'projea2_mcp'@'%' IDENTIFIED BY 'CHANGE_ME_STRONG_PASSWORD';

-- ── 2. DB miroir ─────────────────────────────────────────────────────────────
CREATE DATABASE IF NOT EXISTS projea2_readonly
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- ── 3a. Vues à colonnes projetées (tables portant un secret) ─────────────────
-- Ces 5 tables NE PASSENT PAS par la boucle : leur vue est écrite à la main et
-- omet la colonne sensible. Un `SELECT *` sur la vue ne peut donc pas la lire.

-- users : identité et état du compte, JAMAIS le hash de mot de passe.
CREATE OR REPLACE VIEW projea2_readonly.users AS
  SELECT id, email, display_name, is_active, password_changed_at,
         phone, function_title, office_id, created_at, updated_at
    FROM projea2.users;

-- email_messages : `unsubscribe_token` retiré (jeton de capacité — il permet de
-- désinscrire un destinataire). `message_uid` reste (corrélation des bounces).
CREATE OR REPLACE VIEW projea2_readonly.email_messages AS
  SELECT id, event_id, company_contact_id, email_type_id, template_id,
         from_email, to_email, subject, body_html, status, error_message,
         sent_by_user_id, operation_id, message_uid,
         opened_at, unsubscribed_at, bounced_at, bounce_kind, bounce_detail,
         created_at, updated_at
    FROM projea2.email_messages;

-- tracking_links : `token` retiré (jeton de capacité — il permet de simuler un
-- clic, donc de fabriquer un event « Email ouvert » et de fausser le tracking).
CREATE OR REPLACE VIEW projea2_readonly.tracking_links AS
  SELECT id, email_message_id, destination_url, label,
         first_clicked_at, created_at, updated_at
    FROM projea2.tracking_links;

-- export_approvals : `token` retiré (jeton de capacité — il autorise le
-- téléchargement d'un export approuvé).
CREATE OR REPLACE VIEW projea2_readonly.export_approvals AS
  SELECT id, requester_user_id, source_type, source_id, source_name,
         source_fingerprint, entity_type, template_id, format, row_count,
         status, decided_by_user_id, decided_at, expires_at,
         created_at, updated_at
    FROM projea2.export_approvals;

-- tracking_hits : `ip` retirée (donnée personnelle, purgée à 13 mois côté app).
-- `user_agent` et `is_bot` restent — ils servent l'analyse anti-robots.
CREATE OR REPLACE VIEW projea2_readonly.tracking_hits AS
  SELECT id, tracking_link_id, kind, hit_at, method, user_agent, is_bot,
         created_at, updated_at
    FROM projea2.tracking_hits;

-- ── 3b. Vues SELECT * pour toutes les autres tables (boucle) ─────────────────
DELIMITER //
CREATE OR REPLACE PROCEDURE projea2_readonly.build_views()
BEGIN
  DECLARE done INT DEFAULT FALSE;
  DECLARE tbl VARCHAR(255);
  DECLARE cur CURSOR FOR
    SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = 'projea2'
      AND TABLE_TYPE = 'BASE TABLE'
      -- (a) tables déjà couvertes par une vue projetée en §3a
      AND TABLE_NAME NOT IN (
        'users', 'email_messages', 'tracking_links', 'export_approvals',
        'tracking_hits',
      -- (b) tables JAMAIS exposées
        --   secrets / capacités
        'password_tokens',        -- hashs de jetons de reset et d'invitation
        --   plomberie technique
        'alembic_version',        -- version de schéma
        'migration_id_map',       -- correspondance legacy↔projea2 (volumineuse)
        'migration_runs',         -- journal des runs de migration
        'migration_watermarks'    -- bornes de reprise du pipeline
      )
    ORDER BY TABLE_NAME;
  DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = TRUE;
  OPEN cur;
  read_loop: LOOP
    FETCH cur INTO tbl;
    IF done THEN LEAVE read_loop; END IF;
    SET @ddl = CONCAT(
      'CREATE OR REPLACE VIEW projea2_readonly.`', tbl,
      '` AS SELECT * FROM projea2.`', tbl, '`'
    );
    PREPARE stmt FROM @ddl;
    EXECUTE stmt;
    DEALLOCATE PREPARE stmt;
  END LOOP;
  CLOSE cur;
END //
DELIMITER ;
CALL projea2_readonly.build_views();
DROP PROCEDURE projea2_readonly.build_views;

-- ── 4. Purge des vues orphelines ─────────────────────────────────────────────
-- `CREATE OR REPLACE VIEW` ne supprime pas la vue d'une table disparue (ex.
-- `campaigns`, `pappers_cache`, `company_external_ids`, `operation_permissions`,
-- toutes droppées par Alembic). Sans cette étape, la vue survit et renvoie une
-- erreur à l'exécution — ou, pire, exclut une table nouvellement interdite.
-- Deux précautions :
--   * on lit `INFORMATION_SCHEMA.TABLES` (TABLE_TYPE='VIEW') et NON `.VIEWS` : une vue orpheline
--     est cassée par construction, et `.VIEWS` en force l'expansion ;
--   * la liste est FIGÉE dans une table temporaire avant de dropper — on ne mute pas le catalogue
--     pendant qu'un curseur le parcourt.
DELIMITER //
CREATE OR REPLACE PROCEDURE projea2_readonly.drop_orphan_views()
BEGIN
  DROP TEMPORARY TABLE IF EXISTS projea2_readonly.tmp_orphan_views;
  CREATE TEMPORARY TABLE projea2_readonly.tmp_orphan_views (v VARCHAR(255)) ENGINE = MEMORY;

  INSERT INTO projea2_readonly.tmp_orphan_views (v)
    SELECT x.TABLE_NAME FROM INFORMATION_SCHEMA.TABLES x
    WHERE x.TABLE_SCHEMA = 'projea2_readonly'
      AND x.TABLE_TYPE = 'VIEW'
      AND (
        -- la table source n'existe plus
        NOT EXISTS (
          SELECT 1 FROM INFORMATION_SCHEMA.TABLES t
          WHERE t.TABLE_SCHEMA = 'projea2'
            AND t.TABLE_TYPE = 'BASE TABLE'
            AND t.TABLE_NAME = x.TABLE_NAME
        )
        -- ou la table est passée dans la liste d'exclusion (b) ci-dessus
        OR x.TABLE_NAME IN (
          'password_tokens', 'alembic_version',
          'migration_id_map', 'migration_runs', 'migration_watermarks'
        )
      );

  BEGIN
    DECLARE done INT DEFAULT FALSE;
    DECLARE vw VARCHAR(255);
    DECLARE cur CURSOR FOR SELECT v FROM projea2_readonly.tmp_orphan_views ORDER BY v;
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = TRUE;
    OPEN cur;
    purge_loop: LOOP
      FETCH cur INTO vw;
      IF done THEN LEAVE purge_loop; END IF;
      SET @ddl = CONCAT('DROP VIEW IF EXISTS projea2_readonly.`', vw, '`');
      PREPARE stmt FROM @ddl;
      EXECUTE stmt;
      DEALLOCATE PREPARE stmt;
    END LOOP;
    CLOSE cur;
  END;

  DROP TEMPORARY TABLE IF EXISTS projea2_readonly.tmp_orphan_views;
END //
DELIMITER ;
CALL projea2_readonly.drop_orphan_views();
DROP PROCEDURE projea2_readonly.drop_orphan_views;

-- ── 5. Grants — SELECT only, et RIEN sur la base source ──────────────────────
REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'projea2_mcp'@'%';
GRANT SELECT ON projea2_readonly.* TO 'projea2_mcp'@'%';
FLUSH PRIVILEGES;

-- ── 6. Vérifications (affichées à la sortie de mysql) ────────────────────────
SHOW GRANTS FOR 'projea2_mcp'@'%';

-- Le compte de l'APPLICATION doit être INTACT : la preuve qu'on n'a pas marché sur les
-- listes Expert/IA de PROJEA2. Attendu : une ligne `projea2_readonly` / `127.0.0.1`
-- (et AUCUNE ligne `projea2_readonly` / `%`, qui signalerait une collision de nom).
-- Requête tolérante : pas de `SHOW GRANTS`, qui échouerait si le compte n'existe pas
-- encore et interromprait les contrôles suivants.
SELECT User AS compte_app, Host AS hote_app
  FROM mysql.user
 WHERE User IN ('projea2_readonly', 'projea2_app', 'projea2_admin', 'projea2_mcp')
 ORDER BY User, Host;

SELECT COUNT(*) AS nb_vues
  FROM information_schema.tables
 WHERE table_schema = 'projea2_readonly' AND table_type = 'VIEW';

-- Doit renvoyer 0 ligne : aucune table exclue ne doit avoir de vue.
SELECT table_name AS fuite_vue_exclue
  FROM information_schema.tables
 WHERE table_schema = 'projea2_readonly'
   AND table_name IN ('password_tokens', 'alembic_version',
                      'migration_id_map', 'migration_runs',
                      'migration_watermarks');

-- Doit renvoyer 0 ligne : aucune colonne secrète ne doit être exposée.
SELECT table_name, column_name AS fuite_colonne
  FROM information_schema.columns
 WHERE table_schema = 'projea2_readonly'
   AND (
     (table_name = 'users'            AND column_name = 'password_hash')
  OR (table_name = 'email_messages'   AND column_name = 'unsubscribe_token')
  OR (table_name = 'tracking_links'   AND column_name = 'token')
  OR (table_name = 'export_approvals' AND column_name = 'token')
  OR (table_name = 'tracking_hits'    AND column_name = 'ip')
   );

-- Doit renvoyer 0 ligne : aucune table de `projea2` ne doit être oubliée sans
-- décision explicite (une ligne ici = table nouvelle → l'exposer ou l'exclure).
SELECT t.table_name AS table_source_sans_vue
  FROM information_schema.tables t
 WHERE t.table_schema = 'projea2'
   AND t.table_type = 'BASE TABLE'
   AND t.table_name NOT IN ('password_tokens', 'alembic_version',
                            'migration_id_map', 'migration_runs',
                            'migration_watermarks')
   AND NOT EXISTS (
     SELECT 1 FROM information_schema.tables v
      WHERE v.table_schema = 'projea2_readonly'
        AND v.table_type = 'VIEW'
        AND v.table_name = t.table_name
   );

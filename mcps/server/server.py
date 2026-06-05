"""Serveur MCP read-only générique, transport HTTPS (Streamable HTTP) pour claude.ai.

Expose UNE base miroir MariaDB read-only (`<base>_readonly`) via un seul outil
`mysql_query(sql)`. Le même fichier sert iafec ET projea : tout est paramétré par
variables d'environnement (voir `.env.example`). Une instance = une base = un sous-domaine.

Modèle de sécurité (cf. ../../docs/ARCHITECTURE.md §3) — défense en profondeur :
  Couche 1  vues SQL SECURITY DEFINER (filtrage structurel, côté MariaDB)
  Couche 2  GRANT SELECT only sur <base>_readonly.* (le user RO ne peut RIEN muter)  ← rempart dur
  Couche 3  garde SELECT-only par AST sqlglot, ICI (remplace les flags ALLOW_*_OPERATION)
  Couche 4  OAuth 2.1 WorkOS AuthKit (seul Laurent, invite-only) + filet allowlist de sujet

⚠️ Lignes sensibles à la version de FastMCP signalées par `# [FASTMCP-API]`. La phase de
build sur le VPS valide l'API exacte contre la version installée (voir
../../docs/INSTALL_PROCEDURE_HTTPS.md). En cas d'écart, ajuster ces points uniquement.
"""

from __future__ import annotations

import datetime
import decimal
import json
import logging
import os

import pymysql
import pymysql.cursors
import sqlglot
from sqlglot import exp

# ── Configuration via environnement ──────────────────────────────────────────

DB_HOST = os.environ.get("MCP_DB_HOST", "host.docker.internal")
DB_PORT = int(os.environ.get("MCP_DB_PORT", "3306"))
DB_USER = os.environ["MCP_DB_USER"]
DB_PASS = os.environ["MCP_DB_PASS"]
DB_NAME = os.environ["MCP_DB_NAME"]

AUTHKIT_DOMAIN = os.environ["AUTHKIT_DOMAIN"]   # ex. https://xxxx.authkit.app
BASE_URL = os.environ["BASE_URL"]               # ex. https://mcp-iafec.twinl.fr
SERVER_NAME = os.environ.get("MCP_SERVER_NAME", "mcp-readonly")
MCP_PORT = int(os.environ.get("MCP_PORT", "8080"))

BIND_HOST = os.environ.get("MCP_BIND_HOST", "0.0.0.0")         # 0.0.0.0 DANS le conteneur : l'isolation
                                                                # vient du bind hôte 127.0.0.1:80xx (compose)
MAX_ROWS = int(os.environ.get("MCP_MAX_ROWS", "1000"))         # plancher dur de protection
MAX_BYTES = int(os.environ.get("MCP_MAX_BYTES", "1000000"))    # ~1 Mo de payload max
STMT_TIMEOUT_S = float(os.environ.get("MCP_STMT_TIMEOUT_S", "20"))  # MariaDB max_statement_time (secondes)

# Allowlist de sujets (emails/sub) — filet SECONDAIRE, la garde primaire est l'invite-only WorkOS.
# Vide = on ne fait QUE confiance à WorkOS (déjà restreint à Laurent).
ALLOWED_SUBJECTS = {
    s.strip().lower() for s in os.environ.get("MCP_ALLOWED_SUBJECTS", "").split(",") if s.strip()
}

# ── Logging : métadonnées uniquement, JAMAIS de données ni de littéraux SQL ───
# DEBUG interdit en prod (éviterait tout dump de payload). On reste en INFO.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger(SERVER_NAME)


# ── Garde SELECT-only (couche 3) — parsing AST robuste, fail-closed ───────────

def _exp_types(*names: str) -> tuple[type, ...]:
    """Résout des classes sqlglot.exp par nom, en IGNORANT celles absentes de la version
    installée (évite tout AttributeError au chargement selon la version de sqlglot)."""
    return tuple(getattr(exp, n) for n in names if hasattr(exp, n))


# Types de racine considérés comme "lecture".
_READ_ROOTS = _exp_types("Select", "Union", "Subquery", "Show", "Describe", "Pragma")
# Tout nœud d'écriture / DDL / appel — sa simple présence dans l'arbre fait rejeter.
# (classes critiques toujours présentes ; un TRUNCATE sans TruncateTable retombe sur Command.)
_FORBIDDEN_NODES = _exp_types(
    "Insert", "Update", "Delete", "Merge",
    "Create", "Drop", "Alter", "TruncateTable",
    "Command",          # SET/CALL/GRANT/… non explicitement parsés → rejet par prudence
    "Into",             # SELECT ... INTO OUTFILE/DUMPFILE/@var
)


class SqlRejected(ValueError):
    """Requête rejetée par la garde read-only (fail-closed)."""


def _anonymize(stmt: exp.Expression) -> str:
    """SQL anonymisé pour les logs : littéraux masqués par '?'. Jamais de données en clair."""
    masked = stmt.transform(
        lambda n: exp.Placeholder() if isinstance(n, exp.Literal) else n
    )
    return masked.sql(dialect="mysql")


def validate_read_only(sql: str) -> tuple[str, str]:
    """Valide que `sql` est UNE requête de lecture. Renvoie (sql_sûr, sql_anonymisé).

    Fail-closed : toute ambiguïté (parse KO, multi-statements, racine non-lecture,
    nœud interdit) lève SqlRejected.
    """
    try:
        statements = [s for s in sqlglot.parse(sql, read="mysql") if s is not None]
    except Exception:
        raise SqlRejected("SQL non analysable — rejeté (fail-closed).")

    if len(statements) != 1:
        raise SqlRejected("Exactement une requête est autorisée (pas de requêtes empilées).")

    stmt = statements[0]

    if not isinstance(stmt, _READ_ROOTS):
        raise SqlRejected(f"Type de requête non autorisé : {type(stmt).__name__} (lecture seule).")

    forbidden = next(iter(stmt.find_all(*_FORBIDDEN_NODES)), None)
    if forbidden is not None:
        raise SqlRejected(f"Construction interdite détectée : {type(forbidden).__name__}.")

    # LIMIT de sécurité (best-effort, en plus du fetchmany) pour les SELECT sans LIMIT explicite.
    if isinstance(stmt, exp.Select) and stmt.args.get("limit") is None:
        stmt = stmt.limit(MAX_ROWS)

    return stmt.sql(dialect="mysql"), _anonymize(stmt)


# ── Sérialisation sûre des valeurs (Decimal/date/bytes → str) ─────────────────

def _coerce(value: object) -> object:
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time, datetime.timedelta)):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return value


# ── Exécution avec curseur NON-bufferisé + double plafond (lignes / octets) ───

def run_query(safe_sql: str) -> dict:
    """Ouvre une connexion COURTE (par appel), exécute en read-only, plafonne la réponse.

    SSCursor (non-bufferisé) : seules MAX_ROWS+1 lignes sont tirées du serveur, quelle que
    soit la taille réelle du résultat → RAM du conteneur bornée. On ferme la *connexion*
    (et non le curseur) pour abandonner le reste du flux sans le drainer.
    """
    conn = pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS, database=DB_NAME,
        cursorclass=pymysql.cursors.SSCursor,   # non-bufferisé : streaming
        autocommit=True,
        connect_timeout=10, read_timeout=int(STMT_TIMEOUT_S) + 10,
        charset="utf8mb4",
        # multi-statements NON activé (défaut) — rendu explicite par l'absence de CLIENT.MULTI_STATEMENTS
    )
    try:
        cur = conn.cursor()
        cur.execute("SET SESSION TRANSACTION READ ONLY")          # couche défense supplémentaire
        cur.execute("SET SESSION max_statement_time=%s" % float(STMT_TIMEOUT_S))  # MariaDB (secondes)
        cur.execute(safe_sql)

        columns = [d[0] for d in cur.description] if cur.description else []
        raw = cur.fetchmany(MAX_ROWS + 1)        # +1 pour détecter la troncature
        truncated_rows = len(raw) > MAX_ROWS
        raw = raw[:MAX_ROWS]

        rows: list[dict] = []
        size = 0
        truncated_bytes = False
        for r in raw:
            row = {columns[i]: _coerce(v) for i, v in enumerate(r)}
            size += len(json.dumps(row, ensure_ascii=False, default=str))
            if size > MAX_BYTES:
                truncated_bytes = True
                break
            rows.append(row)
    finally:
        conn.close()   # abandonne tout reste de flux côté serveur sans drainer

    truncated = truncated_rows or truncated_bytes
    result = {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated}
    if truncated:
        result["note"] = (
            f"Réponse tronquée : {len(rows)} lignes renvoyées (plafond MAX_ROWS={MAX_ROWS}, "
            f"MAX_BYTES={MAX_BYTES}). Affine la requête (agrégation, WHERE, LIMIT explicite)."
        )
    return result


# ── Serveur FastMCP ───────────────────────────────────────────────────────────

from fastmcp import FastMCP                                          # [FASTMCP-API]
from fastmcp.server.auth.providers.workos import AuthKitProvider     # [FASTMCP-API]

auth = AuthKitProvider(authkit_domain=AUTHKIT_DOMAIN, base_url=BASE_URL)   # [FASTMCP-API]
# stateless_http=True : pas de session SSE persistante (critique avec Apache mpm_prefork).
mcp = FastMCP(name=SERVER_NAME, auth=auth, stateless_http=True)            # [FASTMCP-API]


def _check_subject() -> str | None:
    """Filet SECONDAIRE : vérifie le sujet du JWT contre l'allowlist (si configurée).

    La garde primaire reste l'invite-only WorkOS. Ne lève que si une allowlist est définie
    ET que le sujet n'y figure pas. Tolérant à l'API exacte (best-effort).
    """
    if not ALLOWED_SUBJECTS:
        return None
    try:
        from fastmcp.server.dependencies import get_access_token   # [FASTMCP-API]
        token = get_access_token()
        claims = getattr(token, "claims", {}) or {}
        subject = (claims.get("email") or claims.get("sub") or "").lower()
    except Exception:
        # Si on ne sait pas lire le token, on ne bloque pas (WorkOS a déjà filtré l'accès).
        return None
    if subject and subject not in ALLOWED_SUBJECTS:
        raise SqlRejected("Sujet non autorisé.")
    return subject or None


@mcp.tool
def mysql_query(sql: str) -> dict:
    """Exécute une requête SQL **en lecture seule** sur la base miroir read-only.

    Seules les requêtes de lecture (SELECT, y compris CTE WITH/UNION, SHOW, DESCRIBE) sont
    autorisées. Les réponses sont plafonnées (lignes et octets) ; si « truncated » est vrai,
    affine la requête (agrège ou filtre) plutôt que de tout re-tirer.
    """
    subject = _check_subject()
    safe_sql, anon_sql = validate_read_only(sql)
    try:
        result = run_query(safe_sql)
    except pymysql.OperationalError as e:
        # Reconnexion / coupure DB : un retry, sinon erreur MCP propre (le process ne crashe pas).
        log.warning("OperationalError, retry once: %s", e.args[0] if e.args else e)
        result = run_query(safe_sql)
    log.info("query ok | sub=%s | rows=%s | trunc=%s | sql=%s",
             subject, result["row_count"], result["truncated"], anon_sql)
    return result


# ── Route /health (hors MCP, non authentifiée) pour le healthcheck Docker ─────

from starlette.responses import JSONResponse                        # [FASTMCP-API]


@mcp.custom_route("/health", methods=["GET"])                       # [FASTMCP-API]
async def health(_request):
    """Healthcheck : vérifie aussi l'accessibilité MariaDB via un SELECT 1 borné."""
    try:
        conn = pymysql.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASS, database=DB_NAME,
            connect_timeout=5, read_timeout=5, autocommit=True,
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        finally:
            conn.close()
        return JSONResponse({"status": "ok"})
    except Exception as e:  # noqa: BLE001 — health doit renvoyer un statut, pas crasher
        return JSONResponse({"status": "db_unreachable", "error": type(e).__name__}, status_code=503)


if __name__ == "__main__":
    log.info("Starting %s on %s:%s (base=%s, max_rows=%s)", SERVER_NAME, BIND_HOST, MCP_PORT, DB_NAME, MAX_ROWS)
    # Transport Streamable HTTP, stateless. Bind 0.0.0.0 dans le conteneur ; l'exposition publique
    # est limitée par le mapping hôte 127.0.0.1:80xx (compose) + le reverse proxy Apache.
    mcp.run(transport="http", host=BIND_HOST, port=MCP_PORT)        # [FASTMCP-API]

"""Serveur MCP read-only générique, transport HTTPS (Streamable HTTP) pour claude.ai.

Expose UNE base miroir MariaDB read-only (`<base>_readonly`) via un seul outil
`mysql_query(sql)`. Le même fichier sert iafec ET projea : tout est paramétré par
variables d'environnement (voir `.env.example`). Une instance = une base = un sous-domaine.

Modèle de sécurité (cf. ../../docs/ARCHITECTURE.md §3) — défense en profondeur :
  Couche 1  vues SQL SECURITY DEFINER (filtrage structurel, côté MariaDB)
  Couche 2  GRANT SELECT only sur <base>_readonly.* (le user RO ne peut RIEN muter)  ← rempart dur
  Couche 3  garde SELECT-only par AST sqlglot, ICI (remplace les flags ALLOW_*_OPERATION)
  Couche 4  OAuth 2.1 WorkOS AuthKit (seul Laurent, invite-only) + filet allowlist de sujet

Extraction volumineuse (Couche 5) :
  Toute réponse dépassant SLACK_NOTIFY_THRESHOLD lignes OU SLACK_BYTES_THRESHOLD octets
  déclenche une demande d'approbation interactive Slack (Block Kit, boutons Approuver/Refuser).
  Le tool call reste bloqué jusqu'au clic ou jusqu'à SLACK_APPROVAL_TIMEOUT_S secondes.
  Activé uniquement si SLACK_SIGNING_SECRET est défini.

⚠️ Lignes sensibles à la version de FastMCP signalées par `# [FASTMCP-API]`. La phase de
build sur le VPS valide l'API exacte contre la version installée (voir
../../docs/INSTALL_PROCEDURE_HTTPS.md). En cas d'écart, ajuster ces points uniquement.
"""

from __future__ import annotations

import datetime
import decimal
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request

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

# Instructions du serveur (champ MCP standard, lu automatiquement par TOUT client : claude.ai
# web, Claude Desktop, Cowork, Claude Code). PAR INSTANCE → projea ≠ iafec, jamais mélangés.
# Source : un fichier monté dans le conteneur (MCP_INSTRUCTIONS_FILE), sinon inline
# (MCP_INSTRUCTIONS). Absent = pas d'instructions (rétro-compatible).
INSTRUCTIONS_FILE = os.environ.get("MCP_INSTRUCTIONS_FILE", "/app/instructions.md").strip()
INSTRUCTIONS_INLINE = os.environ.get("MCP_INSTRUCTIONS", "")

BIND_HOST = os.environ.get("MCP_BIND_HOST", "0.0.0.0")         # 0.0.0.0 DANS le conteneur : l'isolation
                                                                # vient du bind hôte 127.0.0.1:80xx (compose)
MAX_ROWS = int(os.environ.get("MCP_MAX_ROWS", "1000"))         # plancher dur de protection
MAX_BYTES = int(os.environ.get("MCP_MAX_BYTES", "1000000"))    # ~1 Mo de payload max
STMT_TIMEOUT_S = float(os.environ.get("MCP_STMT_TIMEOUT_S", "20"))  # MariaDB max_statement_time (secondes)

# ── Approbation Slack pour extractions volumineuses ───────────────────────────
# Activé uniquement si SLACK_SIGNING_SECRET est défini.
# SLACK_WEBHOOK_URL : Incoming Webhook pour envoyer la demande d'approbation.
# SLACK_SIGNING_SECRET : secret de signature de l'app Slack (Basic Information).
# SLACK_NOTIFY_THRESHOLD : seuil en lignes (défaut 200).
# SLACK_BYTES_THRESHOLD : seuil en octets (défaut 50 000 — couvre les GROUP_CONCAT).
# SLACK_APPROVAL_TIMEOUT_S : délai d'attente max en secondes (défaut 120).
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "").strip()
SLACK_NOTIFY_THRESHOLD = int(os.environ.get("SLACK_NOTIFY_THRESHOLD", "200"))
SLACK_BYTES_THRESHOLD = int(os.environ.get("SLACK_BYTES_THRESHOLD", "50000"))
SLACK_APPROVAL_TIMEOUT_S = int(os.environ.get("SLACK_APPROVAL_TIMEOUT_S", "120"))

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
    Retourne aussi `byte_size` (octets JSON des données) pour la détection d'extraction volumineuse.
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
            row_size = len(json.dumps(row, ensure_ascii=False, default=str))
            size += row_size
            if size > MAX_BYTES:
                truncated_bytes = True
                break
            rows.append(row)
    finally:
        conn.close()   # abandonne tout reste de flux côté serveur sans drainer

    truncated = truncated_rows or truncated_bytes
    result = {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "byte_size": size,
        "truncated": truncated,
    }
    if truncated:
        result["note"] = (
            f"Réponse tronquée : {len(rows)} lignes renvoyées (plafond MAX_ROWS={MAX_ROWS}, "
            f"MAX_BYTES={MAX_BYTES}). Affine la requête (agrégation, WHERE, LIMIT explicite)."
        )
    return result


# ── Approbation interactive Slack ─────────────────────────────────────────────

# État en mémoire des demandes d'approbation en cours.
# Clé : request_id (token aléatoire). Valeur : threading.Event (signalé par /slack/action).
_pending_approvals: dict[str, threading.Event] = {}
_approval_results: dict[str, bool] = {}   # True = approuvé, False = refusé


def _slack_approval_active() -> bool:
    return bool(SLACK_WEBHOOK_URL and SLACK_SIGNING_SECRET)


def _is_large_result(result: dict) -> bool:
    return (
        result["row_count"] > SLACK_NOTIFY_THRESHOLD
        or result["byte_size"] > SLACK_BYTES_THRESHOLD
    )


def _post_to_url(url: str, payload: dict, timeout: int = 5) -> None:
    """POST JSON vers une URL Slack (webhook ou response_url). Fail-soft."""
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                log.warning("slack post: status %s for %s", resp.status, url[:60])
    except Exception as e:
        log.warning("slack post failed: %s (%s)", type(e).__name__, url[:60])


def _send_slack_approval_request(request_id: str, row_count: int, byte_size: int) -> bool:
    """Envoie la demande d'approbation dans Slack. Retourne True si l'envoi a réussi."""
    if not SLACK_WEBHOOK_URL:
        return False
    size_kb = byte_size // 1024
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    ":warning: *MCP Projea — Extraction volumineuse*\n"
                    f"Une requête demande *{row_count} lignes* / *{size_kb} Ko*.\n"
                    f"Seuils configurés : {SLACK_NOTIFY_THRESHOLD} lignes · "
                    f"{SLACK_BYTES_THRESHOLD // 1024} Ko\n\n"
                    f"Approuves-tu cette extraction ?"
                ),
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approuver"},
                    "style": "primary",
                    "action_id": "approve",
                    "value": request_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Refuser"},
                    "style": "danger",
                    "action_id": "deny",
                    "value": request_id,
                },
            ],
        },
    ]
    _post_to_url(SLACK_WEBHOOK_URL, {
        "text": f"Extraction volumineuse — {row_count} lignes / {size_kb} Ko — approbation requise",
        "blocks": blocks,
    })
    return True


def _verify_slack_signature(body: bytes, timestamp: str, signature: str) -> bool:
    """Vérifie la signature HMAC-SHA256 de Slack. Fail-closed."""
    if not SLACK_SIGNING_SECRET:
        return False
    try:
        if abs(time.time() - int(timestamp)) > 300:   # replay > 5 min → rejeté
            return False
        base = f"v0:{timestamp}:{body.decode('utf-8')}".encode("utf-8")
        expected = "v0=" + hmac.new(
            SLACK_SIGNING_SECRET.encode("utf-8"), base, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False


def _wait_for_approval(request_id: str) -> bool:
    """Bloque jusqu'à approbation/refus ou timeout. Retourne True si approuvé."""
    event = threading.Event()
    _pending_approvals[request_id] = event
    try:
        approved_in_time = event.wait(timeout=SLACK_APPROVAL_TIMEOUT_S)
        if not approved_in_time:
            log.warning("slack approval timeout | request_id=%s", request_id)
            return False
        return _approval_results.get(request_id, False)
    finally:
        _pending_approvals.pop(request_id, None)
        _approval_results.pop(request_id, None)


# ── Serveur FastMCP ───────────────────────────────────────────────────────────

from fastmcp import FastMCP                                          # [FASTMCP-API]
from fastmcp.server.auth.providers.workos import AuthKitProvider     # [FASTMCP-API]

def _load_instructions() -> str | None:
    """Charge les instructions de l'instance : fichier monté en priorité, sinon inline.

    Fail-soft : un fichier illisible ne fait PAS crasher le serveur (les instructions sont un
    confort, pas un prérequis au fonctionnement). On loggue la longueur, jamais le contenu.
    """
    if INSTRUCTIONS_FILE:
        try:
            with open(INSTRUCTIONS_FILE, encoding="utf-8") as f:
                text = f.read().strip()
            if text:
                log.info("instructions loaded from file (%s chars)", len(text))
                return text
            log.warning("instructions file is empty: %s", INSTRUCTIONS_FILE)
        except OSError as e:
            log.warning("instructions file unreadable (%s): %s", INSTRUCTIONS_FILE, type(e).__name__)
    if INSTRUCTIONS_INLINE.strip():
        text = INSTRUCTIONS_INLINE.strip()
        log.info("instructions loaded from env inline (%s chars)", len(text))
        return text
    log.info("no instructions configured for this instance")
    return None


SERVER_INSTRUCTIONS = _load_instructions()


def _extract_digest(instructions: str | None) -> str | None:
    """Digest court = bloc entre `<!-- DIGEST -->` et `<!-- /DIGEST -->` du fichier d'instructions.

    Pourquoi : certains clients MCP (dont claude.ai web) n'exposent PAS le champ `instructions` au
    modèle. En revanche, TOUS exposent la DESCRIPTION des outils. On injecte donc ce digest dans la
    description de `mysql_query` (garde-fou anti-improvisation, à coût réduit par requête), le data
    model complet restant disponible via l'outil `get_data_model_reference`.
    """
    if not instructions:
        return None
    m = re.search(r"<!--\s*DIGEST\s*-->(.*?)<!--\s*/DIGEST\s*-->", instructions, re.DOTALL)
    return m.group(1).strip() if m else None


TOOL_DIGEST = _extract_digest(SERVER_INSTRUCTIONS)
if TOOL_DIGEST:
    log.info("tool digest extracted (%s chars)", len(TOOL_DIGEST))

auth = AuthKitProvider(authkit_domain=AUTHKIT_DOMAIN, base_url=BASE_URL)   # [FASTMCP-API]
# stateless_http=True : pas de session SSE persistante (critique avec Apache mpm_prefork).
# instructions : champ MCP standard. ATTENTION : claude.ai web ne le surface PAS au modèle ; il
# n'est utile qu'aux clients qui l'exposent (ex. Claude Code). Le canal FIABLE cross-client est la
# description d'outil (cf. _MYSQL_QUERY_DESC + get_data_model_reference).
mcp = FastMCP(                                                            # [FASTMCP-API]
    name=SERVER_NAME, auth=auth, stateless_http=True, instructions=SERVER_INSTRUCTIONS,
)


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


_MYSQL_QUERY_BASE_DESC = (
    "Exécute une requête SQL **en lecture seule** sur la base miroir read-only.\n\n"
    "Seules les requêtes de lecture (SELECT, y compris CTE WITH/UNION, SHOW, DESCRIBE) sont "
    "autorisées. Les réponses sont plafonnées (lignes et octets) ; si « truncated » est vrai, "
    "affine la requête (agrège ou filtre) plutôt que de tout re-tirer.\n\n"
    "⛔ INTERDIT — contournement du seuil d'extraction : ne jamais paginer (OFFSET/fragmentation "
    "multi-appels, tranches d'ID, filtres alphabétiques, GROUP_CONCAT ou toute autre technique) "
    "pour dépasser le seuil sans approbation. Si une requête est volumineuse, exécute-la "
    "normalement en UN SEUL appel : le serveur envoie automatiquement une demande d'approbation "
    "Slack et attend la réponse de l'utilisateur avant de renvoyer les données."
)
if TOOL_DIGEST:
    _MYSQL_QUERY_DESC = (
        _MYSQL_QUERY_BASE_DESC
        + "\n\n=== Règles métier essentielles de CETTE base (résumé — NE PAS improviser) ===\n"
        + TOOL_DIGEST
        + "\n\n⚠️ Pour le data model COMPLET (catalogue des tables, colonnes, patterns SQL, codes "
        "détaillés), appelle l'outil `get_data_model_reference` AVANT de composer une requête non "
        "triviale. Ne devine jamais un nom de table/colonne ni une catégorie métier (secteur, "
        "métier, statut…) à partir de données brutes — le référentiel les définit explicitement."
    )
else:
    _MYSQL_QUERY_DESC = _MYSQL_QUERY_BASE_DESC


@mcp.tool(description=_MYSQL_QUERY_DESC)                              # [FASTMCP-API]
def mysql_query(sql: str) -> dict:
    """Exécute un SELECT read-only ; voir _MYSQL_QUERY_DESC pour la description exposée au modèle."""
    subject = _check_subject()
    safe_sql, anon_sql = validate_read_only(sql)
    try:
        result = run_query(safe_sql)
    except pymysql.OperationalError as e:
        # Reconnexion / coupure DB : un retry, sinon erreur MCP propre (le process ne crashe pas).
        log.warning("OperationalError, retry once: %s", e.args[0] if e.args else e)
        result = run_query(safe_sql)

    log.info("query ok | sub=%s | rows=%s | bytes=%s | trunc=%s | sql=%s",
             subject, result["row_count"], result["byte_size"], result["truncated"], anon_sql)

    if _slack_approval_active() and _is_large_result(result):
        request_id = secrets.token_urlsafe(16)
        log.info("large result — slack approval required | request_id=%s | rows=%s | bytes=%s",
                 request_id, result["row_count"], result["byte_size"])
        sent = _send_slack_approval_request(request_id, result["row_count"], result["byte_size"])
        if not sent:
            return {
                "error": "approval_unavailable",
                "message": "Extraction volumineuse mais la notification Slack a échoué. Réessaie.",
            }
        approved = _wait_for_approval(request_id)
        if not approved:
            log.warning("large extract denied or timed out | request_id=%s", request_id)
            return {
                "error": "extraction_denied",
                "message": (
                    f"Extraction refusée ou délai dépassé ({SLACK_APPROVAL_TIMEOUT_S}s). "
                    f"La demande a été envoyée sur Slack — clique sur Approuver pour autoriser."
                ),
                "row_count": result["row_count"],
            }
        log.info("large extract approved | request_id=%s", request_id)

    return result


# Outil de référence : exposé UNIQUEMENT si des instructions sont configurées pour l'instance.
# C'est le canal FIABLE pour livrer le data model complet à tous les clients (la description de cet
# outil + son retour sont toujours accessibles au modèle, même quand le champ `instructions` ne l'est pas).
if SERVER_INSTRUCTIONS:

    @mcp.tool                                                        # [FASTMCP-API]
    def get_data_model_reference() -> str:
        """Renvoie le data model COMPLET et les règles métier de cette base : schéma des tables et
        colonnes, codes/énumérations, conventions, patterns SQL, règles de dédoublonnage. À appeler
        AVANT de composer des requêtes non triviales — il définit les noms exacts et la sémantique
        métier. Ne devine jamais une table/colonne ou une catégorie métier : consulte cette référence."""
        return SERVER_INSTRUCTIONS


# ── Routes hors MCP (non authentifiées WorkOS) ────────────────────────────────

from starlette.requests import Request                              # [FASTMCP-API]
from starlette.responses import JSONResponse, Response             # [FASTMCP-API]


@mcp.custom_route("/health", methods=["GET"])                       # [FASTMCP-API]
async def health(_request: Request) -> Response:
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


@mcp.custom_route("/slack/action", methods=["POST"])               # [FASTMCP-API]
async def slack_action(request: Request) -> Response:
    """Reçoit les actions interactives Slack (boutons Approuver / Refuser)."""
    body = await request.body()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not _verify_slack_signature(body, timestamp, signature):
        log.warning("slack action: invalid signature")
        return JSONResponse({"error": "invalid signature"}, status_code=403)

    params = urllib.parse.parse_qs(body.decode("utf-8"))
    try:
        payload = json.loads(params.get("payload", ["{}"])[0])
        action_id = payload["actions"][0]["action_id"]   # "approve" | "deny"
        request_id = payload["actions"][0]["value"]
        response_url = payload.get("response_url", "")
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        log.warning("slack action: malformed payload (%s)", e)
        return JSONResponse({"error": "malformed payload"}, status_code=400)

    approved = action_id == "approve"
    _approval_results[request_id] = approved
    event = _pending_approvals.get(request_id)
    if event:
        event.set()
    else:
        log.warning("slack action: unknown or expired request_id=%s", request_id)

    # Mettre à jour le message Slack pour confirmer l'action
    if response_url:
        label = "Extraction approuvée." if approved else "Extraction refusée."
        icon = ":white_check_mark:" if approved else ":no_entry:"
        _post_to_url(response_url, {
            "text": f"{icon} {label}",
            "replace_original": True,
        })

    log.info("slack action | request_id=%s | approved=%s", request_id, approved)
    return JSONResponse({"ok": True})


if __name__ == "__main__":
    log.info("Starting %s on %s:%s (base=%s, max_rows=%s, slack_approval=%s)",
             SERVER_NAME, BIND_HOST, MCP_PORT, DB_NAME, MAX_ROWS, _slack_approval_active())
    # Transport Streamable HTTP, stateless. Bind 0.0.0.0 dans le conteneur ; l'exposition publique
    # est limitée par le mapping hôte 127.0.0.1:80xx (compose) + le reverse proxy Apache.
    mcp.run(transport="http", host=BIND_HOST, port=MCP_PORT)        # [FASTMCP-API]

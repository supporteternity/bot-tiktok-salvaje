"""Base de datos SQLite (PLAN-OFICIAL §5): conexión en modo WAL y migraciones por versión."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from bot import config

NOMBRE_DB = "bot.db"

# Cada entrada es una migración; su posición (1, 2, ...) es la versión que deja la base.
MIGRACIONES: list[str] = [
    # v1 — esquema inicial (§5)
    """
    CREATE TABLE reels (
      id              TEXT PRIMARY KEY,
      url             TEXT NOT NULL,
      publicado_en    TEXT,
      activo          INTEGER NOT NULL DEFAULT 1,
      fijado_estado   TEXT NOT NULL DEFAULT 'pendiente',
      ultimo_repaso   TEXT
    );

    CREATE TABLE contactos (
      user_id         TEXT PRIMARY KEY,
      usuario         TEXT NOT NULL,
      nombre          TEXT,
      contactado      INTEGER NOT NULL DEFAULT 0,
      resultado       TEXT,
      contactado_en   TEXT,
      es_equipo       INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_contactos_usuario ON contactos(usuario);

    CREATE TABLE comentarios (
      id              TEXT PRIMARY KEY,
      reel_id         TEXT NOT NULL REFERENCES reels(id),
      user_id         TEXT NOT NULL REFERENCES contactos(user_id),
      texto           TEXT,
      comentado_en    TEXT NOT NULL,
      detectado_en    TEXT NOT NULL,
      estado          TEXT NOT NULL DEFAULT 'en_cola',
      ofensivo        INTEGER NOT NULL DEFAULT 0,
      intentos        INTEGER NOT NULL DEFAULT 0,
      ultimo_error    TEXT,
      actualizado_en  TEXT NOT NULL
    );
    CREATE INDEX idx_comentarios_cola ON comentarios(estado, comentado_en);

    CREATE TABLE acciones (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      tipo            TEXT NOT NULL,
      comentario_id   TEXT,
      reel_id         TEXT,
      texto_id        INTEGER,
      resultado       TEXT NOT NULL,
      hecho_en        TEXT NOT NULL,
      duracion_ms     INTEGER
    );
    CREATE INDEX idx_acciones_tiempo ON acciones(hecho_en);

    CREATE TABLE eventos (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      tipo            TEXT NOT NULL,
      detalle         TEXT,
      captura         TEXT,
      creado_en       TEXT NOT NULL
    );

    CREATE TABLE textos (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      tipo            TEXT NOT NULL,
      contenido       TEXT NOT NULL,
      activo          INTEGER NOT NULL DEFAULT 1,
      veces_usado     INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE config (
      clave           TEXT PRIMARY KEY,
      valor           TEXT NOT NULL,
      actualizado_en  TEXT NOT NULL
    );

    CREATE TABLE estado_bot (
      id              INTEGER PRIMARY KEY CHECK (id = 1),
      modo            TEXT NOT NULL,
      nivel_ritmo     INTEGER NOT NULL DEFAULT 1,
      frenado_hasta   TEXT,
      frenos_7d       INTEGER NOT NULL DEFAULT 0,
      ultimo_latido   TEXT NOT NULL,
      mensaje         TEXT
    );
    """,
    # v2 — cupo aparte para quien sigue la cuenta (25 nov)
    """
    ALTER TABLE contactos ADD COLUMN te_sigue INTEGER;          -- 1 sigue la cuenta, 0 no, NULL sin mirar
    ALTER TABLE contactos ADD COLUMN sigue_mirado_en TEXT;      -- cuándo se miró en su perfil
    ALTER TABLE acciones ADD COLUMN seguidor INTEGER NOT NULL DEFAULT 0;  -- mensaje gastado del cupo de seguidores
    """,
    # v3 — instructivo del cliente (§17): etapas, conversación, leads, seguidores, palabra clave y «me gusta»
    """
    ALTER TABLE contactos ADD COLUMN etapa TEXT NOT NULL DEFAULT 'A';   -- A nuevo · B tiene la info · C dio número · D el equipo habló
    ALTER TABLE contactos ADD COLUMN telefono TEXT;
    ALTER TABLE contactos ADD COLUMN humano INTEGER NOT NULL DEFAULT 0; -- la atiende una persona: el bot no contesta en el chat
    ALTER TABLE contactos ADD COLUMN humano_motivo TEXT;
    ALTER TABLE contactos ADD COLUMN respuestas_auto INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE contactos ADD COLUMN ultimo_recordatorio_en TEXT;
    ALTER TABLE contactos ADD COLUMN origen TEXT;                       -- comentario | mensaje | seguidor
    ALTER TABLE contactos ADD COLUMN reel_origen TEXT;
    UPDATE contactos SET etapa = 'B' WHERE contactado = 1 AND resultado IN ('dm_enviado', 'dudoso');

    ALTER TABLE reels ADD COLUMN descripcion TEXT;
    ALTER TABLE reels ADD COLUMN palabra_clave TEXT;

    ALTER TABLE comentarios ADD COLUMN likes INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE comentarios ADD COLUMN trae_palabra INTEGER NOT NULL DEFAULT 0;

    CREATE TABLE conversaciones (
      conv_id         TEXT PRIMARY KEY,
      usuario         TEXT,
      vista_previa    TEXT,
      ultimo_visto    TEXT,          -- firma del último mensaje ya procesado
      revisada_en     TEXT
    );

    CREATE TABLE mensajes (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id         TEXT NOT NULL,
      de              TEXT NOT NULL,  -- persona | bot
      texto           TEXT NOT NULL,
      clase           TEXT,           -- telefono | humano | duda | interes | otro
      creado_en       TEXT NOT NULL
    );
    CREATE INDEX idx_mensajes_persona ON mensajes(user_id, id);

    -- Mensajes privados que no salen de un comentario: respuestas en la conversación, seguimiento y bienvenida.
    CREATE TABLE envios (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id         TEXT NOT NULL,
      usuario         TEXT NOT NULL,
      tipo            TEXT NOT NULL,  -- dm | confirmar_numero | pedir_numero | animo | seguimiento | bienvenida
      estado          TEXT NOT NULL DEFAULT 'pendiente',  -- pendiente | enviando | enviado | cerrado | dudoso | error_final | cancelado
      intentos        INTEGER NOT NULL DEFAULT 0,
      ultimo_error    TEXT,
      creado_en       TEXT NOT NULL,
      listo_desde     TEXT NOT NULL,
      actualizado_en  TEXT NOT NULL
    );
    CREATE INDEX idx_envios_estado ON envios(estado, listo_desde);

    CREATE TABLE leads (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id         TEXT NOT NULL,
      usuario         TEXT NOT NULL,
      nombre          TEXT,
      telefono        TEXT NOT NULL,
      reel_id         TEXT,
      palabra_clave   TEXT,
      creado_en       TEXT NOT NULL,
      correo_en       TEXT,
      webhook_en      TEXT,
      webhook_error   TEXT,
      hablado_en      TEXT,
      vendedor        TEXT
    );
    CREATE INDEX idx_leads_persona ON leads(user_id);

    CREATE TABLE seguidores (
      usuario         TEXT PRIMARY KEY,
      visto_en        TEXT NOT NULL,
      inicial         INTEGER NOT NULL DEFAULT 0  -- ya seguía al encender: sin bienvenida
    );
    """,
    """
    -- Botón «Comentar en público ahora» del Registro (27 nov): el comentario al que se contesta a mano.
    ALTER TABLE envios ADD COLUMN comentario_id TEXT;
    """,
]
VERSION_ESQUEMA = len(MIGRACIONES)

# Índices solo para ir más rápido (27 nov, medido con un año de historial: CSV del Registro 170 s, Reels 3 s).
# No cambian datos ni la versión del esquema: una versión anterior del bot sigue abriendo la base igual.
INDICES = """
CREATE INDEX IF NOT EXISTS idx_comentarios_fecha ON comentarios(comentado_en);
CREATE INDEX IF NOT EXISTS idx_comentarios_reel ON comentarios(reel_id);
CREATE INDEX IF NOT EXISTS idx_comentarios_persona ON comentarios(user_id, reel_id);
CREATE INDEX IF NOT EXISTS idx_envios_persona ON envios(user_id, tipo);
"""
# Este necesita la columna `envios.comentario_id` (migración 4): se crea después de migrar.
INDICES_V4 = "CREATE INDEX IF NOT EXISTS idx_envios_comentario ON envios(comentario_id);"


def ruta_db() -> Path:
    return config.carpeta_datos() / NOMBRE_DB


def conectar(ruta: Path | None = None, hilos: bool = False) -> sqlite3.Connection:
    """`hilos=True` para el panel (FastAPI puede usar la conexión desde otro hilo dentro de la misma petición)."""
    conn = sqlite3.connect(ruta or ruta_db(), timeout=10, check_same_thread=not hilos)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


@contextmanager
def junto(conn: sqlite3.Connection):
    """Varios cambios que van juntos: se guardan todos o ninguno (auditoría 26 nov, hallazgo 20). Si ya hay un
    grupo abierto, estos cambios entran en él y se guardan al cerrarse el de fuera (un `with conn:` dentro de otro
    guardaba a mitad)."""
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def migrar(conn: sqlite3.Connection) -> int:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > VERSION_ESQUEMA:
        raise RuntimeError(f"La base es de una versión más nueva ({version}) que este programa ({VERSION_ESQUEMA}).")
    for numero in range(version + 1, VERSION_ESQUEMA + 1):
        # executescript hace COMMIT antes; envolvemos en una transacción explícita.
        conn.executescript(f"BEGIN;\n{MIGRACIONES[numero - 1]}\nPRAGMA user_version = {numero};\nCOMMIT;")
    conn.executescript(f"BEGIN;\n{INDICES}\n{INDICES_V4}\nCOMMIT;")
    return VERSION_ESQUEMA


def preparar(sembrar: bool = True) -> sqlite3.Connection:
    """Crea la carpeta de datos y bot.db si faltan, migra y rellena la config por defecto."""
    carpeta = config.carpeta_datos(crear=True)
    conn = conectar(carpeta / NOMBRE_DB)
    migrar(conn)
    config.inicializar(conn, carpeta)
    if sembrar:
        from bot import textos

        textos.sembrar(conn)  # D43: textos del instructivo en los tipos vacíos
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO estado_bot (id, modo, nivel_ritmo, ultimo_latido, mensaje) VALUES (1, 'activo', ?, ?, ?)",
            (config.leer(conn, "nivel_ritmo"), datetime.now(UTC).isoformat(timespec="seconds"), "Arrancando"),
        )
    return conn

"""Detector (PLAN-OFICIAL §3, tareas C1 y C2): comentarios nuevos → cola, sin duplicados.

Cada `leer(ahora)`:
1. TikTok Studio (C1): del más nuevo hacia atrás hasta llegar a uno ya guardado.
2. Repaso (C2): si a algún reel activo le toca (`repaso_reel_cada_h` horas), se lee TODA la lista de Studio
   sin parar en lo ya conocido, y se marcan como repasados todos los reels activos. Recupera lo que la
   lectura rápida no enseñó. (Antes se hacía en la página de cada vídeo, pero abrir sus comentarios hace
   salir un captcha: 25 nov.)

Guardar (`guardar`) no toca TikTok: reels, contactos y comentarios con INSERT OR IGNORE. Lo comentado antes
de `inicio_desde` entra como `historico` (D10); lo demás, `en_cola`. Las reglas (propio, ya contactado,
ofensivo…) se aplican al atender, no aquí.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from bot import config, conversacion, reglas
from bot.pantallas import studio_comentarios
from bot.pantallas.reel import ComentarioLeido
from bot.ritmo import iso
from bot.selectores import url_reel

log = logging.getLogger("bot.detector")


def _fecha(texto: str) -> datetime:
    dt = datetime.fromisoformat(texto)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _contacto(conn: sqlite3.Connection, c: ComentarioLeido) -> str:
    """user_id con el que se guarda a la persona: el ID interno estable si viene; si no, 'u:'+usuario (§5).
    Si ya estaba como 'u:usuario' y ahora llega su ID, se pasa al ID conservando todo lo suyo (reglas.unir_contactos)."""
    provisional = f"u:{c.usuario}"
    if not c.user_id:
        conn.execute("INSERT OR IGNORE INTO contactos (user_id, usuario, nombre) VALUES (?, ?, ?)",
                     (provisional, c.usuario, c.nombre))
        return provisional

    if conn.execute("SELECT 1 FROM contactos WHERE user_id = ?", (c.user_id,)).fetchone():
        conn.execute("UPDATE contactos SET usuario = ?, nombre = COALESCE(?, nombre) WHERE user_id = ?",
                     (c.usuario, c.nombre, c.user_id))  # puede haber cambiado de @usuario
    else:
        conn.execute("INSERT INTO contactos (user_id, usuario, nombre) VALUES (?, ?, ?)", (c.user_id, c.usuario, c.nombre))

    reglas.unir_contactos(conn, provisional, c.user_id)  # todo: etapa, número, leads, mensajes… (auditoría 26 nov)
    return c.user_id


def guardar(conn: sqlite3.Connection, comentarios: Iterable[ComentarioLeido], ahora: datetime,
            publicado: dict[str, datetime] | None = None) -> int:
    """Guarda lo leído. Devuelve cuántos comentarios eran nuevos."""
    cuenta = config.leer(conn, "cuenta") or ""
    inicio = config.leer(conn, "inicio_desde")
    inicio_desde = _fecha(inicio) if inicio else None
    nuevos = 0
    comentarios_lista = list(comentarios)
    with conn:
        for c in comentarios_lista:
            conn.execute("INSERT OR IGNORE INTO reels (id, url) VALUES (?, ?)", (c.reel_id, url_reel(cuenta, c.reel_id)))
            user_id = _contacto(conn, c)
            estado = "historico" if inicio_desde and c.comentado_en < inicio_desde else "en_cola"
            cursor = conn.execute(
                "INSERT OR IGNORE INTO comentarios (id, reel_id, user_id, texto, comentado_en, detectado_en, estado, "
                "actualizado_en) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (c.id, c.reel_id, user_id, c.texto, iso(c.comentado_en), iso(ahora), estado, iso(ahora)),
            )
            nuevos += cursor.rowcount
            if not cursor.rowcount:  # ya guardado: solo se actualizan los «me gusta» (D42)
                conn.execute("UPDATE comentarios SET likes = ? WHERE id = ? AND likes != ?", (c.likes, c.id, c.likes))
            else:
                conn.execute("UPDATE comentarios SET likes = ? WHERE id = ?", (c.likes, c.id))
        publicado = dict(publicado or {})
        for c in comentarios_lista:
            if c.reel_publicado is not None:
                publicado.setdefault(c.reel_id, c.reel_publicado)
        for reel_id, fecha in publicado.items():
            conn.execute("UPDATE reels SET publicado_en = ? WHERE id = ? AND publicado_en IS NULL", (iso(fecha), reel_id))
        # D41: palabra clave del texto del video (solo si nadie la puso a mano) y si cada comentario la trae.
        for c in comentarios_lista:
            if c.reel_descripcion:
                conn.execute("UPDATE reels SET descripcion = ?, palabra_clave = COALESCE(palabra_clave, ?) WHERE id = ?",
                             (c.reel_descripcion, conversacion.palabra_de_descripcion(c.reel_descripcion), c.reel_id))
        for c in comentarios_lista:
            fila = conn.execute("SELECT palabra_clave FROM reels WHERE id = ?", (c.reel_id,)).fetchone()
            if fila and fila[0]:
                conn.execute("UPDATE comentarios SET trae_palabra = ? WHERE id = ?",
                             (int(conversacion.trae_palabra(c.texto, fila[0])), c.id))
    return nuevos


def reel_para_repasar(conn: sqlite3.Connection, ahora: datetime) -> str | None:
    """El reel activo que más tiempo lleva sin repaso completo, si ya le toca."""
    cfg = config.leer_todo(conn)
    limite = iso(ahora - timedelta(hours=cfg["repaso_reel_cada_h"]))
    desde = iso(ahora - timedelta(days=cfg["reels_activos_dias"]))
    fila = conn.execute(
        "SELECT id FROM reels WHERE activo = 1 AND (publicado_en IS NULL OR publicado_en >= ?) "
        "AND (ultimo_repaso IS NULL OR ultimo_repaso <= ?) ORDER BY ultimo_repaso IS NOT NULL, ultimo_repaso LIMIT 1",
        (desde, limite),
    ).fetchone()
    return fila["id"] if fila else None


class DetectorTikTok:
    """Implementación real del contrato `Detector` usando el contexto de PantallasTikTok."""

    def __init__(self, conn: sqlite3.Connection, pantallas):
        self.conn, self.pantallas = conn, pantallas

    def _conocido(self, cid: str) -> bool:
        return self.conn.execute("SELECT 1 FROM comentarios WHERE id = ?", (cid,)).fetchone() is not None

    def leer(self, ahora: datetime) -> int:
        """Lectura rápida de Studio; si toca repaso (o aún no hay reels), una sola lectura completa en su lugar."""
        ctx = self.pantallas.contexto()
        hay_reels = self.conn.execute("SELECT 1 FROM reels WHERE activo = 1 LIMIT 1").fetchone() is not None
        if hay_reels and reel_para_repasar(self.conn, ahora) is None:
            return guardar(self.conn, studio_comentarios.leer_nuevos(ctx, self._conocido), ahora)

        leidos = studio_comentarios.leer_nuevos(ctx, lambda _cid: False, studio_comentarios.MAX_REPASO)
        nuevos = guardar(self.conn, leidos, ahora)
        with self.conn:
            self.conn.execute("UPDATE reels SET ultimo_repaso = ? WHERE activo = 1", (iso(ahora),))
        log.info("Repaso completo de Studio: %s comentarios nuevos", nuevos)
        return nuevos

"""Lector de la bandeja y de seguidores nuevos (PLAN-OFICIAL §17, D37 y D40). Lee TikTok y decide; los mensajes
se mandan después, uno a uno y con el ritmo de siempre, desde la tabla `envios` (los manda el ejecutor).

Cada `leer(ahora)`:
1. Lista de chats (y solicitudes). La primera vez solo apunta cómo está cada uno: lo de antes de encender el bot
   no se contesta.
2. Abre los chats cuya vista previa cambió (como mucho `MAX_POR_VUELTA`), lee lo NUEVO que escribió la persona y
   aplica `conversacion.decidir`: número → lead; pregunta difícil → a una persona; si toca, una respuesta en
   `envios`.
"""

from __future__ import annotations

import json
import logging
import random
import sqlite3
from datetime import datetime

from bot import config, conversacion, leads, reglas
from bot.pantallas import ChatEnLista, ChatLeido, MensajeChat, Pantallas
from bot.ritmo import iso

log = logging.getLogger("bot.bandeja")

MAX_POR_VUELTA = 5
SIN_HISTORIAL = 5  # chat que nunca habíamos mirado y sin mensajes nuestros: solo se miran los últimos
EN_MARCHA = ("pendiente", "enviando")
DE_CONVERSACION = ("dm", "confirmar_numero", "pedir_numero", "animo")


def firma(m: MensajeChat) -> str:
    return json.dumps([m.propio, m.texto], ensure_ascii=False)


def nuevos_de_la_persona(mensajes: tuple[MensajeChat, ...], ultimo_visto: str | None) -> list[str]:
    """Textos que escribió la persona después de lo último ya procesado. Si no se sabe qué fue lo último
    (chat nuevo para el bot), lo que escribió después de nuestro último mensaje."""
    firmas = [firma(m) for m in mensajes]
    if ultimo_visto and ultimo_visto in firmas:
        resto = mensajes[len(firmas) - 1 - firmas[::-1].index(ultimo_visto) + 1:]
    else:
        propios = [i for i, m in enumerate(mensajes) if m.propio]
        resto = mensajes[propios[-1] + 1:] if propios else mensajes[-SIN_HISTORIAL:]
    return [m.texto for m in resto if not m.propio and m.texto]


class LectorBandeja:
    def __init__(self, conn: sqlite3.Connection, pantallas: Pantallas, rng: random.Random | None = None):
        self.conn, self.pantallas, self.rng = conn, pantallas, rng or random.Random()

    # ------------------------------------------------------------ bandeja

    def leer(self, ahora: datetime) -> int:
        """Devuelve cuántos chats abrió."""
        cfg = config.leer_todo(self.conn)
        chats = self.pantallas.leer_bandeja()
        guardados = {f["conv_id"]: f for f in self.conn.execute("SELECT * FROM conversaciones")}
        if not guardados:  # primera vez: se apunta cómo está todo y no se contesta lo de antes
            with self.conn:
                self.conn.executemany(
                    "INSERT OR IGNORE INTO conversaciones (conv_id, vista_previa, revisada_en) VALUES (?, ?, ?)",
                    [(c.conv_id, c.vista_previa, iso(ahora)) for c in chats])
                if not chats:  # bandeja vacía: una marca para que la próxima vez no se tome como «primera vez»
                    self.conn.execute("INSERT OR IGNORE INTO conversaciones (conv_id, revisada_en) VALUES ('-', ?)",
                                      (iso(ahora),))
            log.info("Bandeja: primera lectura, %s chats apuntados sin contestar", len(chats))
            return 0

        cambiados = [c for c in chats if c.conv_id not in guardados or guardados[c.conv_id]["vista_previa"] != c.vista_previa]
        abiertos = 0
        for chat in cambiados[:MAX_POR_VUELTA]:
            guardado = guardados.get(chat.conv_id)
            leido = self.pantallas.leer_chat(chat, aceptar=not cfg["modo_prueba"])
            abiertos += 1
            if leido.aceptada:
                with self.conn:
                    self.conn.execute("INSERT INTO acciones (tipo, resultado, hecho_en) VALUES ('aceptar', 'ok', ?)",
                                      (iso(ahora),))
            usuario = self._usuario_del_chat(chat, leido, guardado)
            if not usuario:
                # Sin saber quién es no se contesta, y tampoco se marca como visto: se vuelve a mirar en la próxima
                # lectura (antes se daba por visto y sus mensajes se perdían; 26 nov).
                log.warning("Chat %s: no se sabe de quién es (cabecera vacía); se mira otra vez luego", chat.conv_id)
                continue
            leido = ChatLeido(usuario=usuario, mensajes=leido.mensajes, aceptada=leido.aceptada)
            self._procesar(chat, leido, guardado["ultimo_visto"] if guardado else None, cfg, ahora)
            with self.conn:
                self.conn.execute(
                    "INSERT INTO conversaciones (conv_id, usuario, vista_previa, ultimo_visto, revisada_en) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(conv_id) DO UPDATE SET usuario = COALESCE(excluded.usuario, "
                    "usuario), vista_previa = excluded.vista_previa, ultimo_visto = COALESCE(excluded.ultimo_visto, "
                    "ultimo_visto), revisada_en = excluded.revisada_en",
                    (chat.conv_id, leido.usuario, chat.vista_previa,
                     firma(leido.mensajes[-1]) if leido.mensajes else None, iso(ahora)))
        return abiertos

    def _usuario_del_chat(self, chat: ChatEnLista, leido: ChatLeido, guardado: sqlite3.Row | None) -> str | None:
        """@ de la persona del chat. TikTok web a veces deja la cabecera vacía («@», visto el 26 nov): entonces se
        busca por los IDs internos que trae el propio chat (0:1:<id>:<id>) entre las personas ya conocidas, o se usa
        el @ apuntado la última vez que se vio ese chat."""
        if leido.usuario:
            return leido.usuario
        ids = [p for p in chat.conv_id.split(":")[2:] if p.isdigit()]
        if ids:
            fila = self.conn.execute(
                f"SELECT usuario FROM contactos WHERE user_id IN ({','.join('?' * len(ids))}) LIMIT 1", ids).fetchone()
            if fila:
                return fila["usuario"]
        return guardado["usuario"] if guardado and guardado["usuario"] else None

    def _contacto(self, usuario: str, nombre: str, ahora: datetime, origen: str = "mensaje") -> sqlite3.Row:
        fila = self.conn.execute("SELECT * FROM contactos WHERE usuario = ? COLLATE NOCASE ORDER BY contactado DESC "
                                 "LIMIT 1", (usuario,)).fetchone()
        if fila is None:
            with self.conn:
                self.conn.execute("INSERT OR IGNORE INTO contactos (user_id, usuario, nombre, origen) "
                                  "VALUES (?, ?, ?, ?)", (f"u:{usuario}", usuario, nombre or None, origen))
            fila = self.conn.execute("SELECT * FROM contactos WHERE user_id = ?", (f"u:{usuario}",)).fetchone()
        return fila

    def _procesar(self, chat: ChatEnLista, leido: ChatLeido, ultimo_visto: str | None, cfg: dict,
                  ahora: datetime) -> None:
        if reglas.es_propio(leido.usuario, cfg["cuenta"], cfg["cuentas_equipo"]):
            return
        nuevos = nuevos_de_la_persona(leido.mensajes, ultimo_visto)
        if not nuevos:
            return
        k = self._contacto(leido.usuario, chat.nombre, ahora)
        uid = k["user_id"]
        ya = {t for (t,) in self.conn.execute("SELECT DISTINCT tipo FROM envios WHERE user_id = ? AND estado NOT IN "
                                               "('cancelado', 'error_final', 'simulado_repuesto')", (uid,))}
        d = conversacion.decidir(k["etapa"], bool(k["humano"]), k["respuestas_auto"], ya, k["telefono"], nuevos, cfg)
        with self.conn:
            for texto in nuevos:
                clase = conversacion.clasificar(texto, cfg["palabras_humano"], cfg["palabras_interes"])
                self.conn.execute("INSERT INTO mensajes (user_id, de, texto, clase, creado_en) VALUES (?, 'persona', ?, ?, ?)",
                                  (uid, texto[:2000], clase, iso(ahora)))

        if d.telefono:
            leads.registrar(self.conn, uid, d.telefono, ahora)
        if d.humano:
            with self.conn:
                self.conn.execute("UPDATE contactos SET humano = 1, humano_motivo = ? WHERE user_id = ?", (d.humano, uid))
            if not d.telefono:  # quien dejó su número ya va en el correo del lead
                leads.avisar_equipo(self.conn, uid, d.humano, ahora)
        if d.respuesta:
            en_marcha = self.conn.execute(
                f"SELECT 1 FROM envios WHERE user_id = ? AND estado IN ('pendiente', 'enviando') AND tipo IN "
                f"({','.join('?' * len(DE_CONVERSACION))}) LIMIT 1", (uid, *DE_CONVERSACION)).fetchone()
            if en_marcha and d.respuesta != "confirmar_numero":
                return  # ya hay una respuesta esperando turno: no se encadenan dos
            with self.conn:
                if d.respuesta == "confirmar_numero":  # lo demás que esperaba ya no hace falta
                    self.conn.execute("UPDATE envios SET estado = 'cancelado', actualizado_en = ? WHERE user_id = ? "
                                      "AND estado = 'pendiente' AND tipo IN ('pedir_numero', 'animo')", (iso(ahora), uid))
                self.conn.execute("INSERT INTO envios (user_id, usuario, tipo, creado_en, listo_desde, actualizado_en) "
                                  "VALUES (?, ?, ?, ?, ?, ?)", (uid, k["usuario"], d.respuesta, iso(ahora), iso(ahora), iso(ahora)))
                if d.respuesta != "confirmar_numero":
                    self.conn.execute("UPDATE contactos SET respuestas_auto = respuestas_auto + 1 WHERE user_id = ?", (uid,))

    # ------------------------------------------------------------ seguidores nuevos (D40)

    def leer_seguidores(self, ahora: datetime) -> int:
        """Devuelve cuántas bienvenidas dejó en cola."""
        cfg = config.leer_todo(self.conn)
        usuarios = self.pantallas.seguidores_nuevos()
        primera = self.conn.execute("SELECT 1 FROM seguidores LIMIT 1").fetchone() is None
        if primera:
            with self.conn:
                self.conn.executemany("INSERT OR IGNORE INTO seguidores (usuario, visto_en, inicial) VALUES (?, ?, 1)",
                                      [(u, iso(ahora)) for u in usuarios] or [("-", iso(ahora))])
            log.info("Seguidores: primera lectura, %s apuntados sin bienvenida", len(usuarios))
            return 0
        conocidos = {u.casefold() for (u,) in self.conn.execute("SELECT usuario FROM seguidores")}
        en_cola = 0
        for usuario in usuarios:
            if usuario.casefold() in conocidos or reglas.es_propio(usuario, cfg["cuenta"], cfg["cuentas_equipo"]):
                continue
            with self.conn:
                self.conn.execute("INSERT OR IGNORE INTO seguidores (usuario, visto_en) VALUES (?, ?)", (usuario, iso(ahora)))
            k = self._contacto(usuario, "", ahora, origen="seguidor")
            if k["contactado"] or k["etapa"] != "A":
                continue  # ya recibió la info (p. ej. por un comentario)
            if not cfg["enlace_youtube"]:
                log.info("Seguidor nuevo @%s sin bienvenida: falta el enlace de YouTube en Ajustes", usuario)
                continue
            with self.conn:
                self.conn.execute("UPDATE contactos SET origen = COALESCE(origen, 'seguidor'), te_sigue = 1 "
                                  "WHERE user_id = ?", (k["user_id"],))
                self.conn.execute("INSERT INTO envios (user_id, usuario, tipo, creado_en, listo_desde, actualizado_en) "
                                  "VALUES (?, ?, 'bienvenida', ?, ?, ?)",
                                  (k["user_id"], usuario, iso(ahora), iso(ahora), iso(ahora)))
            en_cola += 1
        return en_cola

"""Ejecutor: máquina de estados de un comentario (PLAN-OFICIAL §1, §6).

Cada llamada a `paso(ahora)` hace COMO MUCHO UNA acción de escritura en TikTok, en este orden:
1. Recuperar un comentario que se quedó en `dm_enviando` (corte a mitad de envío): nunca reenvía a ciegas.
2. Pasar a `en_cola` los `error` cuya espera de reintento (10/30/90 min) ya pasó.
3. §17: mensajes privados de la tabla `envios` (respuestas en la conversación, seguimiento, bienvenida). Las
   respuestas en la conversación van primero: el instructivo pide contestar «de inmediato».
4. Respuestas públicas pendientes (`resp_pendiente`, `alt_pendiente`, `rec_pendiente`), si el ritmo deja.
5. El siguiente comentario en cola (reglas.siguiente): mensaje privado, o en `solo_publicas` pasa a `alt_pendiente`.
   Antes de mandar el enlace se mira el chat (D36): si ya hay mensajes nuestros, no se manda otra vez.
"""

from __future__ import annotations

import logging
import random
import sqlite3
import time as reloj
from dataclasses import dataclass
from datetime import datetime, timedelta

from bot import config, db, redaccion, reglas, textos
from bot.freno import Alerta, Freno, Resultado
from bot.pantallas import AvisoTikTok, InfoPerfil, Pantallas
from bot.ritmo import _fecha, iso

log = logging.getLogger("bot.ejecutor")

# Con el cupo general lleno, a quien se miró y no sigue la cuenta no se le vuelve a mirar en este tiempo.
VOLVER_A_MIRAR = timedelta(hours=12)
ESPERAS_REINTENTO = (timedelta(minutes=10), timedelta(minutes=30), timedelta(minutes=90))
MAX_REINTENTOS = len(ESPERAS_REINTENTO)
# Resultado de la persona cuando TikTok no dejó que le llegara el mensaje privado (D47). No es CON_INFO: sigue en
# la etapa A y, si escribe, recibe el enlace.
NO_ENTREGADO = "dm_no_entregado"
# Mensaje privado pedido a mano desde el Registro («Enviar mensaje privado ahora»): el texto «dm» de siempre.
A_MANO = "dm_manual"
PUBLICA_A_MANO = "publica_manual"  # botón «Comentar en público ahora» del Registro (27 nov)


@dataclass(frozen=True)
class Paso:
    trabajo: bool  # se avanzó algo (estado o acción)
    espera_hasta: datetime | None = None  # si no se pudo actuar por ritmo, cuándo volver
    freno: Resultado | None = None  # si saltó el freno (aviso de TikTok o errores repetidos)
    problema: str | None = None  # algo que necesita a una persona (p. ej. no hay textos)
    comentario_id: str | None = None


class Ejecutor:
    def __init__(self, conn: sqlite3.Connection, pantallas: Pantallas, ritmo, freno: Freno, rng: random.Random | None = None):
        self.conn = conn
        self.pantallas = pantallas
        self.ritmo = ritmo
        self.freno = freno
        self.rng = rng or random.Random()
        self._inicio = reloj.monotonic()  # inicio del paso en curso: cada acción guarda cuánto tardó (27 nov)

    def _ms(self) -> int:
        return max(1, round((reloj.monotonic() - self._inicio) * 1000))

    # ------------------------------------------------------------ utilidades de base

    def _comentario(self, cid: str) -> sqlite3.Row:
        return self.conn.execute(
            "SELECT c.*, p.usuario, p.nombre, p.resultado AS resultado_contacto, p.contactado, p.etapa "
            "FROM comentarios c JOIN contactos p ON p.user_id = c.user_id WHERE c.id = ?",
            (cid,),
        ).fetchone()

    def _estado(self, cid: str, estado: str, ahora: datetime, **extra) -> None:
        if estado in ("dm_enviado", "completado", "completado_cerrado", "completado_publica", "completado_recordatorio",
                      "saltado_contactado"):
            extra.setdefault("ultimo_error", None)  # salió bien: el panel no debe enseñar un error viejo
        campos = ", ".join(f"{k} = ?" for k in extra)
        sql = f"UPDATE comentarios SET estado = ?, actualizado_en = ?{', ' + campos if campos else ''} WHERE id = ?"
        with db.junto(self.conn):
            self.conn.execute(sql, (estado, iso(ahora), *extra.values(), cid))

    def _accion(self, tipo: str, c: sqlite3.Row, resultado: str, ahora: datetime, texto_id: int | None = None,
                seguidor: bool = False) -> None:
        with db.junto(self.conn):
            self.conn.execute(
                "INSERT INTO acciones (tipo, comentario_id, reel_id, texto_id, resultado, hecho_en, seguidor, duracion_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (tipo, c["id"], c["reel_id"], texto_id, resultado, iso(ahora), int(seguidor), self._ms()),
            )

    def _hay_textos(self, tipo: str) -> bool:
        return self.conn.execute("SELECT 1 FROM textos WHERE tipo = ? AND activo = 1 LIMIT 1", (tipo,)).fetchone() is not None

    @staticmethod
    def _listo(c: sqlite3.Row, ahora: datetime) -> bool:
        """¿Ya pasó la espera de reintento? (sin intentos fallidos, siempre listo)"""
        if c["intentos"] == 0:
            return True
        espera = ESPERAS_REINTENTO[min(c["intentos"], MAX_REINTENTOS) - 1]
        return ahora >= _fecha(c["actualizado_en"]) + espera

    # ------------------------------------------------------------ paso principal

    def paso(self, ahora: datetime) -> Paso:
        self._inicio = reloj.monotonic()
        cfg = config.leer_todo(self.conn)
        self._reparar_a_medias(cfg, ahora)

        # 1. recuperación tras un corte en dm_enviando
        fila = self.conn.execute("SELECT id FROM comentarios WHERE estado = 'dm_enviando' ORDER BY comentado_en LIMIT 1").fetchone()
        if fila:
            return self._recuperar(self._comentario(fila["id"]), cfg, ahora)

        # 2. errores cuya espera ya pasó → vuelven a la cola
        for f in self.conn.execute("SELECT id FROM comentarios WHERE estado = 'error'").fetchall():
            c = self._comentario(f["id"])
            if self._listo(c, ahora):
                self._estado(c["id"], "en_cola", ahora)

        esperas: list[datetime] = []

        # 3. mensajes privados fuera de comentarios (§17)
        hecho = self._envios(ahora, esperas)
        if hecho is not None:
            return hecho

        # 4. respuestas públicas pendientes
        pendientes = self.conn.execute(
            "SELECT id FROM comentarios WHERE estado IN ('resp_pendiente', 'alt_pendiente', 'rec_pendiente') "
            "ORDER BY comentado_en, id"
        ).fetchall()
        for f in pendientes:
            c = self._comentario(f["id"])
            if not self._listo(c, ahora):
                continue
            if c["estado"] == "rec_pendiente":
                tipo = reglas.tipo_recordatorio(c["etapa"], c["resultado_contacto"])
            else:
                tipo = "respuesta" if c["estado"] == "resp_pendiente" else "respuesta_alt"
            decision = self.ritmo.puede(textos.ACCION_DE_TIPO[tipo], ahora)
            if decision.ok:
                return self._responder(c, tipo, cfg, ahora)
            if decision.hasta:
                esperas.append(decision.hasta)
            break  # el resto de pendientes tampoco puede ahora

        # 5. siguiente comentario en cola
        contar_rec = "SELECT COUNT(*) FROM comentarios WHERE estado = 'rec_pendiente'"
        recordatorios = self.conn.execute(contar_rec).fetchone()[0]
        cid = reglas.siguiente(self.conn, ahora)
        if not cid and self.conn.execute(contar_rec).fetchone()[0] > recordatorios:
            return Paso(True)  # D35: alguien que vuelve a comentar quedó esperando su respuesta pública
        if cid:
            c = self._comentario(cid)
            if cfg["modo_publica"] == "solo_publicas":
                with db.junto(self.conn):
                    reglas.marcar_contactado(self.conn, c["user_id"], "publica", ahora)
                    self._estado(cid, "alt_pendiente", ahora)
                return Paso(True, comentario_id=cid)
            if reglas.privados_en_pausa(self.conn, ahora):
                return self._sin_privado(c, ahora)
            decision = self.ritmo.puede("dm", ahora)
            general, seguidores = self.ritmo.cupo_dm(ahora, False), self.ritmo.cupo_dm(ahora, True)
            if decision.ok and general.ok:
                return self._dm(c, cfg, ahora)
            if decision.ok and seguidores.ok:
                # Cupo general lleno: se sigue escribiendo a quien sigue la cuenta. Los demás no pierden su sitio.
                otro = reglas.siguiente(self.conn, ahora, sin_mirar_desde=ahora - VOLVER_A_MIRAR)
                if otro:
                    return self._dm(self._comentario(otro), cfg, ahora, solo_seguidores=True)
                esperas.append(general.hasta)
            elif decision.ok:
                esperas.append(min(general.hasta, seguidores.hasta))
            elif decision.hasta:
                esperas.append(decision.hasta)

        return Paso(False, espera_hasta=min(esperas) if esperas else None)

    # ------------------------------------------------------------ mensaje privado

    def _dm(self, c: sqlite3.Row, cfg: dict, ahora: datetime, solo_seguidores: bool = False) -> Paso:
        cid = c["id"]
        if not self._hay_textos("dm"):
            return Paso(False, problema="No hay textos activos de mensaje privado: escríbelos en el panel.", comentario_id=cid)
        try:
            perfil = self.pantallas.abrir_perfil(c["usuario"])
            # Cada perfil abierto lleva su pausa aunque no se escriba nada (no gasta tope: no va a `acciones`).
            # Sin ella, con cuentas cerradas o inexistentes seguidas, abría un perfil por segundo (auditoría 26 nov).
            if not perfil.existe:
                self.ritmo.hecho("perfil", ahora)
                self._estado(cid, "saltado_no_disponible", ahora, ultimo_error="la cuenta no existe")
                return Paso(True, comentario_id=cid)

            c = self._unificar_contacto(c, perfil)
            otra_vez = bool(c["contactado"]) and reglas.reel_nuevo(self.conn, c, ahora)  # D49: comenta en otro reel
            if c["contactado"] and not otra_vez:
                self.ritmo.hecho("perfil", ahora)
                self._estado(cid, "saltado_contactado", ahora)
                return Paso(True, comentario_id=cid)

            self._apuntar_si_sigue(c, perfil, ahora)
            if solo_seguidores and perfil.te_sigue is not True:
                # No sigue la cuenta (o no se sabe) y el cupo general está lleno: se queda en la cola, en su sitio.
                self._accion("perfil", c, "no_sigue" if perfil.te_sigue is False else "sin_saber", ahora)
                self.ritmo.hecho("perfil", ahora)
                self.freno.accion_ok()
                return Paso(True, comentario_id=cid)
            seguidor = perfil.te_sigue is True and self.ritmo.cupo_dm(ahora, True).ok

            if not perfil.puede_mensaje:
                return self._cerrado(c, ahora)

            texto_id, texto = textos.preparar(self.conn, "dm", perfil.nombre or c["nombre"], c["reel_id"], self.rng)
            self._estado(cid, "dm_enviando", ahora)  # ANTES de pulsar enviar (§6)
            # D36: si ya tiene el enlace en el chat, no se repite… salvo D49 (otro reel: se le escribe a propósito).
            # A quien nunca le llegó (27 nov) sí se le mira el chat: si ya tiene el enlace, no se repite.
            resultado = self.pantallas.enviar_dm(c["usuario"], texto, simulado=cfg["modo_prueba"],
                                                 solo_si_nuevo=not otra_vez or c["resultado_contacto"] == NO_ENTREGADO)
        except AvisoTikTok as aviso:
            return self._por_aviso(cid, aviso, ahora)
        except (textos.SinTextos, textos.SinLanding) as e:
            return Paso(False, problema=str(e), comentario_id=cid)
        except Exception as e:  # noqa: BLE001 — cualquier fallo técnico
            return self._por_error(cid, e, ahora)

        if resultado == "cerrado":
            return self._cerrado(c, ahora)
        rechazado = resultado == "bloqueado"  # 27 nov: TikTok contestó 7195; por lo demás, igual que «no llegó»
        if rechazado:
            resultado = "dudoso"
            self._apuntar_rechazo(c["usuario"], ahora)

        self.freno.accion_ok()
        if resultado == "ya_hablado":  # D36: ya tenía mensajes nuestros (ManyChat, el equipo…): sin enlace otra vez
            hay = self._hay_textos("recordatorio") and not c["ofensivo"]
            with db.junto(self.conn):
                self._accion("perfil", c, "ya_hablado", ahora)
                reglas.marcar_contactado(self.conn, c["user_id"], "ya_hablado", ahora, c["reel_id"])
                self._estado(cid, "rec_pendiente" if hay else "completado", ahora, intentos=0)
            self.ritmo.hecho("perfil", ahora)
            return Paso(True, comentario_id=cid)
        if resultado == "dudoso":
            # Pulsó enviar pero el mensaje no quedó en el chat. Visto con TikTok real (26 nov): TikTok rechaza
            # algunos primeros mensajes a desconocidos («no se envió para proteger nuestra comunidad», código 7195).
            # Nunca se reenvía (D46). D47: se le contesta en público («escríbenos…», texto «respuesta_alt») y la
            # persona queda en la etapa A, así que cuando escriba ella, el bot le manda el enlace en esa
            # conversación (si ya hubiera un mensaje nuestro en el chat, no se repite: D36).
            with db.junto(self.conn):
                self._accion("dm", c, "dudoso", ahora, texto_id, seguidor=seguidor)
                reglas.marcar_contactado(self.conn, c["user_id"], NO_ENTREGADO, ahora, c["reel_id"])
                self._estado(cid, self._tras_cerrado(c), ahora, intentos=0,
                             ultimo_error="TikTok no dejó enviar el mensaje privado: se le invita en público a escribir")
            self.ritmo.hecho("dm", ahora)
            return Paso(True, comentario_id=cid, freno=self._si_empieza_pausa(ahora, rechazado))

        with db.junto(self.conn):
            self._accion("dm", c, "simulado" if cfg["modo_prueba"] else "ok", ahora, texto_id, seguidor=seguidor)
            self._dm_enviado(c, cfg, ahora)
        textos.marcar_usado(self.conn, texto_id)
        self.ritmo.hecho("dm", ahora)
        return Paso(True, comentario_id=cid)

    def _apuntar_rechazo(self, usuario: str, ahora: datetime) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO eventos (tipo, detalle, creado_en) VALUES ('privado_rechazado', ?, ?)",
                              (f"TikTok no dejó pasar el privado a @{usuario} (código 7195)", iso(ahora)))

    def _si_empieza_pausa(self, ahora: datetime, rechazado: bool = False) -> Resultado | None:
        """Tras un privado que no llegó: si con él ya van NO_LLEGAN_SEGUIDOS, empieza la pausa de privados y se avisa."""
        hasta = reglas.privados_en_pausa(self.conn, ahora)
        if not hasta:
            return None
        with self.conn:
            motivo = ("TikTok rechazó un privado (código 7195)" if rechazado
                      else f"{reglas.NO_LLEGAN_SEGUIDOS} privados seguidos no llegaron")
            self.conn.execute("INSERT INTO eventos (tipo, detalle, creado_en) VALUES ('pausa_privados', ?, ?)",
                              (f"{motivo}: sin privados hasta {iso(hasta)}", iso(ahora)))
        log.warning("Pausa de privados hasta %s (%s)", iso(hasta), motivo)
        que_paso = ("TikTok rechazó un mensaje privado del bot con el aviso «no se envió para proteger nuestra comunidad» "
                    "(código 7195). " if rechazado else
                    f"TikTok no dejó pasar los últimos {reglas.NO_LLEGAN_SEGUIDOS} mensajes privados. ")
        return Resultado("activo", hasta, Alerta(
            "frenado", redaccion.asunto(redaccion.SOLO_AVISO, "TikTok no deja pasar los mensajes privados: pausa de 24 h"),
            que_paso + "Cuando pasa esto, "
            "TikTok suele seguir bloqueándolos unas horas, y cada intento alarga el bloqueo.\n\n"
            f"Para cuidar la cuenta, el bot no intentará mensajes privados hasta {redaccion.cuando(hasta, ahora)}. "
            "Mientras tanto sigue trabajando: contesta en público a cada comentario invitando a escribir, y a quien "
            "escribe le manda el enlace en esa conversación. Después vuelve a probar con un privado; si llega, todo "
            "sigue normal.\n\n"
            "No tienes que hacer nada. Si te llega este correo varios días seguidos, avisa a soporte."))

    def _sin_privado(self, c: sqlite3.Row, ahora: datetime) -> Paso:
        """Pausa de privados: no se intenta. Se le contesta en público y, si escribe, recibe el enlace en esa
        conversación (igual que si TikTok no hubiera dejado pasar el privado, D47). Quien ya tenía el enlace
        (D49, otro reel) no recibe nada más."""
        with db.junto(self.conn):
            if c["contactado"] and c["resultado_contacto"] in reglas.LE_LLEGO:
                self._estado(c["id"], "saltado_contactado", ahora)
            else:
                reglas.marcar_contactado(self.conn, c["user_id"], NO_ENTREGADO, ahora, c["reel_id"])
                self._estado(c["id"], self._tras_cerrado(c), ahora, intentos=0,
                             ultimo_error="privados en pausa (TikTok no los dejaba pasar): se le invita en público a escribir")
        return Paso(True, comentario_id=c["id"])

    def _apuntar_si_sigue(self, c: sqlite3.Row, perfil: InfoPerfil, ahora: datetime) -> None:
        te_sigue = None if perfil.te_sigue is None else int(perfil.te_sigue)
        with self.conn:
            self.conn.execute("UPDATE contactos SET te_sigue = ?, sigue_mirado_en = ? WHERE user_id = ?",
                              (te_sigue, iso(ahora), c["user_id"]))

    @staticmethod
    def _tras_dm(c: sqlite3.Row, cfg: dict) -> str:
        """Estado final de un comentario cuyo mensaje privado salió: su respuesta pública pendiente, o terminado."""
        return "resp_pendiente" if cfg["modo_publica"] == "todos" and not c["ofensivo"] else "completado"

    @staticmethod
    def _tras_cerrado(c: sqlite3.Row) -> str:
        return "completado_cerrado" if c["ofensivo"] else "alt_pendiente"  # D3: ofensivo, nunca en público

    def _dm_enviado(self, c: sqlite3.Row, cfg: dict, ahora: datetime) -> None:
        # Auditoría 26 nov (hallazgo 20): antes pasaba por «dm_enviado» y después a su estado final, en dos
        # guardados. Si el bot se cerraba entre los dos, el comentario se quedaba en «dm_enviado» para siempre
        # (nada lo recoge): su respuesta pública no salía y los otros comentarios de esa persona tampoco avanzaban.
        with db.junto(self.conn):
            reglas.marcar_contactado(self.conn, c["user_id"], "dm_enviado", ahora, c["reel_id"])
            self._estado(c["id"], self._tras_dm(c, cfg), ahora, intentos=0, ultimo_error=None)

    def _cerrado(self, c: sqlite3.Row, ahora: datetime) -> Paso:
        with db.junto(self.conn):
            self._accion("dm", c, "cerrado", ahora)  # no cuenta para los topes…
            reglas.marcar_contactado(self.conn, c["user_id"], "dm_cerrado", ahora, c["reel_id"])
            self._estado(c["id"], self._tras_cerrado(c), ahora, intentos=0)
        self.ritmo.hecho("perfil", ahora)  # …pero se abrió su perfil y su chat: pausa antes del siguiente
        self.freno.accion_ok()
        return Paso(True, comentario_id=c["id"])

    def _reparar_a_medias(self, cfg: dict, ahora: datetime) -> None:
        """Comentarios que una versión anterior dejó a medias en «dm_enviado» o «dm_cerrado» (cierre del bot entre
        dos guardados, hallazgo 20): pasan al estado en el que debían quedar."""
        for f in self.conn.execute(
                "SELECT id FROM comentarios WHERE estado IN ('dm_enviado', 'dm_cerrado') ORDER BY comentado_en").fetchall():
            c = self._comentario(f["id"])
            if c["estado"] == "dm_enviado":
                self._estado(c["id"], self._tras_dm(c, cfg), ahora, intentos=0)
            else:
                self._estado(c["id"], self._tras_cerrado(c), ahora, intentos=0)
            log.info("Comentario %s: estaba a medias (%s), sigue donde debía.", c["id"], c["estado"])

    def _recuperar(self, c: sqlite3.Row, cfg: dict, ahora: datetime) -> Paso:
        """Corte en `dm_enviando`: si se ve el mensaje → dm_enviado; si no se puede asegurar → dudoso."""
        try:
            visto = self.pantallas.dm_ya_enviado(c["usuario"])
        except AvisoTikTok as aviso:
            return self._por_aviso(c["id"], aviso, ahora)
        except Exception as e:  # noqa: BLE001
            visto = None
            log.warning("No se pudo comprobar el envío de %s: %s", c["id"], e)
        with db.junto(self.conn):
            if visto:
                self._accion("dm", c, "simulado" if cfg["modo_prueba"] else "ok", ahora)
                self._dm_enviado(c, cfg, ahora)
            else:
                reglas.marcar_contactado(self.conn, c["user_id"], "dudoso", ahora, c["reel_id"])
                self._estado(c["id"], "dudoso", ahora, ultimo_error="corte durante el envío; no se pudo confirmar")
        return Paso(True, comentario_id=c["id"])

    # ------------------------------------------------------------ respuesta pública

    def _responder(self, c: sqlite3.Row, tipo: str, cfg: dict, ahora: datetime) -> Paso:
        cid = c["id"]
        if not self._hay_textos(tipo):
            return Paso(False, problema=f"No hay textos activos de tipo '{tipo}': escríbelos en el panel.", comentario_id=cid)
        texto_id = None
        try:
            texto_id, texto = textos.preparar(self.conn, tipo, c["nombre"], c["reel_id"], self.rng)
            resultado = self.pantallas.responder(c["reel_id"], cid, texto, simulado=cfg["modo_prueba"])
        except AvisoTikTok as aviso:
            if getattr(aviso, "tras_enviar", False):
                self._respuesta_dudosa(c, tipo, texto_id, ahora, aviso.detalle or aviso.clase)
            return self._por_aviso(cid, aviso, ahora)
        except (textos.SinTextos, textos.SinLanding) as e:
            return Paso(False, problema=str(e), comentario_id=cid)
        except textos.NumeroEnPublico as e:  # D44: nunca se publica; el comentario se cierra sin respuesta
            self._estado(cid, "error_final", ahora, ultimo_error=str(e))
            return Paso(True, comentario_id=cid)
        except Exception as e:  # noqa: BLE001
            if getattr(e, "tras_enviar", False):
                detalle = getattr(e, "detalle", None) or str(e) or type(e).__name__
                self._respuesta_dudosa(c, tipo, texto_id, ahora, detalle)
                freno = self.freno.error_tecnico(f"{cid}: {detalle}", getattr(e, "captura", None), ahora)
                return Paso(True, freno=freno, comentario_id=cid)
            return self._por_error(cid, e, ahora)

        self.freno.accion_ok()
        if resultado == "no_disponible" and c["estado"] == "rec_pendiente":
            self._estado(cid, "saltado_no_disponible", ahora, ultimo_error="comentario no disponible al responder")
            return Paso(True, comentario_id=cid)
        if resultado == "no_disponible":  # el comentario ya no existe
            final = "completado" if tipo == "respuesta" else "saltado_no_disponible"
            self._estado(cid, final, ahora, ultimo_error="comentario no disponible al responder")
            return Paso(True, comentario_id=cid)

        accion = textos.ACCION_DE_TIPO[tipo]
        if c["estado"] == "rec_pendiente":
            final = "completado_recordatorio"
        elif tipo == "respuesta":
            final = "completado"
        else:
            final = "completado_publica" if c["resultado_contacto"] == "publica" else "completado_cerrado"
        # Juntos (hallazgo 20): si se guardaba la acción y el bot se cerraba antes del estado, el comentario seguía
        # «pendiente» y la respuesta se publicaba otra vez.
        with db.junto(self.conn):
            self._accion(accion, c, "simulado" if cfg["modo_prueba"] else "ok", ahora, texto_id)
            self._estado(cid, final, ahora)
        textos.marcar_usado(self.conn, texto_id)
        self.ritmo.hecho(accion, ahora)
        return Paso(True, comentario_id=cid)

    def _respuesta_dudosa(self, c: sqlite3.Row, tipo: str, texto_id: int | None, ahora: datetime, detalle: str) -> None:
        """Falló DESPUÉS de pulsar publicar: pudo salir. Nunca se vuelve a publicar a ciegas (auditoría 26 nov):
        queda «dudoso» para revisar y cuenta como acción para el ritmo, por si salió."""
        accion = textos.ACCION_DE_TIPO[tipo]
        with db.junto(self.conn):
            self._accion(accion, c, "dudoso", ahora, texto_id)
            self._estado(c["id"], "dudoso", ahora, ultimo_error=f"no se pudo comprobar la respuesta pública: {detalle}"[:500])
        self.ritmo.hecho(accion, ahora)

    # ------------------------------------------------------------ mensajes privados fuera de comentarios (§17)

    def _envios(self, ahora: datetime, esperas: list[datetime]) -> Paso | None:
        """Corte a mitad de un envío → dudoso (nunca se reenvía a ciegas). Después, el siguiente pendiente que ya
        toque: primero las respuestas en la conversación, luego seguimientos y bienvenidas."""
        with self.conn:
            self.conn.execute("UPDATE envios SET estado = 'dudoso', ultimo_error = 'corte durante el envío', "
                              "actualizado_en = ? WHERE estado = 'enviando'", (iso(ahora),))
        filas = self.conn.execute(
            "SELECT * FROM envios WHERE estado = 'pendiente' AND listo_desde <= ? ORDER BY "
            "CASE tipo WHEN 'confirmar_numero' THEN 0 WHEN 'dm' THEN 1 WHEN 'pedir_numero' THEN 1 WHEN 'animo' THEN 1 "
            "WHEN 'seguimiento' THEN 2 ELSE 3 END, listo_desde, id", (iso(ahora),)).fetchall()
        # «Enviar mensaje privado ahora» (botón del Registro): lo pidió una persona para alguien concreto, así que
        # sale ya, sin esperar el turno del ritmo. Sí queda apuntado como acción, para que lo siguiente espere.
        manual = next((e for e in filas if e["tipo"] in (A_MANO, PUBLICA_A_MANO)), None)
        if manual is not None and manual["tipo"] == PUBLICA_A_MANO:
            return self._publica_a_mano(manual, ahora)
        if manual is not None:
            return self._envio(manual, "dm", ahora)
        pausa = reglas.privados_en_pausa(self.conn, ahora)
        for e in filas:
            if pausa and e["tipo"] == "bienvenida":
                continue  # la bienvenida es un primer privado a quien no escribió: espera a que acabe la pausa
            accion = "chat" if e["tipo"] == "dm" else textos.ACCION_DE_TIPO[e["tipo"]]
            decision = self.ritmo.puede(accion, ahora)
            if decision.ok:
                return self._envio(e, accion, ahora)
            if decision.hasta:
                esperas.append(decision.hasta)
            if decision.motivo not in ("tope_dm_dia",):
                break  # pausa o tope general: nada más puede salir ahora
        return None

    def _publica_a_mano(self, e: sqlite3.Row, ahora: datetime) -> Paso:
        """Botón «Comentar en público ahora» (27 nov): una persona pidió contestar en público a este comentario.
        Sale ya, sin esperar el turno del ritmo (un clic = un comentario), y queda apuntado como acción para que
        lo siguiente espere. Texto según la persona: «escríbenos» si no le llegó el privado (D47, D35).
        Sin reintento automático, como el privado a mano (D51): si falla, el Registro lo dice."""
        cfg = config.leer_todo(self.conn)
        c = self._comentario(e["comentario_id"]) if e["comentario_id"] else None
        if c is None or c["ofensivo"]:
            self._envio_estado(e["id"], "cancelado", ahora,
                               ultimo_error="comentario ofensivo: nunca en público" if c else "no se encontró el comentario")
            return Paso(True)
        tipo = reglas.tipo_recordatorio(c["etapa"], c["resultado_contacto"])
        if not self._hay_textos(tipo):
            tipo = "respuesta_alt"
        try:
            texto_id, texto = textos.preparar(self.conn, tipo, c["nombre"], c["reel_id"], self.rng)
        except (textos.SinTextos, textos.SinLanding) as error:
            self._envio_estado(e["id"], "error_final", ahora, ultimo_error=str(error)[:300])
            return Paso(True)
        self._envio_estado(e["id"], "enviando", ahora)  # ANTES de pulsar publicar (§6)
        try:
            resultado = self.pantallas.responder(c["reel_id"], c["id"], texto, simulado=cfg["modo_prueba"])
        except AvisoTikTok as aviso:
            if getattr(aviso, "sin_enviar", False):
                self._envio_estado(e["id"], "pendiente", ahora)
            return Paso(True, freno=self.freno.aviso(aviso.clase, aviso.detalle, aviso.captura, ahora))
        except Exception as error:  # noqa: BLE001
            detalle = getattr(error, "detalle", None) or str(error) or type(error).__name__
            estado = "error_final" if getattr(error, "sin_enviar", False) else "dudoso"
            self._envio_estado(e["id"], estado, ahora, ultimo_error=detalle[:500])
            return Paso(True, freno=self.freno.error_tecnico(f"comentario a mano {c['id']}: {detalle}",
                                                            getattr(error, "captura", None), ahora))
        if resultado == "no_disponible":
            self._envio_estado(e["id"], "cancelado", ahora, ultimo_error="el comentario ya no está en TikTok")
            return Paso(True)
        accion = textos.ACCION_DE_TIPO[tipo]
        with db.junto(self.conn):
            self._accion(tipo, c, "simulado" if cfg["modo_prueba"] else "ok", ahora, texto_id)
            self._envio_estado(e["id"], "simulado" if cfg["modo_prueba"] else "enviado", ahora, ultimo_error=None)
        textos.marcar_usado(self.conn, texto_id)
        self.ritmo.hecho(accion, ahora)
        self.freno.accion_ok()
        return Paso(True, comentario_id=c["id"])

    def _envio_estado(self, eid: int, estado: str, ahora: datetime, **extra) -> None:
        campos = "".join(f", {k} = ?" for k in extra)
        with db.junto(self.conn):
            self.conn.execute(f"UPDATE envios SET estado = ?, actualizado_en = ?{campos} WHERE id = ?",
                              (estado, iso(ahora), *extra.values(), eid))

    def _envio(self, e: sqlite3.Row, accion: str, ahora: datetime) -> Paso:
        cfg = config.leer_todo(self.conn)
        k = self.conn.execute("SELECT * FROM contactos WHERE user_id = ?", (e["user_id"],)).fetchone()
        tipo = e["tipo"]
        if k is None and tipo == A_MANO:
            self._envio_estado(e["id"], "cancelado", ahora, ultimo_error="no se encontró a la persona")
            return Paso(True)
        if k is None or (k["humano"] and tipo in ("dm", "pedir_numero", "animo")) or (
                tipo == "bienvenida" and (k["contactado"] or k["etapa"] != "A")) or (
                # Auditoría 26 nov: mientras este enlace esperaba su reintento, otro camino (su comentario) ya se lo dio.
                tipo == "dm" and k["contactado"] and k["resultado"] in reglas.CON_INFO):
            self._envio_estado(e["id"], "cancelado", ahora, ultimo_error="ya no hace falta")
            return Paso(True)
        vendedor = None
        if tipo == "seguimiento":
            lead = self.conn.execute("SELECT vendedor FROM leads WHERE user_id = ? AND hablado_en IS NOT NULL "
                                     "ORDER BY id DESC LIMIT 1", (k["user_id"],)).fetchone()
            vendedor = lead["vendedor"] if lead else None
        try:
            texto_id, texto = textos.preparar(self.conn, "dm" if tipo == A_MANO else tipo, k["nombre"], k["reel_origen"],
                                              self.rng, vendedor=vendedor)
        except (textos.SinTextos, textos.SinLanding) as error:  # no frena al bot: este mensaje no sale y se avisa
            self._envio_estado(e["id"], "cancelado", ahora, ultimo_error=str(error)[:300])
            with self.conn:
                self.conn.execute("INSERT INTO eventos (tipo, detalle, creado_en) VALUES ('falta_texto', ?, ?)",
                                  (f"@{k['usuario']}: {error}", iso(ahora)))
            return Paso(True)

        self._envio_estado(e["id"], "enviando", ahora)  # ANTES de pulsar enviar (§6)
        try:
            resultado = self.pantallas.enviar_dm(k["usuario"], texto, simulado=cfg["modo_prueba"],
                                                 solo_si_nuevo=tipo in ("bienvenida", "dm", A_MANO))  # D36: el enlace, una vez
        except AvisoTikTok as aviso:
            if getattr(aviso, "sin_enviar", False):
                self._envio_estado(e["id"], "pendiente", ahora)
            freno = self.freno.aviso(aviso.clase, aviso.detalle, aviso.captura, ahora)
            return Paso(True, freno=freno)
        except Exception as error:  # noqa: BLE001
            detalle = getattr(error, "detalle", None) or str(error) or type(error).__name__
            if getattr(error, "sin_enviar", False) and tipo == A_MANO:
                # A mano no se reintenta solo: se ve en el Registro y la persona decide si pulsa otra vez.
                self._envio_estado(e["id"], "error_final", ahora, intentos=e["intentos"] + 1, ultimo_error=detalle[:500])
            elif getattr(error, "sin_enviar", False):
                intentos = e["intentos"] + 1
                if intentos > MAX_REINTENTOS:
                    self._envio_estado(e["id"], "error_final", ahora, intentos=intentos, ultimo_error=detalle[:500])
                else:
                    listo = ahora + ESPERAS_REINTENTO[intentos - 1]
                    self._envio_estado(e["id"], "pendiente", ahora, intentos=intentos, listo_desde=iso(listo),
                                       ultimo_error=detalle[:500])
            # si falló tras pulsar enviar, se queda en «enviando» y la próxima vuelta lo deja en dudoso
            freno = self.freno.error_tecnico(f"envío {e['id']}: {detalle}", getattr(error, "captura", None), ahora)
            return Paso(True, freno=freno)

        self.freno.accion_ok()
        if resultado == "bloqueado":  # TikTok contestó 7195: como «no llegó» (no cuenta para la pausa, D50/D51)
            resultado = "dudoso"
        if resultado in ("cerrado", "ya_hablado"):
            with db.junto(self.conn):
                self._envio_estado(e["id"], "cerrado" if resultado == "cerrado" else "cancelado", ahora,
                                   ultimo_error="mensajes cerrados" if resultado == "cerrado" else
                                   ("ya tenía el enlace en el chat" if tipo == A_MANO else "ya habíamos hablado"))
                if resultado == "ya_hablado":
                    reglas.marcar_contactado(self.conn, k["user_id"], "ya_hablado", ahora)
            return Paso(True)
        seguidor = tipo == "bienvenida"
        # «simulado» (modo de prueba): no le llegó nada; al pasar a real se repone (reglas.reponer_simulados).
        final = "dudoso" if resultado == "dudoso" else ("simulado" if cfg["modo_prueba"] else "enviado")
        with db.junto(self.conn):  # todo junto (hallazgo 20)
            self.conn.execute(
                "INSERT INTO acciones (tipo, texto_id, resultado, hecho_en, seguidor, duracion_ms) VALUES (?, ?, ?, ?, ?, ?)",
                (accion, texto_id, "dudoso" if resultado == "dudoso" else ("simulado" if cfg["modo_prueba"] else "ok"),
                 iso(ahora), int(seguidor), self._ms()))
            self.conn.execute("INSERT INTO mensajes (user_id, de, texto, clase, creado_en) VALUES (?, 'bot', ?, ?, ?)",
                              (k["user_id"], texto, tipo, iso(ahora)))
            self._envio_estado(e["id"], final, ahora)
            if tipo in ("dm", A_MANO, "bienvenida") and final != "dudoso":
                reglas.marcar_contactado(self.conn, k["user_id"], "bienvenida" if tipo == "bienvenida" else "dm_enviado",
                                         ahora)
            elif tipo in ("dm", "bienvenida"):  # como antes: el «dudoso» de estos cuenta como enviado (D46)
                reglas.marcar_contactado(self.conn, k["user_id"], "dm_enviado" if tipo == "dm" else "bienvenida", ahora)
        textos.marcar_usado(self.conn, texto_id)
        self.ritmo.hecho(accion, ahora)
        return Paso(True)

    # ------------------------------------------------------------ avisos y errores

    def _por_aviso(self, cid: str, aviso: AvisoTikTok, ahora: datetime) -> Paso:
        """El comentario se queda donde estaba (en_cola / *_pendiente / dm_enviando) sin contar intento.
        Si el aviso salió ANTES de pulsar enviar, vuelve a la cola (no salió nada: no hace falta recuperación)."""
        if getattr(aviso, "sin_enviar", False):
            c = self._comentario(cid)
            if c["estado"] == "dm_enviando":
                self._estado(cid, "en_cola", ahora)
        resultado = self.freno.aviso(aviso.clase, aviso.detalle, aviso.captura, ahora)
        return Paso(True, freno=resultado, comentario_id=cid)

    def _por_error(self, cid: str, error: Exception, ahora: datetime) -> Paso:
        c = self._comentario(cid)
        detalle = getattr(error, "detalle", None) or str(error) or type(error).__name__
        captura = getattr(error, "captura", None)
        # En dm_enviando lo resuelve la recuperación (sin reenviar), salvo que el fallo fuera ANTES de pulsar
        # enviar (no se abrió el chat, no se pudo escribir…): entonces seguro que no salió y se reintenta.
        if c["estado"] != "dm_enviando" or getattr(error, "sin_enviar", False):
            intentos = c["intentos"] + 1
            if intentos > MAX_REINTENTOS:
                estado = "error_final"
            elif c["estado"] in ("resp_pendiente", "alt_pendiente"):
                estado = c["estado"]  # solo se reintenta la respuesta
            else:
                estado = "error"
            self._estado(cid, estado, ahora, intentos=intentos, ultimo_error=detalle[:500])
        freno = self.freno.error_tecnico(f"{cid}: {detalle}", captura, ahora)
        return Paso(True, freno=freno, comentario_id=cid)

    # ------------------------------------------------------------ ID estable

    def _unificar_contacto(self, c: sqlite3.Row, perfil: InfoPerfil) -> sqlite3.Row:
        """Si el perfil da el ID interno estable, la persona pasa a guardarse con ese ID (§5).
        Así, si cambia de @usuario, se sigue sabiendo que ya fue atendida."""
        viejo = c["user_id"]
        with self.conn:
            if perfil.nombre:
                self.conn.execute("UPDATE contactos SET nombre = ? WHERE user_id = ?", (perfil.nombre, viejo))
            nuevo = perfil.user_id
            if nuevo and nuevo != viejo:
                # Todo lo de las dos fichas se junta (atendida, etapa, número, leads…): auditoría 26 nov.
                reglas.unir_contactos(self.conn, viejo, nuevo)
                self.conn.execute("UPDATE contactos SET usuario = ?, nombre = COALESCE(?, nombre) WHERE user_id = ?",
                                  (c["usuario"], perfil.nombre, nuevo))
        return self._comentario(c["id"])

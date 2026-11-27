"""Bucle principal (PLAN-OFICIAL §3, tarea E3). Una sola cosa a la vez, en una sola pestaña.

Cada `vuelta(ahora)`:
1. Latido (para que el panel sepa que el bot vive).
2. Si está frenado / necesita a una persona → nada en TikTok (pestaña en blanco) hasta que toque.
3. Si el panel pidió pausa → pausado.
4. Si toca descanso → descansando; en el descanso diario, autoprueba de pantallas una vez al día.
5. Lectura de comentarios si el ritmo deja.
5b. §17: bandeja de mensajes (D37) y seguidores nuevos (D40), cada una con su intervalo; leads pendientes de
    pasar al equipo (D38). Si una de estas lecturas falla (p. ej. TikTok cambió la página), no frena al bot:
    se apunta, se avisa a soporte y se vuelve a intentar en su siguiente turno.
6. Histórico (D10) a la cola si no queda nada nuevo, del más nuevo al más viejo.
7. En `solo_cerrados` y solo con `fijado_auto` (por defecto no, D28): comentario fijado en los reels con
   comentarios en cola que aún no lo tengan. Por defecto lo fija la persona a mano.
8. Un paso del ejecutor (como mucho una acción).
Devuelve cuántos segundos esperar hasta la siguiente vuelta.
"""

from __future__ import annotations

import logging
import random
import sqlite3
import time as reloj
from datetime import UTC, datetime, time, timedelta

from bot import alertas, config, leads, redaccion, textos
from bot.bandeja import LectorBandeja
from bot.ejecutor import Ejecutor
from bot.freno import Alerta, Freno
from bot.pantallas import AvisoTikTok, Detector, ErrorPantalla, Pantallas
from bot.ritmo import ZONA, Ritmo, iso

log = logging.getLogger("bot.bucle")

ESPERA_MAX = 60  # segundos: el bucle nunca duerme más (para leer órdenes del panel a tiempo)
ESPERA_TRABAJO = 1
PARADOS = ("frenado", "parado_revision", "sesion_cerrada")
COMPROBAR_SESION_MIN = 5  # sin sesión, se vuelve a mirar cada 5 min (al iniciar sesión el bot se reinicia y mira ya)


def _ms_desde(inicio: float) -> int:
    """Milisegundos desde `inicio` (reloj.monotonic): se guardan en `acciones.duracion_ms` para poder medir."""
    return max(1, round((reloj.monotonic() - inicio) * 1000))


def _segundos(hasta: datetime | None, ahora: datetime) -> float:
    if hasta is None:
        return ESPERA_MAX
    return max(ESPERA_TRABAJO, min(ESPERA_MAX, (hasta - ahora).total_seconds()))


MIRAR_BOTON = 2  # segundos: mientras duerme, cada cuánto mira si alguien pulsó «Enviar mensaje privado ahora»


def ultimo_a_mano(conn: sqlite3.Connection) -> int:
    """Id del último mensaje pedido a mano que sigue pendiente (0 si no hay)."""
    fila = conn.execute("SELECT MAX(id) FROM envios WHERE tipo IN ('dm_manual', 'publica_manual') "
                        "AND estado = 'pendiente'").fetchone()
    return fila[0] or 0


def dormir(conn: sqlite3.Connection, segundos: float, esperar=None) -> None:
    """Duerme hasta `segundos`, pero se despierta en cuanto alguien pulsa «Enviar mensaje privado ahora» en el
    Registro (sin esto, el clic podía tardar hasta un minuto en notarse). Solo un clic NUEVO lo despierta: si el bot
    está en pausa o frenado, el pendiente espera sin que el bot dé vueltas sin parar."""
    esperar = esperar or reloj.sleep
    antes = ultimo_a_mano(conn)
    restante = segundos
    while restante > 0:
        if ultimo_a_mano(conn) > antes:
            return
        paso = min(MIRAR_BOTON, restante)
        esperar(paso)
        restante -= paso


ESPERA_APAGADO = 30  # segundos entre comprobaciones mientras el bot está apagado


def esperar_encendido(conn: sqlite3.Connection, dormir=None) -> None:
    """Bot apagado desde el panel: no abre Chrome ni impide que la computadora duerma. Da señal de vida
    (para que el panel no diga «no responde») y espera a que alguien pulse «Encender»."""
    import time as _time

    dormir = dormir or _time.sleep
    avisado = False
    while config.leer(conn, "apagado"):
        if not avisado:
            log.info("Bot apagado desde el panel: espera a que lo enciendan")
            avisado = True
        with conn:
            conn.execute("UPDATE estado_bot SET modo = 'apagado', mensaje = 'Apagado desde el panel', ultimo_latido = ? "
                         "WHERE id = 1", (iso(datetime.now(UTC)),))
        dormir(ESPERA_APAGADO)


class Bucle:
    def __init__(self, conn: sqlite3.Connection, pantallas: Pantallas, detector: Detector, rng: random.Random | None = None):
        self.conn = conn
        self.pantallas = pantallas
        self.detector = detector
        self.rng = rng or random.Random()
        self.ritmo = Ritmo(conn, self.rng)
        self.freno = Freno(conn)
        self.ejecutor = Ejecutor(conn, pantallas, self.ritmo, self.freno, self.rng)
        self.lector = LectorBandeja(conn, pantallas, self.rng)
        self._en_blanco = False
        self._sesion_ok = False  # se comprueba al arrancar el proceso y tras «sesión cerrada»
        self._proxima_comprobacion: datetime | None = None
        self._venia_de_cerrada = False

    # ------------------------------------------------------------ estado

    def _modo(self, modo: str, mensaje: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE estado_bot SET modo = ?, mensaje = ? WHERE id = 1", (modo, mensaje))

    def _latido(self, ahora: datetime) -> None:
        with self.conn:
            self.conn.execute("UPDATE estado_bot SET ultimo_latido = ? WHERE id = 1", (iso(ahora),))

    def _blanco(self) -> None:
        if not self._en_blanco:
            self.pantallas.en_blanco()
            self._en_blanco = True

    def _alerta(self, alerta: Alerta | None, ahora: datetime) -> None:
        if alerta is not None:
            alertas.enviar_alerta(self.conn, alerta, ahora)

    # ------------------------------------------------------------ vuelta

    def vuelta(self, ahora: datetime) -> float:
        try:
            return self._vuelta(ahora)
        except AvisoTikTok as aviso:
            r = self.freno.aviso(aviso.clase, aviso.detalle, aviso.captura, ahora)
            self._alerta(r.alerta, ahora)
            return ESPERA_TRABAJO
        except Exception as e:  # noqa: BLE001 — el bucle no debe morir nunca
            log.exception("Fallo inesperado en el bucle")
            r = self.freno.error_tecnico(f"bucle: {type(e).__name__}: {e}", None, ahora, al_actuar=False)
            self._alerta(r.alerta if r else None, ahora)
            return ESPERA_MAX

    def _vuelta(self, ahora: datetime) -> float:
        self._latido(ahora)
        comprobar = getattr(self.pantallas, "comprobar_navegador", None)
        if comprobar is not None:  # si cerraron la ventana del bot, el proceso termina y el servicio la reabre
            comprobar()
        cfg = config.leer_todo(self.conn)

        estado = self.freno.estado(ahora)
        if estado.modo == "sesion_cerrada" and not self._sesion_ok and self._toca_comprobar(ahora):
            # Recién arrancado (p. ej. tras «Iniciar sesión del bot»): si ya hay sesión, sigue solo.
            if self._comprobar_sesion(cfg, ahora) == "ok":
                self.freno.reanudar_manual(ahora)
                estado = self.freno.estado(ahora)
        if estado.modo in PARADOS:
            if estado.modo == "sesion_cerrada":
                self._sesion_ok = False
                self._venia_de_cerrada = True
            self._blanco()
            return _segundos(estado.hasta, ahora) if estado.modo != "sesion_cerrada" else ESPERA_MAX

        if cfg["apagado"]:  # sin servicio que lo reinicie (pruebas): se comporta como una pausa
            self._modo("apagado", "Apagado desde el panel")
            self._blanco()
            return ESPERA_MAX

        if cfg["orden_pausa"]:
            self._modo("pausado", "En pausa (desde el panel)")
            self._blanco()
            return ESPERA_MAX

        descanso = self.ritmo.descansando(ahora)
        if descanso is not None and not ultimo_a_mano(self.conn):  # un clic en el Registro no espera al descanso
            hora = descanso.hasta.astimezone(ZONA).strftime("%H:%M")
            self._modo("descansando", f"Descansando hasta las {hora} (Utah)")
            self._blanco()
            if descanso.motivo == "descanso_diario":
                self._autoprueba_diaria(ahora)
            return _segundos(descanso.hasta, ahora)

        if self._venia_de_cerrada:  # alguien pulsó «Reanudar»: se comprueba la sesión ya, no dentro de 5 min
            self._venia_de_cerrada, self._proxima_comprobacion = False, None
        if not self._sesion_ok:
            if not self._toca_comprobar(ahora):
                self._blanco()
                return ESPERA_MAX
            resultado = self._comprobar_sesion(cfg, ahora)
            if resultado == "sin_cuenta":
                self._modo("sin_cuenta", "Falta conectar la cuenta de TikTok")
                self._blanco()
                return ESPERA_MAX
            if resultado != "ok":
                return ESPERA_TRABAJO  # el freno ya quedó en «sesión cerrada» y avisó
            cfg = config.leer_todo(self.conn)

        self._modo("activo", "Activo" + (" · modo de prueba" if cfg["modo_prueba"] else ""))
        self._en_blanco = False

        if self.ritmo.puede("lectura", ahora).ok:
            inicio = reloj.monotonic()
            nuevos = self.detector.leer(ahora)
            with self.conn:
                self.conn.execute("INSERT INTO acciones (tipo, resultado, hecho_en, duracion_ms) VALUES ('lectura', 'ok', ?, ?)",
                                  (iso(ahora), _ms_desde(inicio)))
            self.ritmo.hecho("lectura", ahora)
            # Una lectura buena demuestra que el bot funciona: los cortes de red sueltos ya no son «seguidos».
            self.freno.lectura_ok()
            if nuevos:
                log.info("Lectura: %s comentarios nuevos", nuevos)

        self._lecturas_extra(cfg, ahora)

        if cfg["atender_historico"]:
            self._promover_historico(ahora)

        if cfg["modo_publica"] == "solo_cerrados" and cfg.get("fijado_auto") and self._fijar_si_toca(cfg, ahora):
            return ESPERA_TRABAJO

        paso = self.ejecutor.paso(ahora)
        if paso.freno is not None:
            self._alerta(paso.freno.alerta, ahora)
        if paso.problema:
            log.warning(paso.problema)
            # No puede trabajar por algo que tiene que poner una persona (textos, enlace): que el panel lo diga.
            self._modo("falta_configurar", paso.problema)
            self._alerta(Alerta(
                "necesita_persona", redaccion.asunto(redaccion.TE_NECESITA, "Te necesita: falta configurar algo en el panel"),
                "El bot está encendido, pero no puede seguir porque falta algo por configurar:\n\n"
                f"{paso.problema}\n\n"
                "Qué tienes que hacer: abre «Panel del bot». En Inicio verás la lista de lo que falta, con un botón para cada cosa."), ahora)
        if paso.trabajo:
            return ESPERA_TRABAJO
        siguiente_lectura = self.ritmo.proxima_lectura
        candidatos = [x for x in (paso.espera_hasta, siguiente_lectura, *self.ritmo.proximas.values()) if x is not None]
        return _segundos(min(candidatos) if candidatos else None, ahora)

    # ------------------------------------------------------------ piezas

    def _lecturas_extra(self, cfg: dict, ahora: datetime) -> None:
        """§17: bandeja (D37), seguidores nuevos (D40) y leads pendientes (D38)."""
        lecturas = (("bandeja", cfg["leer_bandeja"], "leer_bandeja", self.lector.leer, "la bandeja de mensajes"),
                    ("seguidores", cfg["bienvenida_seguidores"], "seguidores_nuevos", self.lector.leer_seguidores,
                     "los seguidores nuevos"))
        for tipo, activa, metodo, leer, que in lecturas:
            if not activa or not hasattr(self.pantallas, metodo) or not self.ritmo.puede(tipo, ahora).ok:
                continue
            resultado = "ok"
            inicio = reloj.monotonic()
            try:
                hechos = leer(ahora)
                self.freno.lectura_ok()
                if hechos:
                    log.info("Lectura de %s: %s", que, hechos)
            except ErrorPantalla as e:  # no frena al bot: lo demás sigue funcionando
                resultado = "error"
                log.warning("No se pudo leer %s: %s", que, e.detalle)
                with self.conn:
                    self.conn.execute("INSERT INTO eventos (tipo, detalle, captura, creado_en) VALUES ('error', ?, ?, ?)",
                                      (f"lectura de {que}: {e.detalle}", e.captura, iso(ahora)))
                self._alerta(Alerta(
                    "necesita_persona", redaccion.asunto(redaccion.REVISAR, f"No se pudo leer {que}: avisa a soporte"),
                    f"El bot sigue respondiendo comentarios, pero no pudo leer {que} de TikTok. Puede que TikTok haya "
                    "cambiado su página.\n\nQué tienes que hacer: avisa a soporte y reenvíales este correo."
                    f"{redaccion.tecnico(e.detalle)}", e.captura), ahora)
            with self.conn:
                self.conn.execute("INSERT INTO acciones (tipo, resultado, hecho_en, duracion_ms) VALUES (?, ?, ?, ?)",
                                  (tipo, resultado, iso(ahora), _ms_desde(inicio)))
            self.ritmo.hecho(tipo, ahora)
        leads.reintentar(self.conn, ahora)

    def _promover_historico(self, ahora: datetime) -> None:
        """D10: con la cola de nuevos vacía, pasa a la cola el comentario histórico más reciente."""
        pendiente = self.conn.execute(
            "SELECT 1 FROM comentarios WHERE estado IN ('en_cola', 'dm_enviando', 'resp_pendiente', 'alt_pendiente') LIMIT 1"
        ).fetchone()
        if pendiente:
            return
        with self.conn:
            self.conn.execute(
                "UPDATE comentarios SET estado = 'en_cola', actualizado_en = ? WHERE id = "
                "(SELECT id FROM comentarios WHERE estado = 'historico' ORDER BY comentado_en DESC LIMIT 1)",
                (iso(ahora),),
            )

    def _fijar_si_toca(self, cfg: dict, ahora: datetime) -> bool:
        """Publica y fija el comentario fijado en un reel activo con comentarios en cola que aún no lo tenga."""
        desde = iso(ahora - timedelta(days=cfg["reels_activos_dias"]))
        reel = self.conn.execute(
            "SELECT r.id FROM reels r WHERE r.activo = 1 AND r.fijado_estado = 'pendiente' "
            "AND (r.publicado_en IS NULL OR r.publicado_en >= ?) "
            "AND EXISTS (SELECT 1 FROM comentarios c WHERE c.reel_id = r.id AND c.estado = 'en_cola') LIMIT 1",
            (desde,),
        ).fetchone()
        if reel is None or not self.ritmo.puede("fijar", ahora).ok:
            return False
        try:
            texto_id, texto = textos.preparar(self.conn, "fijado", None, reel["id"], self.rng)
        except (textos.SinTextos, textos.SinLanding) as e:
            log.warning("Sin comentario fijado para %s: %s", reel["id"], e)
            return False
        try:
            resultado = self.pantallas.publicar_fijado(reel["id"], texto, simulado=cfg["modo_prueba"])
        except AvisoTikTok:
            raise
        except Exception as e:  # noqa: BLE001
            resultado = "error"
            r = self.freno.error_tecnico(f"fijar {reel['id']}: {getattr(e, 'detalle', e)}", getattr(e, "captura", None), ahora)
            self._alerta(r.alerta if r else None, ahora)
        with self.conn:
            self.conn.execute("UPDATE reels SET fijado_estado = ? WHERE id = ?", (resultado, reel["id"]))
            if resultado == "fijado":
                self.conn.execute(
                    "INSERT INTO acciones (tipo, reel_id, texto_id, resultado, hecho_en) VALUES ('fijar', ?, ?, ?, ?)",
                    (reel["id"], texto_id, "simulado" if cfg["modo_prueba"] else "ok", iso(ahora)),
                )
        if resultado == "fijado":
            textos.marcar_usado(self.conn, texto_id)
            self.ritmo.hecho("fijar", ahora)
        return True

    def _autoprueba_diaria(self, ahora: datetime) -> None:
        dia = ahora.astimezone(ZONA).date()
        desde = datetime.combine(dia, time(0), tzinfo=ZONA).astimezone(UTC)
        hecha = self.conn.execute(
            "SELECT 1 FROM eventos WHERE tipo = 'autoprueba' AND creado_en >= ?", (iso(desde),)
        ).fetchone()
        if hecha:
            return
        try:
            ok, detalle = self.pantallas.autoprueba()
        except AvisoTikTok:
            raise
        except Exception as e:  # noqa: BLE001
            ok, detalle = False, f"{type(e).__name__}: {e}"
        with self.conn:
            self.conn.execute("INSERT INTO eventos (tipo, detalle, creado_en) VALUES ('autoprueba', ?, ?)",
                              (("OK: " if ok else "FALLO: ") + detalle, iso(ahora)))
        self.pantallas.en_blanco()
        if not ok:
            self._alerta(Alerta(
                "necesita_persona", redaccion.asunto(redaccion.REVISAR, "La revisión diaria de pantallas falló: avisa a soporte"),
                "Cada día, en su descanso, el bot revisa que las pantallas de TikTok siguen como espera (sin tocar nada). "
                "Hoy algo no estaba donde debía: puede que TikTok haya cambiado su página.\n\n"
                "El bot sigue trabajando, pero puede empezar a fallar.\n"
                "Qué tienes que hacer: avisa a soporte y reenvíales este correo."
                f"{redaccion.tecnico(detalle)}"), ahora)

    # ------------------------------------------------------------ sesión de TikTok

    def _toca_comprobar(self, ahora: datetime) -> bool:
        return self._proxima_comprobacion is None or ahora >= self._proxima_comprobacion

    def _comprobar_sesion(self, cfg: dict, ahora: datetime) -> str:
        """ok | sin_cuenta (instalación nueva sin sesión) | cerrada (la cuenta ya conectada perdió la sesión).
        Si nadie había puesto la cuenta, se toma la de la sesión: el cliente no tiene que escribirla."""
        self._proxima_comprobacion = ahora + timedelta(minutes=COMPROBAR_SESION_MIN)
        comprobar = getattr(self.pantallas, "cuenta_en_sesion", None)
        if comprobar is None:  # pantallas de prueba sin esta función: se da por buena
            self._sesion_ok = True
            return "ok"
        cuenta = (cfg.get("cuenta") or "").strip().lstrip("@")
        en_sesion = comprobar()
        if en_sesion and not cuenta:
            config.guardar(self.conn, "cuenta", en_sesion)
            log.info("Cuenta de TikTok conectada: @%s", en_sesion)
            with self.conn:
                self.conn.execute("INSERT INTO eventos (tipo, detalle, creado_en) VALUES ('cuenta', ?, ?)",
                                  (f"cuenta conectada: @{en_sesion}", iso(ahora)))
            cuenta = en_sesion
        if en_sesion and en_sesion.casefold() == cuenta.casefold():
            self._sesion_ok = True
            return "ok"
        if not cuenta:
            return "sin_cuenta"
        detalle = (f"Hay otra cuenta iniciada (@{en_sesion}) en lugar de @{cuenta}" if en_sesion
                   else "No hay ninguna cuenta con la sesión iniciada")
        r = self.freno.aviso("sesion_cerrada", detalle, getattr(self.pantallas, "captura_sesion", None), ahora)
        self._alerta(r.alerta, ahora)
        self._venia_de_cerrada = True  # si alguien pulsa «Reanudar», se vuelve a comprobar en ese momento
        return "cerrada"

    # ------------------------------------------------------------ correr de verdad

    def correr(self) -> None:  # pragma: no cover — se prueba en E3 con la cuenta de pruebas
        from bot.despierto import Despierto

        log.info("Bot arrancado")
        with self.conn:
            self.conn.execute("INSERT INTO eventos (tipo, detalle, creado_en) VALUES ('arranque', 'bot arrancado', ?)",
                              (iso(datetime.now(UTC)),))
        with Despierto():
            while True:
                espera = self.vuelta(datetime.now(UTC))
                dormir(self.conn, espera)

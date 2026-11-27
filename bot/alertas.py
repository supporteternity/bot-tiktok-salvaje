"""Alertas por email (PLAN-OFICIAL §8). Datos SMTP solo en `.env`; destinatarios en `emails_alerta`.

- enviar_alerta: frenos (§7.3) y "necesita a una persona". La misma alerta no se repite antes de 1 h.
- revisar_cola: cola > `cola_alerta` (máx. 1 email cada 12 h).
- revisar_latido: el bot no da señales hace > 15 min sin estar frenado ni descansando (lo llama el panel).
- revisar_resumen: resumen diario a las 09:00 de Utah, una vez al día (lo llama el panel).
"""

from __future__ import annotations

import logging
import mimetypes
import re
import smtplib
from email.utils import formataddr
import sqlite3
import ssl
from datetime import UTC, datetime, time, timedelta
from email.message import EmailMessage
from pathlib import Path

from bot import config, redaccion, ritmo
from bot.freno import Alerta
from bot.ritmo import NIVELES, ZONA, _fecha, iso

log = logging.getLogger("bot.alertas")

REPETIR_TRAS = timedelta(hours=1)
COLA_CADA = timedelta(hours=12)
LATIDO_MAX = timedelta(minutes=15)
HORA_RESUMEN = time(9, 0)
# Auditoría 26 nov (hallazgo 17): tras un fallo del correo, ningún aviso se intenta en este tiempo (antes, cada
# minuto: Gmail podía bloquear la cuenta). El fallo se apunta como «alerta_fallida», no como error del bot.
ESPERA_TRAS_FALLO = timedelta(minutes=15)
_ultimo_aviso_sin_correo: datetime | None = None
# Teléfonos (7 cifras o más, con espacios, guiones, paréntesis o «+»): fuera de bot.log (hallazgo 18).
_TELEFONO = re.compile(r"\+?\d[\d\s().-]{5,}\d")
_FECHA = re.compile(r"\d{4}-\d{2}-\d{2}")


def sin_telefonos(texto: str) -> str:
    """Quita los números de teléfono (7 cifras o más) de un texto que va a bot.log; las fechas (2026-11-26) no."""
    def cambiar(m: re.Match) -> str:
        trozo = m.group(0)
        if _FECHA.fullmatch(trozo) or sum(c.isdigit() for c in trozo) < 7:
            return trozo
        return "[número]"
    return _TELEFONO.sub(cambiar, texto)


# ---------------------------------------------------------------- envío


def _contexto_tls() -> ssl.SSLContext:
    return ssl.create_default_context()


def datos_smtp() -> dict[str, str | None]:
    s = config.cargar_secretos()
    return {k: s.get(k) for k in ("SMTP_HOST", "SMTP_PUERTO", "SMTP_USUARIO", "SMTP_CONTRASENA", "SMTP_REMITENTE")}


def correo_configurado() -> bool:
    d = datos_smtp()
    return bool(d["SMTP_HOST"] and d["SMTP_USUARIO"] and d["SMTP_CONTRASENA"])


def _entrar(srv: smtplib.SMTP, usuario: str, clave: str) -> None:
    """Inicia sesión con UN solo método. smtplib, si el primero falla, prueba otro; Gmail entonces corta la
    conexión y el error real («usuario o contraseña no aceptados», 535) se perdía como «no se pudo conectar»."""
    if hasattr(srv, "ehlo_or_helo_if_needed"):
        srv.ehlo_or_helo_if_needed()
    metodos = (getattr(srv, "esmtp_features", {}).get("auth") or "").upper().split()
    if "PLAIN" in metodos:
        srv.user, srv.password = usuario, clave
        srv.auth("PLAIN", srv.auth_plain)
    else:
        srv.login(usuario, clave)


def enviar_con(datos: dict, asunto: str, cuerpo: str, destinatarios: list[str], adjunto: str | None = None) -> None:
    """Envía con esos datos SMTP. Lanza la excepción de smtplib/red si falla."""
    host, usuario, clave = datos["SMTP_HOST"], datos["SMTP_USUARIO"], datos["SMTP_CONTRASENA"]
    msg = EmailMessage()
    remitente = datos.get("SMTP_REMITENTE") or usuario
    msg["Subject"], msg["To"] = asunto, ", ".join(destinatarios)
    msg["From"] = remitente if "<" in remitente else formataddr((redaccion.REMITENTE, remitente))
    msg.set_content(cuerpo)
    if adjunto and Path(adjunto).is_file():
        tipo = (mimetypes.guess_type(adjunto)[0] or "application/octet-stream").split("/")
        msg.add_attachment(Path(adjunto).read_bytes(), maintype=tipo[0], subtype=tipo[1], filename=Path(adjunto).name)
    puerto = int(datos.get("SMTP_PUERTO") or 587)
    if puerto == 465:
        with smtplib.SMTP_SSL(host, puerto, timeout=30, context=_contexto_tls()) as srv:
            _entrar(srv, usuario, clave)
            srv.send_message(msg)
    else:
        with smtplib.SMTP(host, puerto, timeout=30) as srv:
            srv.starttls(context=_contexto_tls())
            _entrar(srv, usuario, clave)
            srv.send_message(msg)


def _enviar(asunto: str, cuerpo: str, destinatarios: list[str], adjunto: str | None = None) -> bool:
    if not (correo_configurado() and destinatarios):
        global _ultimo_aviso_sin_correo
        ahora = datetime.now(UTC)
        if _ultimo_aviso_sin_correo is None or ahora - _ultimo_aviso_sin_correo >= REPETIR_TRAS:
            _ultimo_aviso_sin_correo = ahora  # el panel lo intenta cada minuto: no llenar el registro
            log.warning("Email no enviado (falta configurar el correo en el panel): %s", sin_telefonos(asunto))
        return False
    enviar_con(datos_smtp(), asunto, cuerpo, destinatarios, adjunto)
    return True


def explicar_error(e: Exception) -> str:
    """El fallo de envío explicado para alguien que no es técnico."""
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return ("El correo o la contraseña no son correctos. Si es Gmail, tiene que ser la «contraseña de "
                "aplicación» de 16 letras (paso 3), no la contraseña con la que entras a Gmail, y la "
                "verificación en 2 pasos tiene que estar activada (paso 2). Revisa también que el correo "
                "escrito sea el mismo con el que creaste la contraseña de aplicación.")
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        return "El servidor de correo no acepta la dirección a la que hay que avisar. Revisa que esté bien escrita."
    if isinstance(e, smtplib.SMTPSenderRefused):
        return "El servidor no deja enviar desde ese correo. Revisa que el correo esté bien escrito."
    if isinstance(e, smtplib.SMTPNotSupportedError):
        return "Ese servidor de correo no admite conexión segura en ese puerto. Prueba con el puerto 587 o 465."
    if isinstance(e, (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError)) or (
            isinstance(e, OSError) and not isinstance(e, smtplib.SMTPException)):
        return ("No se pudo conectar con el servidor de correo. Comprueba que hay internet"
                " y, si elegiste «Otro», que el servidor y el puerto estén bien.")
    return f"No se pudo enviar el email de prueba ({type(e).__name__}: {e})."


def probar_correo(datos: dict, destinatarios: list[str]) -> str | None:
    """Manda un email de prueba con esos datos. None si llegó al servidor; si no, el motivo en lenguaje sencillo."""
    try:
        enviar_con(datos, redaccion.asunto(redaccion.TODO_BIEN, "Correo de prueba: los avisos te van a llegar"),
                   "¡Funciona! Así te llegarán los avisos del bot de TikTok.\n\n"
                   "Busca siempre los correos que empiezan con «Bot de TikTok»:\n"
                   f"{redaccion.TE_NECESITA} rojo = te necesita, tienes que hacer algo.\n"
                   f"{redaccion.REVISAR} naranja = avisa a soporte.\n"
                   f"{redaccion.SOLO_AVISO} amarillo = solo para que lo sepas, se arregla solo.\n"
                   f"{redaccion.RESUMEN} resumen = cada mañana, cómo le fue ayer.\n\n"
                   "No tienes que responder a este correo.", destinatarios)
    except (OSError, smtplib.SMTPException) as e:
        log.warning("Prueba de correo fallida: %s", type(e).__name__)
        return explicar_error(e)
    return None


def _ultima(conn: sqlite3.Connection, clave: str) -> datetime | None:
    fila = conn.execute(
        "SELECT MAX(creado_en) FROM eventos WHERE tipo IN ('alerta_email', 'resumen') AND detalle = ?", (clave,)
    ).fetchone()
    return _fecha(fila[0]) if fila and fila[0] else None


def correo_arreglado(conn: sqlite3.Connection, ahora: datetime) -> None:
    """Se guardó en el panel un correo nuevo que ya pasó la prueba: las esperas por fallos anteriores se acaban."""
    with conn:
        conn.execute("INSERT INTO eventos (tipo, detalle, creado_en) VALUES ('correo_guardado', 'correo probado y guardado', ?)",
                     (iso(ahora),))


def en_espera_por_fallo(conn: sqlite3.Connection, tipos_fallo: tuple[str, ...], espera: timedelta, ahora: datetime) -> bool:
    """¿Falló el correo hace menos de `espera`? Un correo guardado después del fallo (correo_arreglado) la anula."""
    marcas = ",".join("?" * len(tipos_fallo))
    fallo = conn.execute(f"SELECT MAX(creado_en) FROM eventos WHERE tipo IN ({marcas})", tipos_fallo).fetchone()[0]
    if not fallo or ahora - _fecha(fallo) >= espera:
        return False
    arreglo = conn.execute("SELECT MAX(creado_en) FROM eventos WHERE tipo = 'correo_guardado'").fetchone()[0]
    return not (arreglo and _fecha(arreglo) >= _fecha(fallo))


def _enviar_registrado(conn: sqlite3.Connection, clave: str, tipo_evento: str, asunto: str, cuerpo: str,
                       ahora: datetime, adjunto: str | None = None, no_antes_de: timedelta | None = None) -> bool:
    if no_antes_de and (ultima := _ultima(conn, clave)) and ahora - ultima < no_antes_de:
        return False
    if en_espera_por_fallo(conn, ("alerta_fallida",), ESPERA_TRAS_FALLO, ahora):
        return False  # el correo falló hace poco: se espera antes de volver a intentarlo
    try:
        ok = _enviar(asunto, cuerpo, config.leer(conn, "emails_alerta"), adjunto)
    except (OSError, smtplib.SMTPException) as e:
        log.error("Fallo al enviar email «%s»: %s", sin_telefonos(asunto), e)
        with conn:
            conn.execute("INSERT INTO eventos (tipo, detalle, creado_en) VALUES ('alerta_fallida', ?, ?)",
                         (sin_telefonos(f"email no enviado ({asunto}): {explicar_error(e)}")[:500], iso(ahora)))
        return False
    if ok:
        with conn:
            conn.execute("INSERT INTO eventos (tipo, detalle, captura, creado_en) VALUES (?, ?, ?, ?)",
                         (tipo_evento, clave, adjunto, iso(ahora)))
    return ok


def enviar_alerta(conn: sqlite3.Connection, alerta: Alerta, ahora: datetime) -> bool:
    """Alerta de freno o de "necesita a una persona". La misma (mismo asunto) no se repite antes de 1 h."""
    return _enviar_registrado(conn, f"{alerta.tipo}|{alerta.asunto}", "alerta_email", alerta.asunto, alerta.cuerpo,
                              ahora, alerta.captura, no_antes_de=REPETIR_TRAS)


# ---------------------------------------------------------------- cola y latido


def _datos_cola(conn: sqlite3.Connection, ahora: datetime) -> tuple[int, int, str, str]:
    cfg = config.leer_todo(conn)
    cola = conn.execute("SELECT COUNT(*) FROM comentarios WHERE estado = 'en_cola'").fetchone()[0]
    historico = conn.execute("SELECT COUNT(*) FROM comentarios WHERE estado = 'historico'").fetchone()[0]
    viejo = conn.execute("SELECT MIN(comentado_en) FROM comentarios WHERE estado = 'en_cola'").fetchone()[0]
    antiguedad = redaccion.duracion(max(0, (ahora - _fecha(viejo)).total_seconds())) if viejo else "—"
    por_dia = min(NIVELES[cfg["nivel_ritmo"]][1] / (1.6 if cfg["modo_publica"] == "todos" else 1), cfg["tope_dm_dia"])
    estimado = redaccion.duracion(cola / por_dia * 86400) if cola else "—"
    return cola, historico, antiguedad, estimado


def revisar_cola(conn: sqlite3.Connection, ahora: datetime) -> bool:
    cola, historico, antiguedad, estimado = _datos_cola(conn, ahora)
    if cola <= config.leer(conn, "cola_alerta"):
        return False
    return _enviar_registrado(
        conn, "cola_grande", "alerta_email",
        redaccion.asunto(redaccion.SOLO_AVISO, f"{redaccion.plural(cola, 'persona espera', 'personas esperan')} respuesta"),
        f"Se están juntando comentarios: {redaccion.plural(cola, 'persona espera', 'personas esperan')} su mensaje"
        + (f" (y {historico} comentarios de antes de instalar el bot)" if historico else "") + ".\n"
        f"La que más lleva esperando, desde hace {antiguedad}. Al ritmo de ahora, tardaría unos {estimado} en atender a todas.\n\n"
        "No es una urgencia: el bot sigue trabajando. Si quieres que vaya más rápido:\n"
        "• En «Panel del bot» → Inicio → «Velocidad de respuesta», elige una más rápida (más riesgo para la cuenta).\n"
        "• En Ajustes, deja «Respuesta pública» en «Solo a quien tiene los mensajes cerrados» (gasta menos acciones).\n"
        "• En Ajustes, pon un límite en «No atender comentarios con más de… días» para saltar los muy antiguos.",
        ahora, no_antes_de=COLA_CADA)


def revisar_latido(conn: sqlite3.Connection, ahora: datetime) -> bool:
    e = conn.execute("SELECT modo, ultimo_latido FROM estado_bot WHERE id = 1").fetchone()
    if e is None or e["modo"] in ("frenado", "descansando"):
        return False
    hace = ahora - _fecha(e["ultimo_latido"])
    if hace <= LATIDO_MAX:
        return False
    return _enviar_registrado(
        conn, "sin_actividad", "alerta_email",
        redaccion.asunto(redaccion.TE_NECESITA, f"No responde desde hace {redaccion.duracion(hace.total_seconds())}"),
        f"El bot lleva {redaccion.duracion(hace.total_seconds())} sin dar señales de vida, así que no está atendiendo comentarios.\n\n"
        "Qué tienes que hacer:\n"
        "1. Comprueba que la computadora del bot está encendida, despierta y con internet.\n"
        "2. Comprueba que la ventana de Chrome del bot sigue abierta (minimizada, en el Dock o en la barra de tareas).\n"
        "3. Si todo parece bien y en 5 minutos sigue igual, reinicia la computadora: el bot arranca solo.",
        ahora, no_antes_de=REPETIR_TRAS)


# ---------------------------------------------------------------- resumen diario


def texto_resumen(conn: sqlite3.Connection, ahora: datetime) -> tuple[str, str]:
    hoy = ahora.astimezone(ZONA).date()
    ayer = hoy - timedelta(days=1)
    desde = datetime.combine(ayer, time(0), tzinfo=ZONA).astimezone(UTC)
    hasta = datetime.combine(hoy, time(0), tzinfo=ZONA).astimezone(UTC)
    rango = (iso(desde), iso(hasta))

    def n(tabla: str, campo: str, condicion: str) -> int:
        sql = f"SELECT COUNT(*) FROM {tabla} WHERE {condicion} AND {campo} >= ? AND {campo} < ?"
        return conn.execute(sql, rango).fetchone()[0]

    detectados = n("comentarios", "detectado_en", "1=1")
    enviados = n("acciones", "hecho_en", "tipo='dm' AND resultado IN ('ok','simulado')")
    cerrados = n("acciones", "hecho_en", "tipo='dm' AND resultado='cerrado'")
    publicas = n("acciones", "hecho_en", "tipo IN ('respuesta','respuesta_alt') AND resultado IN ('ok','simulado')")
    dudosos = n("acciones", "hecho_en", "tipo='dm' AND resultado='dudoso'")
    errores = n("eventos", "creado_en", "tipo='error'")
    saltados = conn.execute(
        "SELECT COUNT(*) FROM comentarios WHERE estado LIKE 'saltado_%' AND actualizado_en >= ? AND actualizado_en < ?", rango
    ).fetchone()[0]
    pct = f"{100 * cerrados / (enviados + cerrados):.0f} %" if enviados + cerrados else "—"
    cola, historico, antiguedad, estimado = _datos_cola(conn, ahora)
    nivel = config.leer(conn, "nivel_ritmo")
    auto = conn.execute("SELECT detalle, creado_en FROM eventos WHERE tipo='autoprueba' ORDER BY id DESC LIMIT 1").fetchone()
    prueba = " (MODO DE PRUEBA: nada se envió de verdad)" if config.leer(conn, "modo_prueba") else ""

    momento_ayer = datetime.combine(ayer, time(12), tzinfo=ZONA)
    revisar_n = dudosos + errores
    asunto = redaccion.asunto(redaccion.RESUMEN, f"Ayer: {redaccion.plural(enviados, 'mensaje', 'mensajes')} y "
                              f"{redaccion.plural(publicas, 'respuesta', 'respuestas')}"
                              + (" · revisa algo" if revisar_n else "") + (" (modo de prueba)" if prueba else ""))
    if auto is None:
        revision = "todavía no se ha hecho"
    elif auto["detalle"].startswith("OK"):
        revision = "todo bien"
    else:
        revision = "falló (avisa a soporte)"
    cuerpo = (
        f"Resumen de ayer, {redaccion.fecha(momento_ayer)} (hora de Utah)\n"
        + ("⚠️ Estaba en MODO DE PRUEBA: no se envió nada de verdad.\n" if prueba else "")
        + "\n"
        f"Mensajes privados enviados: {enviados}\n"
        f"Respuestas en público: {publicas}\n"
        f"Personas con los mensajes cerrados: {cerrados}" + (f" ({pct} de los intentos)" if enviados + cerrados else "") + "\n"
        f"Comentarios nuevos: {detectados}\n"
        f"Saltados (equipo, ya atendidos, borrados u ofensivos): {saltados}\n"
        f"Para revisar a mano: {dudosos}" + (" → míralos en «Panel del bot» → Registro" if dudosos else "") + "\n"
        f"Errores del bot: {errores}\n\n"
        "Ahora mismo\n"
        + (f"Esperando respuesta: {redaccion.plural(cola, 'persona', 'personas')} (la que más, desde hace {antiguedad}); "
           f"atendidas en unos {estimado}.\n" if cola else "Esperando respuesta: nadie.\n")
        + (f"Comentarios de antes de instalar el bot, pendientes: {historico}.\n" if historico else "")
        + f"Velocidad: un mensaje cada {ritmo.VELOCIDADES[nivel]['cada']} ({ritmo.VELOCIDADES[nivel]['nota'].lower()}).\n"
        f"Revisión diaria de pantallas: {revision}.\n\n"
        "Las respuestas de la gente y los leads se ven en ManyChat y en el CRM (campaña «bot»).\n"
        "Si una mañana no te llega este resumen, el bot o la computadora están apagados."
    )
    return asunto, cuerpo


def revisar_resumen(conn: sqlite3.Connection, ahora: datetime) -> bool:
    local = ahora.astimezone(ZONA)
    if local.time() < HORA_RESUMEN:
        return False
    clave = f"resumen|{local.date().isoformat()}"
    if _ultima(conn, clave):
        return False
    asunto, cuerpo = texto_resumen(conn, ahora)
    return _enviar_registrado(conn, clave, "resumen", asunto, cuerpo, ahora)


def vigilar_una_vez(conn: sqlite3.Connection, ahora: datetime) -> None:
    """Lo que el proceso del panel revisa cada minuto (si el bot se cae, el panel sigue avisando)."""
    for revisar in (revisar_latido, revisar_cola, revisar_resumen):
        try:
            revisar(conn, ahora)
        except Exception:  # noqa: BLE001 — el vigilante no debe morir nunca
            log.exception("Fallo en %s", revisar.__name__)

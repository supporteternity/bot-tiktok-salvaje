"""Leads (PLAN-OFICIAL §17, D38–D39): la persona dio su número → se apunta y se pasa al equipo.

- `registrar`: guarda el lead (usuario, nombre, número, video y palabra clave de origen, fecha), pone la etapa C
  y avisa al momento: correo a `emails_leads` (o a los de alertas) y, si hay enlace, envío a GoHighLevel u otro
  sistema (`webhook_leads`, JSON por POST).
- `reintentar`: lo que no se pudo mandar (sin internet, sistema caído, correo roto) se reintenta como mucho 1 vez
  cada 10 min (el webhook, por lead; el correo, para todos a la vez: si falla, es el correo y no el lead).
- `avisar_equipo`: la persona pregunta algo que el bot no contesta (precios, quejas…) → correo al equipo.
- `marcar_hablado`: el equipo ya habló con la persona → etapa D y seguimiento «¿cómo te fue?» (D39).
"""

from __future__ import annotations

import json
import logging
import smtplib
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from bot import alertas, config, redaccion
from bot.ritmo import _fecha, iso
from bot.selectores import url_perfil, url_reel

log = logging.getLogger("bot.leads")

LEAD = "📞"
REINTENTO = timedelta(minutes=10)
CORREO_FALLO = "correo_lead_fallo"  # evento: el correo de un lead no salió (marca la espera de 10 min)
TIEMPO_WEBHOOK = 15  # segundos


def _destinatarios(conn: sqlite3.Connection) -> list[str]:
    return config.leer(conn, "emails_leads") or config.leer(conn, "emails_alerta")


def _evento(conn: sqlite3.Connection, tipo: str, detalle: str, ahora: datetime) -> None:
    with conn:
        conn.execute("INSERT INTO eventos (tipo, detalle, creado_en) VALUES (?, ?, ?)", (tipo, detalle, iso(ahora)))


def telefono_legible(telefono: str) -> str:
    """«8015551234» → «801 555 1234»; con prefijo de país se deja tal cual."""
    if len(telefono) == 10 and telefono.isdigit():
        return f"{telefono[:3]} {telefono[3:6]} {telefono[6:]}"
    return telefono


# ---------------------------------------------------------------- registrar


def _origen(conn: sqlite3.Connection, user_id: str) -> tuple[str | None, str | None]:
    """(reel, palabra clave) de donde vino la persona: el reel de su primer comentario (o el apuntado)."""
    fila = conn.execute(
        "SELECT COALESCE(k.reel_origen, (SELECT c.reel_id FROM comentarios c WHERE c.user_id = k.user_id "
        "ORDER BY c.comentado_en LIMIT 1)) AS reel FROM contactos k WHERE k.user_id = ?", (user_id,)).fetchone()
    reel = fila["reel"] if fila else None
    palabra = None
    if reel:
        r = conn.execute("SELECT palabra_clave FROM reels WHERE id = ?", (reel,)).fetchone()
        palabra = r["palabra_clave"] if r else None
    return reel, palabra


def registrar(conn: sqlite3.Connection, user_id: str, telefono: str, ahora: datetime) -> int:
    k = conn.execute("SELECT * FROM contactos WHERE user_id = ?", (user_id,)).fetchone()
    reel, palabra = _origen(conn, user_id)
    with conn:
        cursor = conn.execute(
            "INSERT INTO leads (user_id, usuario, nombre, telefono, reel_id, palabra_clave, creado_en) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", (user_id, k["usuario"], k["nombre"], telefono, reel, palabra, iso(ahora)))
        conn.execute("UPDATE contactos SET telefono = ?, etapa = CASE WHEN etapa = 'D' THEN 'D' ELSE 'C' END "
                     "WHERE user_id = ?", (telefono, user_id))
    lead_id = cursor.lastrowid
    log.info("Lead nuevo: @%s", k["usuario"])
    _evento(conn, "lead", f"@{k['usuario']} dejó su número", ahora)
    # También en modo de prueba: el número lo escribió de verdad una persona y el equipo tiene que llamarla.
    enviar(conn, lead_id, ahora)
    return lead_id


# ---------------------------------------------------------------- pasar al equipo


def datos(conn: sqlite3.Connection, lead_id: int) -> dict:
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    cuenta = config.leer(conn, "cuenta") or ""
    return {
        "usuario_tiktok": "@" + lead["usuario"], "perfil_tiktok": url_perfil(lead["usuario"]),
        "nombre": lead["nombre"] or "", "telefono": lead["telefono"],
        "video": url_reel(cuenta, lead["reel_id"]) if lead["reel_id"] and cuenta else (lead["reel_id"] or ""),
        "palabra_clave": lead["palabra_clave"] or "", "fecha": lead["creado_en"], "origen": "TikTok (bot)",
    }


def _correo(conn: sqlite3.Connection, lead_id: int, ahora: datetime) -> bool:
    d = datos(conn, lead_id)
    asunto = redaccion.asunto(LEAD, f"Lead nuevo: {d['usuario_tiktok']} · {telefono_legible(d['telefono'])}")
    cuerpo = (
        f"{d['usuario_tiktok']} dejó su número por mensaje privado. Ya le contestamos que el equipo le escribe por "
        "WhatsApp: escríbele lo antes posible.\n\n"
        f"Número: {telefono_legible(d['telefono'])}\n"
        f"Nombre en TikTok: {d['nombre'] or '—'}\n"
        f"Usuario de TikTok: {d['usuario_tiktok']} ({d['perfil_tiktok']})\n"
        f"Video donde comentó: {d['video'] or '—'}\n"
        f"Palabra clave del video: {d['palabra_clave'] or '—'}\n"
        f"Cuándo: {redaccion.cuando(_fecha(d['fecha']), ahora)}\n\n"
        "Cuando ya hayas hablado con la persona, márcalo en «Panel del bot» → Leads → «Ya hablé con la persona»: "
        "el bot le escribirá «¿cómo te fue?»."
    )
    try:
        return alertas._enviar(asunto, cuerpo, _destinatarios(conn))
    except (OSError, smtplib.SMTPException) as e:
        log.error("Correo del lead %s no enviado: %s", lead_id, e)
        # El fallo es del correo, no del lead: ningún correo de lead se reintenta hasta pasada la espera.
        _evento(conn, CORREO_FALLO, f"lead {lead_id}: {alertas.explicar_error(e)}"[:500], ahora)
        return False


def _correo_en_pausa(conn: sqlite3.Connection, ahora: datetime) -> bool:
    """Tras un fallo del correo, 10 min sin volver a intentarlo (auditoría 26 nov: antes se reintentaba en cada
    vuelta del bot y con cada lead; Gmail podía bloquear la cuenta y cada intento frenaba el bot hasta 30 s).
    Si después se guarda en el panel un correo que funciona, la espera se acaba."""
    return alertas.en_espera_por_fallo(conn, (CORREO_FALLO,), REINTENTO, ahora)


class _SinRedirecciones(urllib.request.HTTPRedirectHandler):
    """Una redirección NO se sigue: urllib la repetiría como GET y sin el lead, el destino contestaría 200 y el lead
    quedaría como entregado sin haber llegado (auditoría 26 nov). Se trata como error y se avisa del enlace."""

    def redirect_request(self, *_args, **_kwargs):
        return None


_ABRIR = urllib.request.build_opener(_SinRedirecciones).open


def _webhook(conn: sqlite3.Connection, lead_id: int, url: str) -> str | None:
    """POST con el JSON del lead. None si el sistema lo aceptó; si no, el motivo."""
    cuerpo = json.dumps(datos(conn, lead_id), ensure_ascii=False).encode()
    peticion = urllib.request.Request(url, data=cuerpo, method="POST",
                                      headers={"Content-Type": "application/json", "User-Agent": "bot-tiktok"})
    try:
        with _ABRIR(peticion, timeout=TIEMPO_WEBHOOK) as r:
            return None if 200 <= r.status < 300 else f"respondió {r.status}"
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            destino = e.headers.get("Location", "") if e.headers else ""
            return (f"el enlace redirige a otra dirección ({e.code} → {destino or '¿?'}): "
                    "pon en Ajustes el enlace final del webhook")
        return f"respondió {e.code}"
    except (urllib.error.URLError, OSError) as e:
        return f"no se pudo conectar ({getattr(e, 'reason', e)})"


def enviar(conn: sqlite3.Connection, lead_id: int, ahora: datetime, correo: bool = True, webhook: bool = True) -> None:
    """Correo y envío al sistema del equipo, lo que falte de los dos (`correo`/`webhook`: cuáles toca intentar)."""
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if correo and lead["correo_en"] is None and not _correo_en_pausa(conn, ahora) and _correo(conn, lead_id, ahora):
        with conn:
            conn.execute("UPDATE leads SET correo_en = ? WHERE id = ?", (iso(ahora), lead_id))
    url = config.leer(conn, "webhook_leads")
    if webhook and url and lead["webhook_en"] is None:
        error = _webhook(conn, lead_id, url)
        with conn:
            if error:
                conn.execute("UPDATE leads SET webhook_error = ? WHERE id = ?", (f"{iso(ahora)} {error}", lead_id))
            else:
                conn.execute("UPDATE leads SET webhook_en = ?, webhook_error = NULL WHERE id = ?", (iso(ahora), lead_id))
        if error:
            log.warning("Lead %s no llegó al sistema del equipo: %s", lead_id, error)


def reintentar(conn: sqlite3.Connection, ahora: datetime) -> None:
    """Lo pendiente de pasar al equipo (de las últimas 48 h). Cada parte con su espera de 10 min: el webhook por
    lead (`webhook_error`) y el correo para todos a la vez (si falla, es el correo y no el lead)."""
    url = config.leer(conn, "webhook_leads")
    desde = iso(ahora - timedelta(hours=48))
    for lead in conn.execute(
            "SELECT * FROM leads WHERE creado_en >= ? AND (correo_en IS NULL OR (? != '' AND webhook_en IS NULL))",
            (desde, url)).fetchall():
        falta_correo = (lead["correo_en"] is None and alertas.correo_configurado()
                        and not _correo_en_pausa(conn, ahora))
        ultimo = (lead["webhook_error"] or "").split(" ")[0]
        falta_webhook = bool(url) and lead["webhook_en"] is None and not (ultimo and ahora - _fecha(ultimo) < REINTENTO)
        if falta_correo or falta_webhook:
            enviar(conn, lead["id"], ahora, correo=falta_correo, webhook=falta_webhook)


# ---------------------------------------------------------------- pasar a una persona


def avisar_equipo(conn: sqlite3.Connection, user_id: str, motivo: str, ahora: datetime) -> None:
    k = conn.execute("SELECT usuario, nombre FROM contactos WHERE user_id = ?", (user_id,)).fetchone()
    _evento(conn, "pasado_a_persona", f"@{k['usuario']}: {motivo}", ahora)
    asunto = redaccion.asunto(redaccion.TE_NECESITA, f"Contesta a @{k['usuario']} por mensaje privado")
    cuerpo = (f"@{k['usuario']} ({k['nombre'] or 'sin nombre'}) {motivo}.\n\n"
              "El bot ya no le contesta: escríbele tú desde la app de TikTok (Mensajes).\n"
              f"Su perfil: {url_perfil(k['usuario'])}")
    try:
        alertas._enviar(asunto, cuerpo, _destinatarios(conn))
    except (OSError, smtplib.SMTPException) as e:
        log.error("Aviso al equipo no enviado: %s", e)


# ---------------------------------------------------------------- seguimiento (D39)


def marcar_hablado(conn: sqlite3.Connection, lead_id: int, vendedor: str, ahora: datetime) -> None:
    """El equipo ya habló con la persona: etapa D y seguimiento programado (una sola vez por lead)."""
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if lead is None or lead["hablado_en"]:
        return
    vendedor = vendedor.strip()[:60] or None
    listo = ahora + timedelta(hours=config.leer(conn, "seguimiento_espera_h"))
    with conn:
        conn.execute("UPDATE leads SET hablado_en = ?, vendedor = ? WHERE id = ?", (iso(ahora), vendedor, lead_id))
        conn.execute("UPDATE contactos SET etapa = 'D' WHERE user_id = ?", (lead["user_id"],))
        conn.execute("INSERT INTO envios (user_id, usuario, tipo, creado_en, listo_desde, actualizado_en) "
                     "VALUES (?, ?, 'seguimiento', ?, ?, ?)",
                     (lead["user_id"], lead["usuario"], iso(ahora), iso(listo), iso(ahora)))
    _evento(conn, "hablado", f"@{lead['usuario']}: el equipo ya habló" + (f" ({vendedor})" if vendedor else ""), ahora)

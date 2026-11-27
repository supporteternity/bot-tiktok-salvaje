"""Reglas de la cola (PLAN-OFICIAL §1, §6, D3, D17).

Orden de las reglas para un comentario en cola:
1. Propia cuenta o equipo            → saltado_propio
2. Persona ya atendida              → D35: respuesta pública según su etapa (`rec_pendiente`: B recordatorio,
                                      C/D «ya estamos en contacto», A «mándame msj»), como mucho una cada
                                      `recordatorio_cada_dias`; si no toca (o es ofensivo) → saltado_contactado
3. Más viejo que la caducidad       → caducado
4. Ofensivo: se marca (ofensivo = 1). En `solo_publicas` → saltado_ofensivo (no hay nada que hacer).
Orden (D42): `orden_cola` = videos_recientes (video más reciente primero y, dentro, más «me gusta») o llegada.
Además, si la misma persona tiene otro comentario a medias (enviando, reintento, respuesta pendiente)
se ESPERA: no se toca hasta que ese termine, para no atenderla dos veces.
"""

from __future__ import annotations

import sqlite3
import unicodedata
import re
from datetime import UTC, datetime, timedelta

from bot import config, db

ATENDER = "atender"
ESPERAR = "esperar"

# Estados en los que un comentario de la persona está "a medias".
EN_CURSO = ("dm_enviando", "dm_enviado", "dm_cerrado", "resp_pendiente", "alt_pendiente", "rec_pendiente", "error")
# Resultados que dejan a la persona con la info (etapa B).
CON_INFO = ("dm_enviado", "dudoso", "ya_hablado", "bienvenida")


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in sin_tildes if not unicodedata.combining(c)).casefold()


def _usuario(nombre: str) -> str:
    return nombre.strip().lstrip("@").casefold()


def es_propio(usuario: str, cuenta: str, equipo: list[str]) -> bool:
    u = _usuario(usuario)
    return bool(u) and (u == _usuario(cuenta) or u in {_usuario(e) for e in equipo})


def es_ofensivo(texto: str | None, palabras: list[str]) -> bool:
    if not texto:
        return False
    limpio = _normalizar(texto)
    return any(re.search(rf"(?<!\w){re.escape(_normalizar(p))}(?!\w)", limpio) for p in palabras if p.strip())


def _fecha(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _poner_estado(conn: sqlite3.Connection, cid: str, estado: str, ahora: datetime) -> None:
    with conn:
        conn.execute(
            "UPDATE comentarios SET estado = ?, actualizado_en = ? WHERE id = ?",
            (estado, ahora.isoformat(timespec="seconds"), cid),
        )


NO_ENTREGADO = "dm_no_entregado"  # TikTok no dejó pasar su privado (D47)
# Con estos resultados la persona sí recibió (o ya tenía) nuestro mensaje privado: se le puede volver a escribir.
LE_LLEGO = ("dm_enviado", "ya_hablado", "bienvenida")
# D49: entre un mensaje privado y el siguiente a la misma persona (por comentar en otro reel), como mínimo esto.
# Dos enlaces seguidos a la misma persona parecen spam y TikTok lo castiga.
ESPERA_OTRO_REEL = timedelta(hours=24)

# Freno de privados (27 nov). Visto con TikTok real (26 nov): tras varios privados rechazados («no se envió para
# proteger nuestra comunidad»), TikTok siguió rechazando todos los siguientes, y cada intento alargaba el castigo.
# Si los últimos NO_LLEGAN_SEGUIDOS privados a gente que comentó no llegaron, no se intenta ninguno durante
# PAUSA_PRIVADOS (contadas desde el último): se contesta solo en público y, si la persona escribe, recibe el enlace
# en esa conversación (D47). Pasado ese tiempo se prueba con uno: si llega, todo sigue normal; si no, otras 24 h.
NO_LLEGAN_SEGUIDOS = 3
PAUSA_PRIVADOS = timedelta(hours=24)


def privados_en_pausa(conn: sqlite3.Connection, ahora: datetime) -> datetime | None:
    """Hasta cuándo no se intentan privados a quien comenta (None = se pueden intentar)."""
    candidatas = []
    # 27 nov: TikTok contestó 7195 («no se envió para proteger nuestra comunidad»): pausa ya, desde ese rechazo.
    rechazo = conn.execute("SELECT creado_en FROM eventos WHERE tipo = 'privado_rechazado' ORDER BY id DESC LIMIT 1").fetchone()
    if rechazo:
        candidatas.append(_fecha(rechazo[0]) + PAUSA_PRIVADOS)
    filas = conn.execute(
        "SELECT resultado, hecho_en FROM acciones WHERE tipo = 'dm' AND comentario_id IS NOT NULL "
        "AND resultado IN ('ok', 'dudoso') ORDER BY id DESC LIMIT ?", (NO_LLEGAN_SEGUIDOS,)).fetchall()
    if len(filas) == NO_LLEGAN_SEGUIDOS and all(f[0] == "dudoso" for f in filas):
        candidatas.append(_fecha(filas[0][1]) + PAUSA_PRIVADOS)
    hasta = max(candidatas, default=None)
    return hasta if hasta and ahora < hasta else None


def reel_nuevo(conn: sqlite3.Connection, c: sqlite3.Row, ahora: datetime) -> bool:
    """D49 (pedido del usuario, 26 nov): quien ya recibió el enlace y comenta en un reel donde nunca había comentado
    vuelve a recibir el mensaje privado (en el mismo reel, solo el recordatorio público, D35). No si la lleva el
    equipo (dio su número o pidió una persona), si nunca le llegó un privado (cerrado / TikTok no lo dejó) ni si el
    último fue hace menos de ESPERA_OTRO_REEL (comentó en dos reels seguidos: un solo enlace)."""
    k = conn.execute("SELECT contactado, resultado, etapa, humano, contactado_en FROM contactos WHERE user_id = ?",
                     (c["user_id"],)).fetchone()
    if not k or not k["contactado"] or k["humano"] or k["etapa"] in ("C", "D"):
        return False
    if k["resultado"] == NO_ENTREGADO:
        # 27 nov (pedido del usuario): a quien TikTok no le dejó pasar el privado y comenta en otro reel se le vuelve a
        # intentar, sin esperar 24 h (nunca recibió el enlace: no son dos enlaces seguidos). Si tampoco llega, se le
        # contesta en público en ese reel; si los privados están en pausa (D50), solo en público.
        pass
    elif k["resultado"] not in LE_LLEGO:
        return False
    elif not k["contactado_en"] or _fecha(k["contactado_en"]) > ahora - ESPERA_OTRO_REEL:
        return False
    antes = conn.execute("SELECT 1 FROM comentarios WHERE user_id = ? AND reel_id = ? AND id != ? AND estado != 'en_cola' "
                         "LIMIT 1", (c["user_id"], c["reel_id"], c["id"])).fetchone()
    return antes is None


def evaluar(conn: sqlite3.Connection, cid: str, ahora: datetime) -> str:
    """Aplica las reglas a un comentario `en_cola`. Devuelve el estado final si se descarta,
    ATENDER si hay que procesarlo o ESPERAR si la persona tiene otro comentario a medias."""
    c = conn.execute(
        "SELECT c.*, p.usuario, p.contactado, p.etapa, p.resultado AS resultado_contacto, p.ultimo_recordatorio_en "
        "FROM comentarios c JOIN contactos p ON p.user_id = c.user_id WHERE c.id = ?",
        (cid,),
    ).fetchone()
    cfg = config.leer_todo(conn)

    if es_propio(c["usuario"], cfg["cuenta"], cfg["cuentas_equipo"]):
        _poner_estado(conn, cid, "saltado_propio", ahora)
        return "saltado_propio"

    ofensivo = es_ofensivo(c["texto"], cfg["palabras_ofensivas"])
    if ofensivo and not c["ofensivo"]:
        with conn:
            conn.execute("UPDATE comentarios SET ofensivo = 1 WHERE id = ?", (cid,))
    caducado = cfg["caducidad_dias"] > 0 and _fecha(c["comentado_en"]) < ahora - timedelta(days=cfg["caducidad_dias"])

    if c["contactado"] and not (reel_nuevo(conn, c, ahora) and cfg["modo_publica"] != "solo_publicas"):
        return _vuelve_a_comentar(conn, c, ofensivo or caducado, cfg, ahora)

    if caducado:
        _poner_estado(conn, cid, "caducado", ahora)
        return "caducado"

    if ofensivo and cfg["modo_publica"] == "solo_publicas":
        _poner_estado(conn, cid, "saltado_ofensivo", ahora)
        return "saltado_ofensivo"

    otro_a_medias = conn.execute(
        f"SELECT 1 FROM comentarios WHERE user_id = ? AND id != ? AND estado IN ({','.join('?' * len(EN_CURSO))}) LIMIT 1",
        (c["user_id"], cid, *EN_CURSO),
    ).fetchone()
    if otro_a_medias:
        return ESPERAR

    return ATENDER


def tipo_recordatorio(etapa: str, resultado: str | None) -> str:
    """D35: qué respuesta pública le toca a quien ya fue atendido y vuelve a comentar."""
    if etapa in ("C", "D"):
        return "ya_en_contacto"
    if etapa == "B" or resultado in CON_INFO:
        return "recordatorio"
    return "respuesta_alt"  # no se le pudo escribir: otra vez «mándame msj»


def _vuelve_a_comentar(conn: sqlite3.Connection, c: sqlite3.Row, sin_publica: bool, cfg: dict, ahora: datetime) -> str:
    dias = cfg["recordatorio_cada_dias"]
    reciente = c["ultimo_recordatorio_en"] and _fecha(c["ultimo_recordatorio_en"]) > ahora - timedelta(days=dias)
    tipo = tipo_recordatorio(c["etapa"], c["resultado_contacto"])
    hay_textos = conn.execute("SELECT 1 FROM textos WHERE tipo = ? AND activo = 1 LIMIT 1", (tipo,)).fetchone()
    if sin_publica or dias <= 0 or reciente or not hay_textos or cfg["modo_publica"] == "solo_publicas" and tipo == "respuesta_alt":
        _poner_estado(conn, c["id"], "saltado_contactado", ahora)
        return "saltado_contactado"
    otro_a_medias = conn.execute(
        f"SELECT 1 FROM comentarios WHERE user_id = ? AND id != ? AND estado IN ({','.join('?' * len(EN_CURSO))}) LIMIT 1",
        (c["user_id"], c["id"], *EN_CURSO),
    ).fetchone()
    if otro_a_medias:
        _poner_estado(conn, c["id"], "saltado_contactado", ahora)
        return "saltado_contactado"
    with conn:
        conn.execute("UPDATE contactos SET ultimo_recordatorio_en = ? WHERE user_id = ?",
                     (ahora.isoformat(timespec="seconds"), c["user_id"]))
        conn.execute("UPDATE comentarios SET estado = 'rec_pendiente', intentos = 0, actualizado_en = ? WHERE id = ?",
                     (ahora.isoformat(timespec="seconds"), c["id"]))
    return "rec_pendiente"


def siguiente(conn: sqlite3.Connection, ahora: datetime, sin_mirar_desde: datetime | None = None) -> str | None:
    """El comentario en cola más antiguo que haya que atender (aplicando las reglas a los que va
    descartando por el camino). None si no queda ninguno.
    `sin_mirar_desde`: con el cupo general lleno solo se escribe a quien sigue la cuenta; se saltan las personas
    que ya se miraron desde ese momento y no la seguían (siguen en la cola, en su sitio)."""
    sql = ("SELECT c.id FROM comentarios c JOIN contactos k ON k.user_id = c.user_id LEFT JOIN reels r ON r.id = c.reel_id "
           "WHERE c.estado = 'en_cola'")
    params: tuple = ()
    if sin_mirar_desde is not None:
        sql += " AND NOT (COALESCE(k.te_sigue, 0) = 0 AND k.sigue_mirado_en IS NOT NULL AND k.sigue_mirado_en > ?)"
        params = (sin_mirar_desde.isoformat(timespec="seconds"),)
    if config.leer(conn, "orden_cola") == "videos_recientes":  # D42, como en el instructivo
        orden = " ORDER BY COALESCE(r.publicado_en, '') DESC, c.reel_id DESC, c.likes DESC, c.comentado_en, c.id"
    else:
        orden = " ORDER BY c.comentado_en, c.id"
    candidatos = [f["id"] for f in conn.execute(sql + orden, params)]
    for cid in candidatos:
        if evaluar(conn, cid, ahora) == ATENDER:
            return cid
    return None


TABLAS_DE_PERSONA = ("comentarios", "mensajes", "envios", "leads")


def unir_contactos(conn: sqlite3.Connection, viejo: str, nuevo: str) -> None:
    """La misma persona guardada con dos IDs (p. ej. 'u:usuario' de la bandeja y el ID estable de TikTok, §5):
    todo pasa a `nuevo` sin perder nada (auditoría 26 nov). Si ya fue atendida, la etapa más avanzada, el número,
    que la lleva el equipo… se conservan; sus comentarios, mensajes, envíos y leads se van con ella.
    No abre transacción: la pone quien llama."""
    if viejo == nuevo:
        return
    v = conn.execute("SELECT * FROM contactos WHERE user_id = ?", (viejo,)).fetchone()
    if v is None:
        return
    n = conn.execute("SELECT * FROM contactos WHERE user_id = ?", (nuevo,)).fetchone()
    if n is None:
        columnas = ", ".join(f[1] for f in conn.execute("PRAGMA table_info(contactos)") if f[1] != "user_id")
        conn.execute(f"INSERT INTO contactos (user_id, {columnas}) SELECT ?, {columnas} FROM contactos WHERE user_id = ?",
                     (nuevo, viejo))
    else:
        uno = {k: n[k] for k in n.keys() if k not in ("user_id", "usuario")}
        if v["contactado"] and not n["contactado"]:
            uno.update(contactado=1, resultado=v["resultado"], contactado_en=v["contactado_en"])
        if v["humano"] and not n["humano"]:
            uno.update(humano=1, humano_motivo=v["humano_motivo"])
        uno["etapa"] = max(n["etapa"] or "A", v["etapa"] or "A")  # A < B < C < D
        uno["respuestas_auto"] = (n["respuestas_auto"] or 0) + (v["respuestas_auto"] or 0)
        uno["es_equipo"] = max(n["es_equipo"] or 0, v["es_equipo"] or 0)
        for k in ("nombre", "telefono", "origen", "reel_origen"):
            uno[k] = n[k] if n[k] is not None else v[k]
        uno["ultimo_recordatorio_en"] = max(filter(None, (n["ultimo_recordatorio_en"], v["ultimo_recordatorio_en"])),
                                            default=None)
        if (v["sigue_mirado_en"] or "") > (n["sigue_mirado_en"] or ""):
            uno.update(te_sigue=v["te_sigue"], sigue_mirado_en=v["sigue_mirado_en"])
        conn.execute(f"UPDATE contactos SET {', '.join(f'{k} = ?' for k in uno)} WHERE user_id = ?",
                     (*uno.values(), nuevo))
    for tabla in TABLAS_DE_PERSONA:
        conn.execute(f"UPDATE {tabla} SET user_id = ? WHERE user_id = ?", (nuevo, viejo))
    conn.execute("DELETE FROM contactos WHERE user_id = ?", (viejo,))


def marcar_contactado(conn: sqlite3.Connection, user_id: str, resultado: str, ahora: datetime,
                      reel_id: str | None = None) -> None:
    """La persona queda atendida (§6): sus otros comentarios en cola ya no reciben el enlace (D35: como mucho un
    recordatorio público). Con la info (CON_INFO) pasa a la etapa B; se apunta de qué reel vino."""
    marca = ahora.isoformat(timespec="seconds")
    with db.junto(conn):  # dentro del grupo de cambios del ejecutor, si lo hay (hallazgo 20)
        conn.execute(
            "UPDATE contactos SET contactado = 1, resultado = ?, contactado_en = ?, ultimo_recordatorio_en = ?, "
            "reel_origen = COALESCE(reel_origen, ?), origen = COALESCE(origen, CASE WHEN ? IS NULL THEN NULL "
            "ELSE 'comentario' END), etapa = CASE WHEN etapa = 'A' AND ? THEN 'B' ELSE etapa END WHERE user_id = ?",
            (resultado, marca, marca, reel_id, reel_id, int(resultado in CON_INFO), user_id),
        )


def reponer_simulados(conn: sqlite3.Connection, ahora: datetime) -> int:
    """Al quitar el modo de prueba: quien fue «atendido» solo en prueba (nada se le envió de verdad) vuelve a la
    cola para recibir su mensaje real. Sin esto, la regla de «una vez por persona» lo saltaría para siempre.
    No toca a nadie que ya tenga alguna acción real (enviada o dudosa). Devuelve cuántos vuelven (comentarios y,
    desde la auditoría del 26 nov, también los enlaces y bienvenidas de la bandeja que solo salieron en simulado)."""
    reales = "SELECT comentario_id FROM acciones WHERE resultado IN ('ok', 'dudoso') AND comentario_id IS NOT NULL"
    filas = conn.execute(
        "SELECT DISTINCT c.id, c.user_id FROM acciones a JOIN comentarios c ON c.id = a.comentario_id "
        "WHERE a.resultado = 'simulado' AND a.tipo IN ('dm', 'respuesta', 'respuesta_alt') "
        f"AND c.id NOT IN ({reales})"
    ).fetchall()
    con_real = {u for (u,) in conn.execute(
        f"SELECT DISTINCT user_id FROM comentarios WHERE id IN ({reales})")}
    con_real |= {u for (u,) in conn.execute(  # también quien recibió algo de verdad desde la bandeja
        "SELECT DISTINCT user_id FROM envios WHERE estado IN ('enviado', 'dudoso', 'enviando')")}
    simulados = conn.execute("SELECT id, user_id, tipo FROM envios WHERE estado = 'simulado' ORDER BY id").fetchall()
    marca = ahora.isoformat(timespec="seconds")
    repuestos = 0

    def desatender(user_id: str) -> None:
        conn.execute("UPDATE contactos SET contactado = 0, resultado = NULL, contactado_en = NULL, etapa = 'A', "
                     "ultimo_recordatorio_en = NULL WHERE user_id = ? AND etapa = 'B'", (user_id,))
        conn.execute("UPDATE contactos SET contactado = 0, resultado = NULL, contactado_en = NULL WHERE user_id = ?",
                     (user_id,))

    with conn:
        for cid, user_id in filas:
            if user_id in con_real:
                continue
            conn.execute("UPDATE comentarios SET estado = 'en_cola', intentos = 0, ultimo_error = NULL, actualizado_en = ? "
                         "WHERE id = ?", (marca, cid))
            desatender(user_id)
            repuestos += 1
        # Enlace o bienvenida de la bandeja que solo salió en simulado: vuelve a salir, esta vez de verdad (con
        # solo_si_nuevo, D36). Las respuestas de conversación simuladas no se repiten fuera de contexto.
        for eid, user_id, tipo in simulados:
            if tipo in ("dm", "bienvenida") and user_id not in con_real:
                conn.execute("UPDATE envios SET estado = 'pendiente', intentos = 0, ultimo_error = NULL, listo_desde = ?, "
                             "actualizado_en = ? WHERE id = ?", (marca, marca, eid))
                desatender(user_id)
                repuestos += 1
            else:
                conn.execute("UPDATE envios SET estado = 'simulado_repuesto', actualizado_en = ? WHERE id = ?", (marca, eid))
        # Quedan en el historial, pero ya no cuentan como hechos ni se vuelven a reponer.
        conn.execute("UPDATE acciones SET resultado = 'simulado_repuesto' WHERE resultado = 'simulado' "
                     "AND tipo IN ('dm', 'respuesta', 'respuesta_alt', 'chat', 'bienvenida')")
    return repuestos

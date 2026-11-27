"""Control de ritmo (PLAN-OFICIAL §7.1–7.2).

Decide si AHORA se puede hacer una acción en TikTok y, si no, hasta cuándo esperar:
- Presupuesto por hora (últimos 60 min) y por día (últimas 24 h) según el nivel, contado desde `acciones`.
- Topes por tipo: mensajes y respuestas no pueden comerse más del 75 % del presupuesto horario cada uno.
- Pausa aleatoria entre acciones (media 3600/acciones_h, ±50 %, nunca menos de `pausa_min_seg`).
- Descanso largo cada 12–20 acciones: 10–30 min en el nivel 1, proporcional al ritmo en los demás
  (mismo % de tiempo de descanso en todos los niveles, mínimo 3 min; D20) y descanso diario de 4 h que empieza entre 02:00 y 04:00 (Utah).
- Lecturas: cada 6–12 min y como mucho `tope_lecturas_hora`.
- Subida de nivel: se PROPONE tras 3 días sin avisos; la confirma una persona (§7.2).

`dm` con resultado `cerrado` no cuenta como acción (no se envió nada; la acción es la respuesta alternativa).
"""

from __future__ import annotations

import math
import random
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from bot import config

NIVELES: dict[int, tuple[int, int]] = {  # nivel: (acciones por hora, tope por día)
    1: (8, 120),
    2: (15, 220),
    3: (25, 380),
    4: (40, 600),
    5: (60, 900),
    6: (90, 1300),
}
# D31: el cliente elige la velocidad en el panel, en minutos (cada nivel es una velocidad).
# Medido con simulación (20 comentarios en 10 min, solo mensajes privados): entre un mensaje y otro, y total.
VELOCIDADES: dict[int, dict[str, str]] = {
    1: {"cada": "~9 min", "veinte": "~3 h 20 min", "nota": "La más segura"},
    2: {"cada": "~4 min", "veinte": "~1 h 50 min", "nota": "Muy segura"},
    3: {"cada": "~2,5 min", "veinte": "~1 h 10 min", "nota": "Recomendada"},
    4: {"cada": "~1,5 min", "veinte": "~40 min", "nota": "Riesgo alto"},
    5: {"cada": "~1 min", "veinte": "~28 min", "nota": "Riesgo alto"},
    6: {"cada": "~40 s", "veinte": "~21 min", "nota": "Riesgo muy alto"},
}
VELOCIDAD_RECOMENDADA = 3
RIESGO_DESDE = 4  # desde aquí hay que marcar «Entiendo el riesgo»
# Máximo de mensajes privados al día (buenas prácticas investigadas el 25 nov: TikTok vigila sobre todo el total
# diario de mensajes a quien no te sigue; referencias de 40–60/día en cuentas establecidas y 60–100 en cuentas
# con mucha confianza). Por encima del recomendado hay que marcar «Entiendo el riesgo».
TOPES_DM_DIA = (40, 60, 100, 150, 250)
TOPE_DM_RECOMENDADO = 100
# Cupo APARTE para quien sigue la cuenta (el mensaje le llega a la bandeja normal, no a «Solicitudes»).
TOPES_SEGUIDORES_DIA = (0, 50, 100, 200)
TOPE_SEGUIDORES_RECOMENDADO = 100
# «perfil» = mirar si alguien sigue la cuenta cuando el cupo general está lleno: también va al ritmo elegido.
# §17 (D45): «chat» = respuesta dentro de la conversación; «aceptar» = aceptar una solicitud de mensajes.
TIPOS_ACCION = ("dm", "respuesta", "respuesta_alt", "fijar", "perfil", "recordatorio", "ya_en_contacto", "chat",
                "seguimiento", "bienvenida", "aceptar")
TIPOS_RESPUESTA = ("respuesta", "respuesta_alt", "recordatorio", "ya_en_contacto")
# Lecturas (no escriben nada): cada una con su intervalo en config.
LECTURAS = {"lectura": "lectura_cada_min", "bandeja": "bandeja_cada_min", "seguidores": "seguidores_cada_min"}
DESCANSO_MINIMO_MIN = 3  # D20
ZONA = ZoneInfo(config.ZONA_HORARIA)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _fecha(texto: str) -> datetime:
    dt = datetime.fromisoformat(texto)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Decision:
    ok: bool
    hasta: datetime | None = None  # si no se puede, a partir de cuándo volver a preguntar
    motivo: str = ""


class Ritmo:
    def __init__(self, conn: sqlite3.Connection, rng: random.Random | None = None):
        self.conn = conn
        self.rng = rng or random.Random()
        self.proxima_accion: datetime | None = None
        self.proxima_lectura: datetime | None = None
        self.proximas: dict[str, datetime] = {}  # bandeja y seguidores
        self.descanso_hasta: datetime | None = None
        self.contador = 0
        self.objetivo = self._nuevo_objetivo()

    # ------------------------------------------------------------ datos

    def _cfg(self) -> dict:
        return config.leer_todo(self.conn)

    def _contar(self, desde: datetime, tipos: tuple[str, ...]) -> int:
        marcas = ",".join("?" * len(tipos))
        return self.conn.execute(
            f"SELECT COUNT(*) FROM acciones WHERE hecho_en > ? AND tipo IN ({marcas}) AND resultado != 'cerrado'",
            (iso(desde), *tipos),
        ).fetchone()[0]

    def _ultima(self, tipos: tuple[str, ...]) -> datetime | None:
        marcas = ",".join("?" * len(tipos))
        fila = self.conn.execute(
            f"SELECT MAX(hecho_en) FROM acciones WHERE tipo IN ({marcas}) AND resultado != 'cerrado'", tipos
        ).fetchone()
        return _fecha(fila[0]) if fila and fila[0] else None

    def _primera_desde(self, desde: datetime, tipos: tuple[str, ...]) -> datetime | None:
        marcas = ",".join("?" * len(tipos))
        fila = self.conn.execute(
            f"SELECT MIN(hecho_en) FROM acciones WHERE hecho_en > ? AND tipo IN ({marcas}) AND resultado != 'cerrado'",
            (iso(desde), *tipos),
        ).fetchone()
        return _fecha(fila[0]) if fila and fila[0] else None

    def _nuevo_objetivo(self) -> int:
        lo, hi = self._cfg()["descanso_cada"]
        return self.rng.randint(lo, hi)

    # ------------------------------------------------------------ descanso diario

    def descanso_diario(self, ahora: datetime, dias_antes: int = 0) -> tuple[datetime, datetime]:
        """Ventana de descanso del día (hora de Utah) de `ahora` (o de `dias_antes` días antes). Estable para el mismo
        día aunque se reinicie. Auditoría 26 nov: el rango de inicio puede cruzar la medianoche («entre las 23:00 y la
        01:00») y la duración se suma en hora real, así que también dura lo mismo los días de cambio de hora."""
        cfg = self._cfg()
        dia = ahora.astimezone(ZONA).date() - timedelta(days=dias_antes)
        semilla = random.Random(f"{dia.isoformat()}|{cfg['inicio_desde']}")
        h_ini, h_fin = (time.fromisoformat(h) for h in cfg["descanso_diario_inicio"])
        margen = (datetime.combine(dia, h_fin) - datetime.combine(dia, h_ini)).total_seconds()
        if margen < 0:
            margen += 24 * 3600  # «desde las 23:00 hasta la 01:00»: el rango pasa al día siguiente
        # Hora de reloj para elegir el inicio dentro del rango; la duración, en tiempo real (UTC).
        inicio = (datetime.combine(dia, h_ini) + timedelta(seconds=semilla.uniform(0, margen))).replace(tzinfo=ZONA)
        inicio = inicio.astimezone(UTC)
        return inicio, inicio + timedelta(hours=cfg["descanso_diario_horas"])

    def descansando(self, ahora: datetime) -> Decision | None:
        """Si ahora toca descanso (diario o largo), la decisión con hasta cuándo; si no, None.
        Se mira el descanso de hoy y el de ayer: el de ayer puede seguir pasada la medianoche (empieza a las 23:00)."""
        for dias_antes in (0, 1):
            inicio, fin = self.descanso_diario(ahora, dias_antes)
            if inicio <= ahora < fin:
                return Decision(False, fin, "descanso_diario")
        if self.descanso_hasta and ahora < self.descanso_hasta:
            return Decision(False, self.descanso_hasta, "descanso")
        return None

    # ------------------------------------------------------------ decisiones

    def puede(self, tipo: str, ahora: datetime) -> Decision:
        """¿Se puede hacer ahora una acción de `tipo` (dm, respuesta, respuesta_alt, fijar, lectura)?"""
        if (d := self.descansando(ahora)) is not None:
            return d
        if tipo == "lectura":
            return self._puede_leer(ahora)
        if tipo in LECTURAS:
            return self._puede_otra_lectura(tipo, ahora)

        cfg = self._cfg()
        por_hora, por_dia = NIVELES[cfg["nivel_ritmo"]]
        hace_1h = ahora - timedelta(hours=1)

        if self.proxima_accion is None:  # recién arrancado: respeta la última acción guardada
            ultima = self._ultima(TIPOS_ACCION)
            media = 3600 / por_hora
            minimo = max(cfg["pausa_min_seg"], media * (1 - cfg["intervalo_variacion"]))
            self.proxima_accion = ultima + timedelta(seconds=minimo) if ultima else ahora
        if ahora < self.proxima_accion:
            return Decision(False, self.proxima_accion, "pausa")

        if self._contar(ahora - timedelta(hours=24), TIPOS_ACCION) >= por_dia:
            primera = self._primera_desde(ahora - timedelta(hours=24), TIPOS_ACCION)
            return Decision(False, (primera or ahora) + timedelta(hours=24, seconds=1), "tope_dia")

        if self._contar(hace_1h, TIPOS_ACCION) >= por_hora:
            primera = self._primera_desde(hace_1h, TIPOS_ACCION)
            return Decision(False, (primera or ahora) + timedelta(hours=1, seconds=1), "tope_hora")

        if tipo == "dm":  # sin cupo diario para nadie (ni no seguidores ni seguidores): no se puede escribir
            general, seguidores = self.cupo_dm(ahora, False), self.cupo_dm(ahora, True)
            if not general.ok and not seguidores.ok:
                return Decision(False, min(general.hasta, seguidores.hasta), "tope_dm_dia")
        if tipo == "bienvenida":  # D40: la bienvenida gasta el cupo de quien sigue la cuenta
            seguidores = self.cupo_dm(ahora, True)
            if not seguidores.ok:
                return seguidores

        grupo, fraccion = None, None
        if tipo == "dm":
            grupo, fraccion = ("dm",), cfg["tope_dm_hora"]
        elif tipo in TIPOS_RESPUESTA:
            grupo, fraccion = TIPOS_RESPUESTA, cfg["tope_resp_hora"]
        if grupo and self._contar(hace_1h, grupo) >= max(1, math.floor(por_hora * fraccion)):
            primera = self._primera_desde(hace_1h, grupo)
            return Decision(False, (primera or ahora) + timedelta(hours=1, seconds=1), f"tope_{tipo}")

        return Decision(True)

    def cupo_dm(self, ahora: datetime, seguidor: bool) -> Decision:
        """¿Queda cupo diario de mensajes privados? Dos cupos: quien sigue la cuenta y los demás."""
        cfg = self._cfg()
        tope = cfg["tope_dm_seguidores_dia"] if seguidor else cfg["tope_dm_dia"]
        desde = iso(ahora - timedelta(hours=24))
        filas = self.conn.execute(
            "SELECT hecho_en FROM acciones WHERE tipo IN ('dm', 'bienvenida') AND resultado != 'cerrado' AND seguidor = ? "
            "AND hecho_en > ? "
            "ORDER BY hecho_en", (int(seguidor), desde)).fetchall()
        if len(filas) < tope:
            return Decision(True)
        primera = _fecha(filas[0][0]) if filas else ahora
        return Decision(False, primera + timedelta(hours=24, seconds=1), "tope_dm_dia")

    def _puede_leer(self, ahora: datetime) -> Decision:
        cfg = self._cfg()
        if self.proxima_lectura is None:
            ultima = self._ultima(("lectura",))
            self.proxima_lectura = ultima + timedelta(minutes=cfg["lectura_cada_min"][0]) if ultima else ahora
        if ahora < self.proxima_lectura:
            return Decision(False, self.proxima_lectura, "pausa_lectura")
        hace_1h = ahora - timedelta(hours=1)
        if self._contar(hace_1h, ("lectura",)) >= cfg["tope_lecturas_hora"]:
            primera = self._primera_desde(hace_1h, ("lectura",))
            return Decision(False, (primera or ahora) + timedelta(hours=1, seconds=1), "tope_lecturas")
        return Decision(True)

    def _puede_otra_lectura(self, tipo: str, ahora: datetime) -> Decision:
        if tipo not in self.proximas:
            ultima = self._ultima((tipo,))
            self.proximas[tipo] = ultima + timedelta(minutes=self._cfg()[LECTURAS[tipo]][0]) if ultima else ahora
        if ahora < self.proximas[tipo]:
            return Decision(False, self.proximas[tipo], f"pausa_{tipo}")
        return Decision(True)

    def hay_actividad(self, ahora: datetime) -> bool:
        """¿Conviene mirar Studio más a menudo? Comentarios nuevos hace poco o un reel recién publicado."""
        cfg = self._cfg()
        desde = iso(ahora - timedelta(minutes=cfg["actividad_min"]))
        if self.conn.execute("SELECT 1 FROM comentarios WHERE comentado_en >= ? LIMIT 1", (desde,)).fetchone():
            return True
        reciente = iso(ahora - timedelta(hours=cfg["reel_reciente_h"]))
        return self.conn.execute(
            "SELECT 1 FROM reels WHERE activo = 1 AND publicado_en >= ? LIMIT 1", (reciente,)).fetchone() is not None

    def hecho(self, tipo: str, ahora: datetime) -> None:
        """Avisar después de hacer una acción (ya guardada en `acciones`): planifica la siguiente pausa."""
        cfg = self._cfg()
        if tipo == "lectura":
            lo, hi = cfg["lectura_activa_min"] if self.hay_actividad(ahora) else cfg["lectura_cada_min"]
            self.proxima_lectura = ahora + timedelta(minutes=self.rng.uniform(lo, hi))
            return
        if tipo in LECTURAS:
            lo, hi = cfg[LECTURAS[tipo]]
            self.proximas[tipo] = ahora + timedelta(minutes=self.rng.uniform(lo, hi))
            return
        por_hora = NIVELES[cfg["nivel_ritmo"]][0]
        media = 3600 / por_hora
        var = cfg["intervalo_variacion"]
        pausa = max(cfg["pausa_min_seg"], media * self.rng.uniform(1 - var, 1 + var))
        self.proxima_accion = ahora + timedelta(seconds=pausa)
        self.contador += 1
        if self.contador >= self.objetivo:
            # D20: el descanso escala con el ritmo para ocupar el mismo % del tiempo en todos los niveles.
            lo, hi = cfg["descanso_duracion_min"]
            factor = NIVELES[1][0] / por_hora
            minutos = max(DESCANSO_MINIMO_MIN, self.rng.uniform(lo, hi) * factor)
            self.descanso_hasta = ahora + timedelta(minutes=minutos)
            self.contador = 0
            self.objetivo = self._nuevo_objetivo()


# ---------------------------------------------------------------- niveles


def cambiar_nivel(conn: sqlite3.Connection, nuevo: int, ahora: datetime, motivo: str) -> None:
    config.guardar(conn, "nivel_ritmo", nuevo)
    with conn:
        conn.execute("UPDATE estado_bot SET nivel_ritmo = ? WHERE id = 1", (nuevo,))
        conn.execute(
            "INSERT INTO eventos (tipo, detalle, creado_en) VALUES ('cambio_nivel', ?, ?)",
            (f"Nivel {nuevo}: {motivo}", iso(ahora)),
        )


def elegir_velocidad(conn: sqlite3.Connection, nivel: int, acepta_riesgo: bool, ahora: datetime,
                     tope_dm_dia: int | None = None, tope_seguidores: int | None = None) -> str | None:
    """La persona elige la velocidad en el panel (D31). Devuelve el error para mostrar, o None si se guardó.
    Las velocidades de riesgo alto solo se guardan si marcó «Entiendo el riesgo» (queda en el registro)."""
    if nivel not in NIVELES:
        return "Esa velocidad no existe."
    if tope_dm_dia is not None and tope_dm_dia not in TOPES_DM_DIA:
        return "Ese máximo no existe."
    if tope_seguidores is not None and tope_seguidores not in TOPES_SEGUIDORES_DIA:
        return "Ese máximo no existe."
    riesgo_tope = tope_dm_dia is not None and tope_dm_dia > TOPE_DM_RECOMENDADO
    riesgo_seg = tope_seguidores is not None and tope_seguidores > TOPE_SEGUIDORES_RECOMENDADO
    if (nivel >= RIESGO_DESDE or riesgo_tope or riesgo_seg) and not acepta_riesgo:
        return "Para elegir una opción de riesgo alto, marca «Entiendo el riesgo»."
    if tope_dm_dia is not None and tope_dm_dia != config.leer(conn, "tope_dm_dia"):
        config.guardar(conn, "tope_dm_dia", tope_dm_dia)
        with conn:
            conn.execute("INSERT INTO eventos (tipo, detalle, creado_en) VALUES ('cambio_nivel', ?, ?)",
                         (f"Máximo de mensajes al día: {tope_dm_dia}" + (" (riesgo aceptado)" if riesgo_tope else ""),
                          iso(ahora)))
    if tope_seguidores is not None and tope_seguidores != config.leer(conn, "tope_dm_seguidores_dia"):
        config.guardar(conn, "tope_dm_seguidores_dia", tope_seguidores)
        with conn:
            conn.execute("INSERT INTO eventos (tipo, detalle, creado_en) VALUES ('cambio_nivel', ?, ?)",
                         (f"Mensajes extra al día a quien sigue la cuenta: {tope_seguidores}"
                          + (" (riesgo aceptado)" if riesgo_seg else ""), iso(ahora)))
    if nivel != config.leer(conn, "nivel_ritmo"):
        v = VELOCIDADES[nivel]
        cambiar_nivel(conn, nivel, ahora, f"velocidad elegida en el panel: cada {v['cada']}"
                      + (" (riesgo aceptado)" if nivel >= RIESGO_DESDE else ""))
    return None

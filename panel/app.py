"""Panel web local (PLAN-OFICIAL §10). Solo en http://127.0.0.1:8765, con contraseña (su resumen, PANEL_CONTRASENA en .env).

El panel y el bot son procesos separados que solo comparten bot.db: lo que se cambia aquí
(textos, ajustes, pausar/reanudar) lo usa el bot en su siguiente vuelta.
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import secrets
import sqlite3
import sys
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import re

from bot import alertas, config, db, reglas, ritmo, textos, cuenta, navegador, instalador
from bot.freno import Freno
from bot.ritmo import NIVELES, ZONA, _fecha, iso

AQUI = Path(__file__).resolve().parent
plantillas = Jinja2Templates(directory=str(AQUI / "plantillas"))
# «?v=…» en los enlaces a estilo.css y panel.js: cambia cuando cambian los archivos, así el navegador
# no se queda con una versión vieja guardada tras una actualización.
plantillas.env.globals["version_estaticos"] = str(max(int(f.stat().st_mtime) for f in (AQUI / "estaticos").rglob("*.*")))
COOKIE = "panel_bot"
HOST, PUERTO = "127.0.0.1", 8765
# Auditoría 26 nov (hallazgos 10 y 13). El panel solo atiende peticiones dirigidas a este ordenador por su nombre
# local: una web que haga que su dominio apunte a 127.0.0.1 («DNS rebinding») llega con su nombre y se rechaza.
NOMBRES_LOCALES = {"127.0.0.1", "localhost", "::1"}
METODOS_SIN_CAMBIOS = {"GET", "HEAD", "OPTIONS"}
FALLOS_ANTES_DE_ESPERAR = 5  # contraseñas falsas seguidas antes de hacer esperar
ESPERA_FALLOS_SEG, ESPERA_FALLOS_MAX_SEG = 60, 900  # 1 min, y se dobla con cada fallo más (máximo 15 min)
_reloj = monotonic
# Auditoría 26 nov (hallazgo 19): la sesión del navegador caduca; pasado este tiempo el panel vuelve a pedir la contraseña.
DIAS_SESION = 30

ESTADOS_FINALES = (
    "completado", "completado_cerrado", "completado_publica", "completado_recordatorio", "saltado_propio",
    "saltado_contactado",
    "saltado_no_disponible", "saltado_ofensivo", "caducado", "dudoso", "error_final",
)
# Comentarios cuya persona aún no ha recibido su mensaje (Inicio: «N personas esperando su mensaje»).
SIN_MENSAJE = ("en_cola", "dm_enviando", "error")
PENDIENTES = "pendientes"  # filtro del Registro que muestra justo esa lista
# «Necesitan atención» (pedido del usuario, 27 nov): a quien el bot no pudo escribir por privado (TikTok no lo dejó
# pasar, mensajes cerrados, solo público, caducado…) o no pudo terminar (error_final, dudoso). Para actuar rápido.
ATENDER = "atender"
_LE_LLEGO = "(" + ", ".join(f"'{r}'" for r in reglas.LE_LLEGO) + ")"
def _sql_atender(c: str = "c") -> str:
    return (f"{c}.estado NOT IN ('saltado_propio', 'saltado_ofensivo', 'saltado_no_disponible', 'en_cola', "
            f"'dm_enviando', 'error', 'historico') AND ({c}.estado IN ('error_final', 'dudoso') OR p.resultado IS NULL "
            f"OR p.resultado NOT IN {_LE_LLEGO})")


SQL_ATENDER = _sql_atender()
# Un renglón por persona (27 nov: con datos reales @persona.dos salía 6 veces): su último comentario de la lista.
SQL_ATENDER_UNO = (f"{SQL_ATENDER} AND c.id = (SELECT c2.id FROM comentarios c2 WHERE c2.user_id = c.user_id "
                   f"AND {_sql_atender('c2')} ORDER BY c2.comentado_en DESC, c2.id DESC LIMIT 1)")
NOMBRES_MODO = {
    "activo": "Activo", "pausado": "En pausa", "descansando": "Descansando", "frenado": "Frenado por TikTok",
    "parado_revision": "Necesita a una persona", "sesion_cerrada": "Sesión cerrada: hay que iniciar sesión",
    "sin_cuenta": "Falta conectar la cuenta de TikTok", "falta_configurar": "Le falta algo para trabajar",
}
TIPOS_TEXTO = {
    "dm": "Mensaje privado con el enlace", "respuesta": "Respuesta pública",
    "respuesta_alt": "Respuesta alternativa (mensajes cerrados)",
    "recordatorio": "Recordatorio (ya tenía la info y vuelve a comentar)",
    "ya_en_contacto": "Ya estamos en contacto (dio su número y vuelve a comentar)",
    "confirmar_numero": "Cuando deja su número", "pedir_numero": "Cuando pregunta o duda (pedir el número)",
    "animo": "Respuesta de ánimo (cuando se muestra interesado)", "seguimiento": "Seguimiento «¿cómo te fue?»",
    "bienvenida": "Bienvenida a seguidores nuevos", "fijado": "Comentario fijado",
}


GMAIL = {"SMTP_HOST": "smtp.gmail.com", "SMTP_PUERTO": "587"}
RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_CONTRASENA = 6
# El bot apunta su latido al empezar cada vuelta (nunca espera más de 60 s entre vueltas, pero una vuelta
# atendiendo a alguien puede tardar minutos). Más de 10 min sin latido = el bot no está funcionando.
MIN_SIN_SENAL = 10


def ahora() -> datetime:
    return datetime.now(UTC)


def _firma(clave_sesion: str, resumen: str, expira: int) -> str:
    # La firma depende también del resumen de la contraseña: si se cambia la contraseña, las sesiones viejas no valen.
    return hmac.new(f"{clave_sesion}|{resumen}".encode(), str(expira).encode(), hashlib.sha256).hexdigest()


def _token(clave_sesion: str, resumen: str, momento: datetime) -> str:
    """Valor de la cookie de sesión: «caduca.firma» (caduca en segundos desde 1970, DIAS_SESION días después)."""
    expira = int(momento.timestamp()) + DIAS_SESION * 86400
    return f"{expira}.{_firma(clave_sesion, resumen, expira)}"


def _token_valido(valor: str, clave_sesion: str | None, resumen: str | None, momento: datetime) -> bool:
    if not clave_sesion or not resumen:
        return False
    expira_texto, _, firma = valor.partition(".")
    if not expira_texto.isdigit() or int(expira_texto) <= momento.timestamp():
        return False
    return _iguales(firma, _firma(clave_sesion, resumen, int(expira_texto)))


def _pasar_a_resumen() -> None:
    """Instalaciones anteriores guardaban la contraseña del panel tal cual en .env: se cambia por su resumen (la
    contraseña sigue siendo la misma). También crea la clave de las sesiones si aún no hay."""
    secretos = config.cargar_secretos()
    cambios = {}
    clave = secretos.get("PANEL_CONTRASENA")
    if clave and not config.es_resumen(clave):
        cambios["PANEL_CONTRASENA"] = config.resumen_clave(clave)
    if not secretos.get("PANEL_SESION"):
        cambios["PANEL_SESION"] = secrets.token_hex(32)
    if cambios:
        config.guardar_secretos(cambios)


# Excel (y Google Sheets) toman como fórmula una celda que empieza por = + - @ (o tabulador / salto de línea).
_FORMULA = ("=", "+", "-", "@", "\t", "\r", "\n")
# Lo que sí puede empezar así y no es una fórmula: un teléfono (+1 801…) o un @usuario de TikTok (letras, números,
# punto y guion bajo: sin paréntesis no se puede montar ninguna fórmula).
_SEGURO = re.compile(r"[+-]?[\d\s().-]+|@[\w.]+")


def _celda(valor):
    """Celda del CSV que se abre en Excel. Auditoría 26 nov (hallazgo 12): un comentario o nombre de TikTok que
    empiece por «=» se ejecutaba como fórmula (p. ej. un enlace trampa en el Excel de los leads, con teléfonos).
    Se le pone ' delante, que Excel entiende como «esto es texto»."""
    if isinstance(valor, str) and valor.startswith(_FORMULA) and not _SEGURO.fullmatch(valor):
        return "'" + valor
    return valor


def _host_local(host: str) -> bool:
    """¿La cabecera Host nombra a este ordenador? («127.0.0.1:8765», «localhost», «[::1]:8765»…)"""
    try:
        nombre = urlsplit(f"//{host}").hostname
    except ValueError:
        return False
    return nombre in NOMBRES_LOCALES


def _origen_valido(request: Request) -> bool:
    """Un formulario (POST) solo vale si lo envía el propio panel. El navegador dice desde qué página sale
    (Origin, o si no Referer): tiene que ser esta misma dirección y puerto. Así otra web, o el panel de prueba de
    otro puerto (para el navegador, «el mismo sitio»), no pueden pulsar botones con la sesión del cliente.
    Sin ninguna de las dos no es un navegador engañado (los navegadores las mandan en los POST): se deja pasar."""
    origen = request.headers.get("origin") or request.headers.get("referer")
    if not origen:
        return True
    try:
        partes = urlsplit(origen)
    except ValueError:
        return False
    return partes.scheme == "http" and partes.netloc.lower() == request.headers.get("host", "").lower()


def _iguales(a: str, b: str) -> bool:
    """Comparación en tiempo constante que acepta ñ y tildes (con texto, compare_digest solo admite ASCII y daba
    error 500: el cliente se quedaba fuera del panel; auditoría 26 nov)."""
    return hmac.compare_digest(a.encode("utf-8", "surrogatepass"), b.encode("utf-8", "surrogatepass"))


def _lineas(texto: str) -> list[str]:
    return [x.strip().lstrip("@") for x in texto.replace(",", "\n").splitlines() if x.strip()]


def _hace(iso_texto: str | None, momento: datetime) -> str:
    if not iso_texto:
        return "—"
    s = (momento - _fecha(iso_texto)).total_seconds()
    if s < 3600:
        return f"{int(s // 60)} min"
    if s < 86400:
        return f"{s / 3600:.1f} h".replace(".", ",")
    return f"{s / 86400:.1f} días".replace(".", ",")


def _local(iso_texto: str | None) -> str:
    return _fecha(iso_texto).astimezone(ZONA).strftime("%d/%m %H:%M") if iso_texto else "—"


def crear_app() -> FastAPI:
    app = FastAPI(title="Panel Bot TikTok", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/estaticos", StaticFiles(directory=str(AQUI / "estaticos")), name="estaticos")
    plantillas.env.filters["local"] = _local

    def conexion():
        conn = db.conectar(hilos=True)
        try:
            yield conn
        finally:
            conn.close()

    _pasar_a_resumen()

    def contrasena() -> str | None:
        """El resumen de la contraseña del panel (o None si aún no se ha creado)."""
        guardada = config.cargar_secretos().get("PANEL_CONTRASENA")
        if guardada and not config.es_resumen(guardada):  # alguien la ha escrito a mano en el .env
            _pasar_a_resumen()
            guardada = config.cargar_secretos().get("PANEL_CONTRASENA")
        return guardada

    def clave_sesion() -> str:
        clave = config.cargar_secretos().get("PANEL_SESION")
        if not clave:
            clave = secrets.token_hex(32)
            config.guardar_secretos({"PANEL_SESION": clave})
        return clave

    def abrir_sesion(respuesta: Response, resumen: str) -> Response:
        respuesta.set_cookie(COOKIE, _token(clave_sesion(), resumen, ahora()), max_age=DIAS_SESION * 86400,
                             httponly=True, samesite="strict")
        return respuesta

    def autenticado(request: Request) -> bool:
        return _token_valido(request.cookies.get(COOKIE, ""), config.cargar_secretos().get("PANEL_SESION"),
                             contrasena(), ahora())

    def pagina(request: Request, nombre: str, conn: sqlite3.Connection, codigo: int = 200, **datos) -> HTMLResponse:
        modo_prueba = config.leer(conn, "modo_prueba")
        if nombre == "ajustes.html":  # «Tu cuenta de TikTok» también está en Ajustes
            datos.setdefault("cta", datos_cuenta(conn))
            datos.setdefault("es_mac", sys.platform == "darwin")
            datos.setdefault("cuenta_listo", request.query_params.get("cuenta") == "listo")
        fila = conn.execute("SELECT modo, ultimo_latido FROM estado_bot WHERE id = 1").fetchone()
        sin_senal = bool(fila) and (not fila[1] or (ahora() - _fecha(fila[1])).total_seconds() > MIN_SIN_SENAL * 60)
        cabecera = {"modo_bot": fila[0] if fila else None, "pausado_bot": config.leer(conn, "orden_pausa"),
                    "sin_senal_bot": sin_senal}
        return plantillas.TemplateResponse(
            request, nombre, {"modo_prueba": modo_prueba, "seccion": nombre.split(".")[0], **cabecera, **datos},
            status_code=codigo,
        )

    @app.middleware("http")
    async def exigir_login(request: Request, siguiente):
        # Antes que nada (también en /entrar y /entrar/crear): dirección local y formularios del propio panel.
        if not _host_local(request.headers.get("host", "")):
            return Response("Dirección no permitida: abre el panel en http://127.0.0.1:8765", status_code=400,
                            media_type="text/plain; charset=utf-8")
        if request.method not in METODOS_SIN_CAMBIOS and not _origen_valido(request):
            return Response("Este formulario no viene del panel del bot: no se ha hecho nada.", status_code=403,
                            media_type="text/plain; charset=utf-8")
        if request.url.path.startswith(("/entrar", "/estaticos")) or autenticado(request):
            return await siguiente(request)
        return RedirectResponse("/entrar", status_code=303)

    # ------------------------------------------------------------ login

    # Contraseñas falsas seguidas (en memoria: el panel es uno y solo escucha en este ordenador).
    intentos = {"fallos": 0, "hasta": 0.0}

    @app.get("/entrar", response_class=HTMLResponse)
    def entrar_form(request: Request):
        return plantillas.TemplateResponse(request, "entrar.html", {"error": None, "crear": not contrasena()})

    @app.post("/entrar")
    def entrar(request: Request, contrasena_form: str = Form(alias="contrasena")):
        clave = contrasena()
        momento = _reloj()
        if momento < intentos["hasta"]:  # mientras dura la espera no se comprueba ninguna, ni la buena
            segundos = int(intentos["hasta"] - momento) + 1
            return plantillas.TemplateResponse(
                request, "entrar.html", {"error": f"Demasiados intentos seguidos. Espera {segundos} segundos y vuelve "
                                                  "a escribir la contraseña.", "crear": not clave}, status_code=429)
        if not config.clave_coincide(contrasena_form, clave):
            intentos["fallos"] += 1
            if intentos["fallos"] >= FALLOS_ANTES_DE_ESPERAR:
                extra = intentos["fallos"] - FALLOS_ANTES_DE_ESPERAR
                intentos["hasta"] = momento + min(ESPERA_FALLOS_SEG * 2 ** min(extra, 10), ESPERA_FALLOS_MAX_SEG)
            return plantillas.TemplateResponse(request, "entrar.html", {"error": "Contraseña incorrecta", "crear": not clave},
                                               status_code=401)
        intentos.update(fallos=0, hasta=0.0)
        return abrir_sesion(RedirectResponse("/", status_code=303), clave)

    @app.post("/salir")
    def salir():
        """Botón «Salir» (Ajustes): cambia la clave de las sesiones, así se cierra la sesión en este navegador y en
        cualquier otro que la tuviera abierta. La contraseña no cambia."""
        config.guardar_secretos({"PANEL_SESION": secrets.token_hex(32)})
        r = RedirectResponse("/entrar", status_code=303)
        r.delete_cookie(COOKIE, httponly=True, samesite="strict")
        return r

    @app.post("/entrar/crear")
    def crear_contrasena(request: Request, nueva: str = Form(...), repetida: str = Form(...)):
        """Primera vez: el panel pide crear la contraseña (solo si aún no hay ninguna)."""
        if contrasena():
            return RedirectResponse("/entrar", status_code=303)
        error = None
        if len(nueva) < MIN_CONTRASENA:
            error = f"La contraseña tiene que tener al menos {MIN_CONTRASENA} caracteres."
        elif nueva != repetida:
            error = "Las dos contraseñas no coinciden. Escríbelas otra vez."
        if error:
            return plantillas.TemplateResponse(request, "entrar.html", {"error": error, "crear": True}, status_code=400)
        resumen = config.resumen_clave(nueva)
        config.guardar_secretos({"PANEL_CONTRASENA": resumen})
        # Inicio: la lista de lo que falta (cuenta, correo, landing, textos)
        return abrir_sesion(RedirectResponse("/", status_code=303), resumen)

    # ------------------------------------------------------------ inicio

    def resumen(conn: sqlite3.Connection, momento: datetime) -> dict:
        cfg = config.leer_todo(conn)
        estado = conn.execute("SELECT * FROM estado_bot WHERE id = 1").fetchone()
        # Quien aún no ha recibido su mensaje: en cola, enviándose ahora o esperando un reintento.
        cola = conn.execute(f"SELECT COUNT(*) FROM comentarios WHERE estado IN {SIN_MENSAJE}").fetchone()[0]
        historico = conn.execute("SELECT COUNT(*) FROM comentarios WHERE estado = 'historico'").fetchone()[0]
        mas_viejo = conn.execute(f"SELECT MIN(comentado_en) FROM comentarios WHERE estado IN {SIN_MENSAJE}").fetchone()[0]
        por_dia = min(NIVELES[cfg["nivel_ritmo"]][1] / (1.6 if cfg["modo_publica"] == "todos" else 1), cfg["tope_dm_dia"])
        horas = cola / (por_dia / 24) if cola else 0
        medianoche = datetime.combine(momento.astimezone(ZONA).date(), time(0), tzinfo=ZONA).astimezone(UTC).isoformat()

        def contar(sql: str) -> int:
            return conn.execute(sql, (medianoche,)).fetchone()[0]

        return {
            "estado": estado, "nombre_modo": NOMBRES_MODO.get(estado["modo"], estado["modo"]),
            "pausado": cfg["orden_pausa"], "cola": cola, "historico": historico, "mas_viejo": _hace(mas_viejo, momento),
            "estimado": "—" if not cola else (f"{horas:.1f} h" if horas < 48 else f"{horas / 24:.1f} días").replace(".", ","),
            "enviados": contar("SELECT COUNT(*) FROM acciones WHERE tipo='dm' AND resultado IN ('ok','simulado') AND hecho_en >= ?"),
            "cerrados": contar("SELECT COUNT(*) FROM acciones WHERE tipo='dm' AND resultado='cerrado' AND hecho_en >= ?"),
            "publicas": contar("SELECT COUNT(*) FROM acciones WHERE tipo IN ('respuesta','respuesta_alt') AND resultado IN ('ok','simulado') AND hecho_en >= ?"),
            "errores": contar("SELECT COUNT(*) FROM eventos WHERE tipo='error' AND creado_en >= ?"),
            "leads_hoy": contar("SELECT COUNT(*) FROM leads WHERE creado_en >= ?"),
            "leads_sin_hablar": conn.execute("SELECT COUNT(*) FROM leads WHERE hablado_en IS NULL").fetchone()[0],
            "personas_esperando": conn.execute("SELECT COUNT(*) FROM contactos WHERE humano = 1 AND telefono IS NULL").fetchone()[0],
            "nivel": cfg["nivel_ritmo"], "velocidades": ritmo.VELOCIDADES, "recomendada": ritmo.VELOCIDAD_RECOMENDADA,
            "riesgo_desde": ritmo.RIESGO_DESDE, "tope_dm_dia": cfg["tope_dm_dia"], "topes_dm": ritmo.TOPES_DM_DIA,
            "tope_recomendado": ritmo.TOPE_DM_RECOMENDADO, "tope_seguidores": cfg["tope_dm_seguidores_dia"],
            "topes_seguidores": ritmo.TOPES_SEGUIDORES_DIA, "seguidores_recomendado": ritmo.TOPE_SEGUIDORES_RECOMENDADO,
            "latido": _hace(estado["ultimo_latido"], momento),
            "privados_pausa": reglas.privados_en_pausa(conn, momento),
            # «+c.user_id»: que SQLite busque por fecha (índice) y no recorra todos los comentarios (medido: 0,4 → 0,09 s).
            "atender": conn.execute(f"SELECT COUNT(DISTINCT +c.user_id) FROM comentarios c JOIN contactos p "
                                    f"ON p.user_id = +c.user_id WHERE {SQL_ATENDER} AND c.comentado_en >= ?",
                                    ((momento - timedelta(days=7)).isoformat(timespec="seconds"),)).fetchone()[0],
            "correo_fallo": correo_fallido(conn),
        }

    def correo_fallido(conn: sqlite3.Connection) -> str | None:
        """Motivo del último correo de alertas que no salió, si después no salió ninguno ni se arregló el correo.
        Sin esto, con el correo mal puesto, el cliente no se enteraba de que no le llegaban los avisos."""
        fallo = conn.execute("SELECT detalle, creado_en FROM eventos WHERE tipo = 'alerta_fallida' "
                             "ORDER BY id DESC LIMIT 1").fetchone()
        if not fallo:
            return None
        despues = conn.execute("SELECT 1 FROM eventos WHERE tipo IN ('alerta_email', 'resumen', 'correo_guardado') "
                               "AND creado_en >= ? LIMIT 1", (fallo[1],)).fetchone()
        if despues:
            return None
        motivo = fallo[0] or ""
        return motivo.split("): ", 1)[1] if "): " in motivo else motivo  # «email no enviado (asunto): motivo»

    def textos_necesarios(modo: str) -> list[str]:
        """Tipos de texto que pide cada forma de responder (el comentario fijado es opcional)."""
        return {"todos": ["dm", "respuesta", "respuesta_alt"], "solo_cerrados": ["dm", "respuesta_alt", "fijado"],
                "solo_publicas": ["respuesta_alt"]}.get(modo, ["dm", "respuesta_alt"])

    def primeros_pasos(conn: sqlite3.Connection) -> list[dict]:
        """Lo que hace falta para que el bot pueda trabajar, según el modo de respuesta pública.
        Cada paso: hecho, texto, enlace y si es opcional (el comentario fijado: sin él el bot simplemente no fija)."""
        cfg = config.leer_todo(conn)
        modo = cfg["modo_publica"]
        activos = {t: n for t, n in conn.execute("SELECT tipo, COUNT(*) FROM textos WHERE activo = 1 GROUP BY tipo")}
        correo = alertas.correo_configurado()
        modo_bot = (conn.execute("SELECT modo FROM estado_bot WHERE id = 1").fetchone() or [""])[0]
        cuenta = (cfg.get("cuenta") or "").strip().lstrip("@")
        conectada = bool(cuenta) and modo_bot not in ("sin_cuenta", "sesion_cerrada")
        pasos = [{"hecho": conectada,
                  "texto": f"Cuenta de TikTok conectada: @{cuenta}" if conectada else
                  "Conecta tu cuenta de TikTok",
                  "enlace": "/cuenta", "boton": "Conectar mi cuenta", "opcional": False},
                 {"hecho": correo, "texto": "Correo de alertas configurado" if correo else
                  "Falta configurar el correo de alertas",
                  "enlace": "/correo", "boton": "Configurar el correo", "opcional": False}]
        if modo != "solo_publicas":
            hay = bool(cfg["landing"])
            pasos.append({"hecho": hay, "texto": "Enlace de tu página (landing): listo" if hay else "Escribe el enlace de tu página (landing)",
                          "enlace": "/ajustes#landing", "boton": "Ir a Ajustes", "opcional": False})
        if cfg["bienvenida_seguidores"]:  # D40: sin enlace de YouTube no hay bienvenida (el resto funciona igual)
            hay = bool(cfg["enlace_youtube"])
            pasos.append({"hecho": hay, "texto": "Enlace de YouTube para la bienvenida: listo" if hay else
                          "Escribe el enlace de YouTube para la bienvenida a seguidores nuevos",
                          "enlace": "/ajustes#bienvenida", "boton": "Ir a Ajustes", "opcional": True,
                          "nota": "opcional: sin él no se manda la bienvenida; lo demás funciona igual"})
        necesarios = textos_necesarios(modo)
        for tipo in necesarios:
            hay = activos.get(tipo, 0) > 0
            pasos.append({"hecho": hay, "texto": f"{TIPOS_TEXTO[tipo]}: {'listo' if hay else 'escribe al menos una versión'}",
                          "enlace": f"/textos#grupo-{tipo}", "boton": "Escribir", "opcional": tipo == "fijado"})
        return pasos

    @app.get("/", response_class=HTMLResponse)
    def inicio(request: Request, conn=Depends(conexion)):
        pasos = primeros_pasos(conn)
        return pagina(request, "inicio.html", conn, falta_correo=not alertas.correo_configurado(), pasos=pasos,
                      error_freno=request.query_params.get("freno") == "clave",
                      aviso_velocidad=request.query_params.get("velocidad", ""),
                      pasos_pendientes=[p for p in pasos if not p["hecho"] and not p["opcional"]], **resumen(conn, ahora()))

    @app.post("/pausar")
    def pausar(conn=Depends(conexion)):
        config.guardar(conn, "orden_pausa", True)
        return RedirectResponse("/", status_code=303)

    def _reiniciar_bot() -> None:
        """Vuelve a arrancar el proceso del bot para que aplique ya «apagado»/«encendido» (sin servicio, nada)."""
        if instalador.servicio_instalado("bot"):
            instalador.parar("bot")
            instalador.arrancar("bot")

    @app.post("/apagar")
    def apagar(conn=Depends(conexion)):
        """Apaga el bot del todo: cierra su Chrome, deja dormir la computadora y sigue apagado al reiniciar."""
        config.guardar(conn, "apagado", True)
        with conn:
            conn.execute("UPDATE estado_bot SET modo = 'apagado', mensaje = 'Apagado desde el panel' WHERE id = 1")
            conn.execute("INSERT INTO eventos (tipo, detalle, creado_en) VALUES ('apagado', 'apagado desde el panel', ?)",
                         (iso(ahora()),))
        _reiniciar_bot()
        return RedirectResponse("/", status_code=303)

    @app.post("/encender")
    def encender(conn=Depends(conexion)):
        config.guardar(conn, "apagado", False)
        config.guardar(conn, "orden_pausa", False)
        with conn:
            conn.execute("UPDATE estado_bot SET modo = 'activo', mensaje = 'Encendiendo', ultimo_latido = ? WHERE id = 1",
                         (iso(ahora()),))
            conn.execute("INSERT INTO eventos (tipo, detalle, creado_en) VALUES ('encendido', 'encendido desde el panel', ?)",
                         (iso(ahora()),))
        _reiniciar_bot()
        return RedirectResponse("/", status_code=303)

    @app.post("/reanudar")
    def reanudar(conn=Depends(conexion)):
        config.guardar(conn, "orden_pausa", False)
        modo = conn.execute("SELECT modo FROM estado_bot WHERE id = 1").fetchone()[0]
        if modo in ("parado_revision", "sesion_cerrada"):
            Freno(conn).reanudar_manual(ahora())
        return RedirectResponse("/", status_code=303)

    @app.post("/quitar-freno")
    def quitar_freno(clave_soporte: str = Form(""), conn=Depends(conexion)):
        """Solo soporte: el freno fue una falsa alarma (TikTok no avisó de nada). Pide la clave de soporte."""
        if not config.clave_soporte_correcta(clave_soporte):
            return RedirectResponse("/?freno=clave", status_code=303)
        Freno(conn).quitar_falsa_alarma(ahora())
        return RedirectResponse("/", status_code=303)

    # ------------------------------------------------------------ cuenta de TikTok

    def datos_cuenta(conn: sqlite3.Connection) -> dict:
        cuenta_ = (config.leer(conn, "cuenta") or "").strip().lstrip("@")
        modo = (conn.execute("SELECT modo FROM estado_bot WHERE id = 1").fetchone() or [""])[0]
        if navegador.ventana_sesion_abierta():
            estado = "conectando"
        elif not cuenta_ or modo == "sin_cuenta":
            estado = "sin_cuenta"
        elif modo == "sesion_cerrada":
            estado = "cerrada"
        else:
            estado = "conectada"
        return {"cuenta": cuenta_, "estado": estado, "no_vimos_sesion": estado == "sin_cuenta" and _ya_entre_sin_sesion(conn)}

    def _ya_entre_sin_sesion(conn: sqlite3.Connection) -> bool:
        """¿Pulsó «Ya entré» hace poco y el bot, al volver a mirar, no encontró la sesión? (entró antes de tiempo
        o el código QR no llegó a usarse). Se da 45 s al bot para arrancar y comprobarlo."""
        fila = conn.execute("SELECT creado_en FROM eventos WHERE tipo = 'cuenta' AND detalle = 'ya entré' "
                            "ORDER BY id DESC LIMIT 1").fetchone()
        latido = (conn.execute("SELECT ultimo_latido FROM estado_bot WHERE id = 1").fetchone() or [None])[0]
        if not fila or not latido:
            return False
        pulsado = datetime.fromisoformat(fila[0])
        return (ahora() - pulsado).total_seconds() < 30 * 60 and \
            (datetime.fromisoformat(latido) - pulsado).total_seconds() > 45

    @app.get("/cuenta", response_class=HTMLResponse)
    def ver_cuenta(request: Request, listo: int = 0, conn=Depends(conexion)):
        return pagina(request, "cuenta.html", conn, cta=datos_cuenta(conn), listo=bool(listo),
                      es_mac=sys.platform == "darwin")

    def _volver(volver: str, listo: bool = False) -> RedirectResponse:
        """Tras una acción de la cuenta, de vuelta a la página desde la que se pulsó (Cuenta o Ajustes)."""
        if volver == "ajustes":
            return RedirectResponse("/ajustes" + ("?cuenta=listo" if listo else "") + "#cuenta", status_code=303)
        return RedirectResponse("/cuenta" + ("?listo=1" if listo else ""), status_code=303)

    @app.post("/cuenta/conectar")
    def cuenta_conectar(volver: str = Form("")):
        cuenta.conectar()
        return _volver(volver)

    @app.post("/cuenta/listo")
    def cuenta_listo(volver: str = Form(""), conn=Depends(conexion)):
        cuenta.listo()
        with conn:
            conn.execute("INSERT INTO eventos (tipo, detalle, creado_en) VALUES ('cuenta', 'ya entré', ?)", (iso(ahora()),))
        return _volver(volver, listo=True)

    @app.post("/cuenta/desconectar")
    def cuenta_desconectar(volver: str = Form(""), conn=Depends(conexion)):
        """«Cerrar sesión»: el bot deja de usar la cuenta (no trabaja hasta que se conecte una)."""
        cuenta.desconectar(conn, ahora())
        return _volver(volver)

    @app.post("/cuenta/cambiar")
    def cuenta_cambiar(volver: str = Form(""), conn=Depends(conexion)):
        """«Cambiar de cuenta»: cierra la sesión actual y abre ya la ventana para entrar con otra."""
        cuenta.desconectar(conn, ahora())
        cuenta.conectar()
        return _volver(volver)

    @app.post("/velocidad")
    def velocidad(nivel: int = Form(...), tope_dm_dia: int | None = Form(None), tope_seguidores: int | None = Form(None),
                  acepto_riesgo: str = Form(""), conn=Depends(conexion)):
        """D31: el cliente elige cada cuánto responde el bot y el máximo de mensajes al día.
        Las opciones de riesgo alto piden «Entiendo el riesgo»."""
        error = ritmo.elegir_velocidad(conn, nivel, acepto_riesgo == "1", ahora(), tope_dm_dia=tope_dm_dia,
                                       tope_seguidores=tope_seguidores)
        return RedirectResponse("/?velocidad=riesgo#velocidad" if error else "/?velocidad=ok#velocidad", status_code=303)

    # ------------------------------------------------------------ textos

    @app.get("/textos", response_class=HTMLResponse)
    def ver_textos(request: Request, error: str = "", conn=Depends(conexion)):
        landing = config.leer(conn, "landing") or "https://tu-landing.com"
        youtube = config.leer(conn, "enlace_youtube") or "https://youtu.be/…"
        utm = config.leer(conn, "utm")
        necesarios = textos_necesarios(config.leer(conn, "modo_publica"))
        grupos = {}
        for tipo, titulo in TIPOS_TEXTO.items():
            filas = []
            for t in conn.execute("SELECT * FROM textos WHERE tipo = ? ORDER BY id", (tipo,)):
                medio = "comentario" if tipo in textos.PUBLICOS else "dm"
                url = textos.landing_con_utm(landing, utm, reel_id="7300000000000000000", medio=medio)
                try:
                    vista = textos.rellenar(t["contenido"], nombre="María", landing=url, vendedor="Carlos", youtube=youtube)
                except textos.SinLanding:
                    vista = "(falta el enlace de tu página en Ajustes)"
                aviso = "Este mensaje privado no lleva {landing}." if tipo == "dm" and "{landing}" not in t["contenido"] else None
                if textos.numero_en_publico(tipo, t["contenido"]):
                    aviso = "Parece llevar un número de teléfono: el bot nunca lo publicará en un comentario."
                filas.append({"t": t, "vista": vista, "aviso": aviso,
                              "vista_sin_nombre": textos.rellenar(t["contenido"], "", url, youtube=youtube)})
            grupos[tipo] = {"titulo": titulo, "filas": filas, "activos": sum(f["t"]["activo"] for f in filas),
                            "necesario": tipo in necesarios and tipo != "fijado", "privado": tipo not in textos.PUBLICOS}
        return pagina(request, "textos.html", conn, grupos=grupos, error=error)

    def _numero_en_publico(tipo: str, contenido: str) -> bool:
        return textos.numero_en_publico(tipo, contenido)

    @app.post("/textos/nuevo")
    def nuevo_texto(tipo: str = Form(...), contenido: str = Form(...), conn=Depends(conexion)):
        if tipo in TIPOS_TEXTO and contenido.strip():
            if _numero_en_publico(tipo, contenido):  # D44
                return RedirectResponse(f"/textos?error=numero#grupo-{tipo}", status_code=303)
            with conn:
                conn.execute("INSERT INTO textos (tipo, contenido) VALUES (?, ?)", (tipo, contenido.strip()))
        return RedirectResponse(f"/textos#grupo-{tipo}", status_code=303)

    @app.post("/textos/{texto_id}")
    def editar_texto(texto_id: int, contenido: str = Form(...), activo: str = Form("0"), conn=Depends(conexion)):
        fila = conn.execute("SELECT tipo FROM textos WHERE id = ?", (texto_id,)).fetchone()
        tipo = fila["tipo"] if fila else ""
        if contenido.strip():
            if _numero_en_publico(tipo, contenido):  # D44
                return RedirectResponse(f"/textos?error=numero#grupo-{tipo}", status_code=303)
            with conn:
                conn.execute("UPDATE textos SET contenido = ?, activo = ? WHERE id = ?",
                             (contenido.strip(), 1 if activo == "1" else 0, texto_id))
        return RedirectResponse(f"/textos#grupo-{tipo}" if tipo else "/textos", status_code=303)

    # ------------------------------------------------------------ ajustes

    @app.get("/ajustes", response_class=HTMLResponse)
    def ver_ajustes(request: Request, conn=Depends(conexion)):
        return pagina(request, "ajustes.html", conn, cfg=config.leer_todo(conn), error=None, guardado=False)

    @app.post("/ajustes", response_class=HTMLResponse)
    async def guardar_ajustes(request: Request, conn=Depends(conexion)):
        form = await request.form()
        cfg = config.leer_todo(conn)
        # Lo que escribió la persona, tal cual: si algo no vale, se le vuelve a enseñar sin perder sus cambios.
        escrito = {
            "modo_publica": form.get("modo_publica", cfg["modo_publica"]),
            "landing": form.get("landing", cfg["landing"]).strip(),
            "caducidad_dias": form.get("caducidad_dias", str(cfg["caducidad_dias"])).strip(),
            "palabras_ofensivas": _lineas(form.get("palabras_ofensivas", "\n".join(cfg["palabras_ofensivas"]))),
            "cuentas_equipo": _lineas(form.get("cuentas_equipo", "\n".join(cfg["cuentas_equipo"]))),
            "emails_alerta": _lineas(form.get("emails_alerta", "\n".join(cfg["emails_alerta"]))),
            "descanso_diario_inicio": [form.get("descanso_desde", cfg["descanso_diario_inicio"][0]),
                                       form.get("descanso_hasta", cfg["descanso_diario_inicio"][1])],
            "modo_prueba": form.get("modo_prueba") == "1",
            "atender_historico": form.get("atender_historico") == "1",
            # §17 · instructivo
            "orden_cola": form.get("orden_cola", cfg["orden_cola"]),
            "recordatorio_cada_dias": form.get("recordatorio_cada_dias", str(cfg["recordatorio_cada_dias"])).strip(),
            "leer_bandeja": form.get("leer_bandeja") == "1",
            "max_respuestas_auto": form.get("max_respuestas_auto", str(cfg["max_respuestas_auto"])).strip(),
            "palabras_humano": _lineas(form.get("palabras_humano", "\n".join(cfg["palabras_humano"]))),
            "palabras_interes": _lineas(form.get("palabras_interes", "\n".join(cfg["palabras_interes"]))),
            "emails_leads": _lineas(form.get("emails_leads", "\n".join(cfg["emails_leads"]))),
            "webhook_leads": form.get("webhook_leads", cfg["webhook_leads"]).strip(),
            "seguimiento_espera_h": form.get("seguimiento_espera_h", str(cfg["seguimiento_espera_h"])).strip(),
            "bienvenida_seguidores": form.get("bienvenida_seguidores") == "1",
            "enlace_youtube": form.get("enlace_youtube", cfg["enlace_youtube"]).strip(),
        }
        try:
            def numero(clave: str, que: str, nota: str = "") -> int:
                """Número entre el mínimo y el máximo de config.RANGOS (auditoría 26 nov: sin máximo, un número
                absurdo dejaba al bot sin atender)."""
                minimo, maximo = config.RANGOS[clave]
                try:
                    valor = int(escrito[clave] or 0)
                except ValueError:
                    raise ValueError(f"{que} tiene que ser un número{nota}.")
                if not minimo <= valor <= maximo:
                    raise ValueError(f"{que} tiene que estar entre {minimo} y {maximo}{nota}.")
                return valor

            nuevos = {**escrito,
                      "caducidad_dias": numero("caducidad_dias", "los días de «No atender comentarios con más de…»",
                                               " (0 = sin límite)"),
                      "recordatorio_cada_dias": numero("recordatorio_cada_dias", "«Recordatorio en público como mucho cada…»"),
                      "max_respuestas_auto": numero("max_respuestas_auto", "«Respuestas automáticas por persona»"),
                      "seguimiento_espera_h": numero("seguimiento_espera_h", "«Escribir el seguimiento tras…»")}
            for correo in nuevos["emails_leads"]:
                if not RE_EMAIL.match(correo):
                    raise ValueError(f"«{correo}» no parece un correo válido (correos para los leads).")
            for t in nuevos["descanso_diario_inicio"]:
                try:
                    time.fromisoformat(t)
                except ValueError:
                    raise ValueError("la hora del descanso diario no es válida.")
            for clave, valor in nuevos.items():
                config.validar(clave, valor)
        except (ValueError, config.ErrorConfig) as e:
            return pagina(request, "ajustes.html", conn, codigo=400, cfg={**cfg, **escrito}, error=str(e), guardado=False)
        for clave, valor in nuevos.items():
            config.guardar(conn, clave, valor)
        repuestos = None
        if cfg["modo_prueba"] and not nuevos["modo_prueba"]:  # paso a real: lo atendido en prueba vuelve a la cola
            from bot import reglas

            repuestos = reglas.reponer_simulados(conn, ahora())
            with conn:
                conn.execute("INSERT INTO eventos (tipo, detalle, creado_en) VALUES ('modo_prueba', ?, ?)",
                             (f"modo de prueba quitado; {repuestos} comentarios vuelven a la cola", iso(ahora())))
        return pagina(request, "ajustes.html", conn, cfg=config.leer_todo(conn), error=None, guardado=True,
                      repuestos=repuestos)

    # ------------------------------------------------------------ correo de alertas

    def datos_correo(conn: sqlite3.Connection) -> dict:
        d = alertas.datos_smtp()
        otro = bool(d["SMTP_HOST"]) and d["SMTP_HOST"] != GMAIL["SMTP_HOST"]
        return {"correo": d["SMTP_USUARIO"] or "", "proveedor": "otro" if otro else "gmail",
                "servidor": d["SMTP_HOST"] if otro else "", "puerto": d["SMTP_PUERTO"] or "587",
                "clave_guardada": bool(d["SMTP_CONTRASENA"]), "configurado": alertas.correo_configurado(),
                "avisar_a": "\n".join(config.leer(conn, "emails_alerta"))}

    @app.get("/correo", response_class=HTMLResponse)
    def ver_correo(request: Request, conn=Depends(conexion)):
        return pagina(request, "correo.html", conn, f=datos_correo(conn), error=None, listo=None)

    @app.post("/correo", response_class=HTMLResponse)
    async def guardar_correo(request: Request, conn=Depends(conexion)):
        form = await request.form()
        f = {**datos_correo(conn), "correo": form.get("correo", "").strip(), "proveedor": form.get("proveedor", "gmail"),
             "servidor": form.get("servidor", "").strip(), "puerto": form.get("puerto", "587").strip(),
             "avisar_a": form.get("avisar_a", "")}
        clave = form.get("clave", "")
        actuales = alertas.datos_smtp()

        def fallo(texto: str) -> HTMLResponse:
            return pagina(request, "correo.html", conn, codigo=400, f=f, error=texto, listo=None, cambiar=True)

        # 27 nov (decisión del usuario, deshace D30): el cliente configura y cambia el correo él mismo, sin clave.
        if not RE_EMAIL.match(f["correo"]):
            return fallo("Escribe el correo completo, por ejemplo alertas.academia@gmail.com.")
        if f["proveedor"] == "gmail":
            clave = re.sub(r"\s+", "", clave)  # Google la enseña en grupos de 4 con espacios
            if clave and len(clave) != 16:
                return fallo("La contraseña de aplicación de Gmail tiene 16 letras. Cópiala otra vez desde Google.")
            servidor = GMAIL
        else:
            if not f["servidor"]:
                return fallo("Escribe el servidor de correo (te lo da tu proveedor, por ejemplo smtp.tuempresa.com).")
            if not f["puerto"].isdigit():
                return fallo("El puerto es un número (normalmente 587 o 465).")
            servidor = {"SMTP_HOST": f["servidor"], "SMTP_PUERTO": f["puerto"]}
        if not clave:
            if f["clave_guardada"] and f["correo"] == actuales["SMTP_USUARIO"]:
                clave = actuales["SMTP_CONTRASENA"]
            else:
                return fallo("Escribe la contraseña del correo.")
        avisar_a = _lineas(f["avisar_a"]) or [f["correo"]]
        malos = [x for x in avisar_a if not RE_EMAIL.match(x)]
        if malos:
            return fallo(f"Este correo para avisar no está bien escrito: {malos[0]}")

        datos = {**servidor, "SMTP_USUARIO": f["correo"], "SMTP_CONTRASENA": clave, "SMTP_REMITENTE": f["correo"]}
        problema = alertas.probar_correo(datos, avisar_a)
        if problema:
            return fallo(problema)  # no se guarda nada que no funcione
        config.guardar_secretos(datos)
        config.guardar(conn, "emails_alerta", avisar_a)
        alertas.correo_arreglado(conn, ahora())  # correo nuevo y probado: los avisos no esperan por fallos de antes
        return pagina(request, "correo.html", conn, f=datos_correo(conn), error=None, listo=", ".join(avisar_a))

    @app.post("/correo/probar", response_class=HTMLResponse)
    def probar_correo_guardado(request: Request, conn=Depends(conexion)):
        """Vuelve a mandar el correo de prueba con lo ya guardado (sin escribir la contraseña otra vez)."""
        f = datos_correo(conn)
        if not alertas.correo_configurado():
            return pagina(request, "correo.html", conn, codigo=400, f=f, error="Primero configura el correo.", listo=None)
        avisar_a = config.leer(conn, "emails_alerta") or [f["correo"]]
        problema = alertas.probar_correo(alertas.datos_smtp(), avisar_a)
        if problema:
            return pagina(request, "correo.html", conn, codigo=400, f=f, error=problema, listo=None, cambiar=True)
        return pagina(request, "correo.html", conn, f=f, error=None, listo=", ".join(avisar_a))

    # ------------------------------------------------------------ registro

    def filas_registro(conn: sqlite3.Connection, estado: str | None, limite: int | None = 500,
                       detalle: bool = True) -> list:
        """Filas del Registro. `detalle` añade el envío a mano (`a_mano`) y la captura del último error (`captura`),
        que solo usa la página: el CSV no los pide (con un año de historial tardaba ~170 s, medido el 27 nov)."""
        sql = ("SELECT c.id, c.reel_id, p.usuario, c.texto, c.estado, c.ofensivo, c.intentos, c.ultimo_error, "
               "c.comentado_en, c.actualizado_en, p.user_id, p.resultado AS resultado_contacto "
               "FROM comentarios c JOIN contactos p ON p.user_id = c.user_id")
        params: list = []
        if estado == PENDIENTES:  # quien aún no ha recibido su mensaje, en el orden en que llegó
            sql += f" WHERE c.estado IN {SIN_MENSAJE} ORDER BY c.comentado_en ASC"
        elif estado == ATENDER:  # a quien el bot no pudo escribir: lo más reciente arriba
            sql += f" WHERE {SQL_ATENDER_UNO} ORDER BY c.comentado_en DESC"
        else:
            if estado:
                sql += " WHERE c.estado = ?"
                params.append(estado)
            sql += " ORDER BY c.comentado_en DESC"
        if limite:
            sql += f" LIMIT {int(limite)}"
        filas = conn.execute(sql, params).fetchall()
        if not detalle:
            return filas
        # Solo para las filas que se enseñan (como mucho `limite`), cada una con su índice.
        a_mano_sql = ("SELECT estado || '|' || COALESCE(ultimo_error, '') FROM envios WHERE user_id = ? "
                      "AND tipo = 'dm_manual' ORDER BY id DESC LIMIT 1")
        # Captura del último error de cada comentario: los eventos de error empiezan por «<id del comentario>:».
        capturas: dict[str, str] = {}
        for e in conn.execute("SELECT detalle, captura FROM eventos WHERE captura IS NOT NULL AND detalle LIKE '%:%' "
                              "ORDER BY id"):
            capturas[e["detalle"].split(":", 1)[0]] = e["captura"]
        publica_sql = ("SELECT estado || '|' || COALESCE(ultimo_error, '') FROM envios WHERE comentario_id = ? "
                       "AND tipo = 'publica_manual' ORDER BY id DESC LIMIT 1")
        salida = []
        for f in filas:
            d = dict(f)
            fila = conn.execute(a_mano_sql, (f["user_id"],)).fetchone()
            d["a_mano"] = fila[0] if fila else None
            fila = conn.execute(publica_sql, (f["id"],)).fetchone()
            d["publica_a_mano"] = fila[0] if fila else None
            d["captura"] = capturas.get(f["id"])
            salida.append(d)
        return salida

    @app.get("/registro", response_class=HTMLResponse)
    def registro(request: Request, estado: str | None = None, conn=Depends(conexion)):
        estados = [f[0] for f in conn.execute("SELECT DISTINCT estado FROM comentarios ORDER BY estado")]
        cfg = config.leer_todo(conn)
        modo = (conn.execute("SELECT modo FROM estado_bot WHERE id = 1").fetchone() or [""])[0]
        espera = ("El bot está en pausa: saldrá cuando pulses «Reanudar»." if cfg["orden_pausa"] or modo == "pausado" else
                  "El bot está apagado: saldrá cuando lo enciendas." if cfg["apagado"] else
                  "El bot está frenado o necesita a una persona: saldrá cuando vuelva a trabajar."
                  if modo in ("frenado", "parado_revision", "sesion_cerrada") else "")
        filas = filas_registro(conn, estado or None)
        enviando = any((f[k] or "").startswith(("pendiente|", "enviando|")) for f in filas for k in ("a_mano", "publica_a_mano"))
        return pagina(request, "registro.html", conn, filas=filas, estados=estados, enviando_a_mano=enviando,
                      filtro=estado or "", con_boton=sin_privado, con_boton_publica=sin_publica_a_mano,
                      en_pausa=reglas.privados_en_pausa(conn, ahora()),
                      pedido=request.query_params.get("pedido", ""), publica=request.query_params.get("publica", ""),
                      espera_bot=espera)

    def sin_privado(f) -> bool:
        """¿Ofrecer «Enviar mensaje privado ahora» en esta fila? A quien comentó y no le llegó nuestro privado:
        TikTok no lo dejó pasar, tenía los mensajes cerrados o se le contestó solo en público. No a la propia
        cuenta ni al equipo, ni mientras su mensaje ya está en camino."""
        if f["estado"] in ("saltado_propio", "en_cola", "dm_enviando", "error", "historico"):
            return False
        if f["resultado_contacto"] in reglas.LE_LLEGO:
            return False
        return not (f["a_mano"] or "").startswith(("pendiente|", "enviando|"))

    def sin_publica_a_mano(f) -> bool:
        """¿Ofrecer «Comentar en público ahora»? A las mismas filas que el privado a mano (no le llegó nuestro
        privado), salvo comentarios ofensivos (D3: nunca en público) o con un comentario a mano ya en camino."""
        if f["ofensivo"] or f["estado"] in ("saltado_propio", "en_cola", "dm_enviando", "error", "historico",
                                            "saltado_no_disponible", "saltado_ofensivo"):
            return False
        if f["resultado_contacto"] in reglas.LE_LLEGO and f["estado"] not in ("error_final", "dudoso"):
            return False
        return not (f["publica_a_mano"] or "").startswith(("pendiente|", "enviando|"))

    @app.post("/registro/publica/{comentario_id}")
    def publica_a_mano(comentario_id: str, conn=Depends(conexion)):
        """Botón del Registro: el bot contesta en público a este comentario ya (texto «escríbenos» si no le llegó el
        privado). Un clic = un comentario. Nunca a un comentario ofensivo (D3)."""
        c = conn.execute("SELECT c.id, c.user_id, c.ofensivo, p.usuario FROM comentarios c JOIN contactos p "
                         "ON p.user_id = c.user_id WHERE c.id = ?", (comentario_id,)).fetchone()
        if c is None or c["ofensivo"]:
            return RedirectResponse("/registro", status_code=303)
        momento = iso(ahora())
        with conn:
            ya = conn.execute("SELECT 1 FROM envios WHERE comentario_id = ? AND tipo = 'publica_manual' "
                              "AND estado IN ('pendiente', 'enviando')", (comentario_id,)).fetchone()
            if not ya:
                conn.execute("INSERT INTO envios (user_id, usuario, tipo, comentario_id, creado_en, listo_desde, "
                             "actualizado_en) VALUES (?, ?, 'publica_manual', ?, ?, ?, ?)",
                             (c["user_id"], c["usuario"], comentario_id, momento, momento, momento))
        return RedirectResponse(f"/registro?publica={c['usuario']}", status_code=303)

    @app.post("/registro/privado/{user_id}")
    def privado_a_mano(user_id: str, conn=Depends(conexion)):
        """Botón del Registro: el mensaje privado de siempre (Textos → «Mensaje privado con el enlace») a esta
        persona, ya. Lo envía el bot en cuanto lo nota (1–2 s), sin esperar su turno. Un clic = un mensaje."""
        k = conn.execute("SELECT usuario FROM contactos WHERE user_id = ?", (user_id,)).fetchone()
        if k is None:
            return RedirectResponse("/registro", status_code=303)
        momento = iso(ahora())
        with conn:
            ya = conn.execute("SELECT 1 FROM envios WHERE user_id = ? AND tipo = 'dm_manual' "
                              "AND estado IN ('pendiente', 'enviando')", (user_id,)).fetchone()
            if not ya:
                conn.execute("INSERT INTO envios (user_id, usuario, tipo, creado_en, listo_desde, actualizado_en) "
                             "VALUES (?, ?, 'dm_manual', ?, ?, ?)", (user_id, k["usuario"], momento, momento, momento))
        return RedirectResponse(f"/registro?pedido={k['usuario']}", status_code=303)

    @app.get("/registro.csv")
    def registro_csv(estado: str | None = None, conn=Depends(conexion)):
        salida = io.StringIO()
        w = csv.writer(salida)
        filas = filas_registro(conn, estado or None, limite=None, detalle=False)
        w.writerow(["id", "reel_id", "usuario", "texto", "estado", "ofensivo", "intentos", "ultimo_error", "comentado_en", "actualizado_en"])
        for f in filas:
            w.writerow([_celda(f[k]) for k in ("id", "reel_id", "usuario", "texto", "estado", "ofensivo", "intentos", "ultimo_error", "comentado_en", "actualizado_en")])
        return Response("﻿" + salida.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="registro-bot.csv"'})

    @app.get("/capturas/{nombre}")
    def captura(nombre: str):
        carpeta = (config.carpeta_datos() / "capturas").resolve()
        ruta = (carpeta / nombre).resolve()
        if ruta.parent != carpeta or not ruta.is_file():
            return Response(status_code=404)
        return FileResponse(ruta)

    # ------------------------------------------------------------ leads (§17, D38–D39)

    def filas_leads(conn: sqlite3.Connection, filtro: str) -> list[sqlite3.Row]:
        donde = {"sin_hablar": " WHERE l.hablado_en IS NULL", "hablados": " WHERE l.hablado_en IS NOT NULL"}.get(filtro, "")
        return conn.execute(
            "SELECT l.*, r.url AS video_url FROM leads l LEFT JOIN reels r ON r.id = l.reel_id" + donde +
            " ORDER BY l.creado_en DESC LIMIT 1000").fetchall()

    @app.get("/leads", response_class=HTMLResponse)
    def ver_leads(request: Request, filtro: str = "", conn=Depends(conexion)):
        from bot import conversacion, leads

        cuentas = {k: conn.execute(sql).fetchone()[0] for k, sql in (
            ("todos", "SELECT COUNT(*) FROM leads"),
            ("sin_hablar", "SELECT COUNT(*) FROM leads WHERE hablado_en IS NULL"),
            ("hablados", "SELECT COUNT(*) FROM leads WHERE hablado_en IS NOT NULL"))}
        personas = conn.execute(
            "SELECT k.*, (SELECT MAX(m.creado_en) FROM mensajes m WHERE m.user_id = k.user_id AND m.de = 'persona') "
            "AS ultimo_mensaje FROM contactos k WHERE k.humano = 1 AND k.telefono IS NULL ORDER BY ultimo_mensaje DESC"
        ).fetchall()
        etapas = {e: n for e, n in conn.execute("SELECT etapa, COUNT(*) FROM contactos GROUP BY etapa")}
        return pagina(request, "leads.html", conn, filas=filas_leads(conn, filtro), filtro=filtro, cuentas=cuentas,
                      personas=personas, etapas=etapas, NOMBRES_ETAPA=conversacion.ETAPAS,
                      legible=leads.telefono_legible, webhook=bool(config.leer(conn, "webhook_leads")))

    @app.post("/leads/{lead_id}/hablado")
    def lead_hablado(lead_id: int, vendedor: str = Form(""), conn=Depends(conexion)):
        from bot import leads

        leads.marcar_hablado(conn, lead_id, vendedor, ahora())
        return RedirectResponse("/leads?hecho=hablado", status_code=303)

    @app.post("/leads/persona/{user_id}/devolver")
    def devolver_al_bot(user_id: str, conn=Depends(conexion)):
        """La persona que pasó a manos del equipo vuelve a recibir respuestas automáticas."""
        with conn:
            conn.execute("UPDATE contactos SET humano = 0, humano_motivo = NULL, respuestas_auto = 0 WHERE user_id = ?",
                         (user_id,))
        return RedirectResponse("/leads", status_code=303)

    @app.post("/leads/persona/{user_id}/atendida")
    def persona_atendida(user_id: str, conn=Depends(conexion)):
        """El equipo ya le contestó: sale de la lista (el bot sigue sin contestarle)."""
        with conn:
            conn.execute("UPDATE contactos SET humano_motivo = NULL, humano = 2 WHERE user_id = ?", (user_id,))
        return RedirectResponse("/leads", status_code=303)

    @app.get("/leads.csv")
    def leads_csv(filtro: str = "", conn=Depends(conexion)):
        salida = io.StringIO()
        w = csv.writer(salida)
        campos = ("creado_en", "usuario", "nombre", "telefono", "video_url", "palabra_clave", "hablado_en", "vendedor")
        w.writerow(["fecha", "usuario_tiktok", "nombre", "telefono", "video", "palabra_clave", "ya_hablado", "vendedor"])
        for f in filas_leads(conn, filtro):
            w.writerow([_celda(("@" + f[k]) if k == "usuario" else f[k]) for k in campos])
        return Response("\ufeff" + salida.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="leads-bot.csv"'})

    # ------------------------------------------------------------ reels

    @app.get("/reels", response_class=HTMLResponse)
    def reels(request: Request, conn=Depends(conexion)):
        filas = conn.execute(
            "SELECT r.*, (SELECT COUNT(*) FROM comentarios c WHERE c.reel_id = r.id) AS comentarios, "
            "(SELECT COUNT(*) FROM comentarios c WHERE c.reel_id = r.id AND c.trae_palabra = 1) AS con_palabra "
            "FROM reels r ORDER BY COALESCE(r.publicado_en, '') DESC, r.id DESC"
        ).fetchall()
        return pagina(request, "reels.html", conn, filas=filas)

    @app.post("/reels/{reel_id}/palabra")
    def palabra_clave(reel_id: str, palabra: str = Form(""), conn=Depends(conexion)):
        """D41: palabra clave del video, cambiada a mano. Se vuelve a mirar qué comentarios la traen."""
        from bot import conversacion

        palabra = palabra.strip().strip('"«»“”')[:40] or None
        with conn:
            conn.execute("UPDATE reels SET palabra_clave = ? WHERE id = ?", (palabra, reel_id))
            for cid, texto in conn.execute("SELECT id, texto FROM comentarios WHERE reel_id = ?", (reel_id,)).fetchall():
                conn.execute("UPDATE comentarios SET trae_palabra = ? WHERE id = ?",
                             (int(conversacion.trae_palabra(texto, palabra)), cid))
        return RedirectResponse("/reels", status_code=303)

    @app.post("/reels/{reel_id}/fijado-manual")
    def fijado_manual(reel_id: str, conn=Depends(conexion)):
        with conn:
            conn.execute("UPDATE reels SET fijado_estado = 'manual' WHERE id = ?", (reel_id,))
        return RedirectResponse("/reels", status_code=303)

    return app


def _vigilante() -> None:
    """Hilo del panel: cada minuto revisa latido del bot, cola grande y resumen diario (§8)."""
    import threading

    from bot import alertas

    def bucle():
        while True:
            conn = db.conectar()
            try:
                alertas.vigilar_una_vez(conn, ahora())
            finally:
                conn.close()
            threading.Event().wait(60)

    threading.Thread(target=bucle, name="vigilante", daemon=True).start()


def arrancar() -> None:
    import uvicorn

    db.preparar().close()
    _vigilante()
    uvicorn.run(crear_app(), host=HOST, port=PUERTO, log_level="warning")

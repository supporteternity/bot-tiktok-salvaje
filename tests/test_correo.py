"""F2 con servidor de correo local real (STARTTLS + usuario/contraseña) y la página «Correo» del panel.
Nada sale a internet."""

import importlib.util
import sys
from datetime import UTC, datetime
from email.header import decode_header, make_header
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot import alertas, config
from panel import app as panel_app
from smtp_local import CLAVE, USUARIO, ServidorLocal

_spec = importlib.util.spec_from_file_location(
    "probar_alertas", Path(__file__).resolve().parents[1] / "scripts" / "probar_alertas.py")
probar_alertas = importlib.util.module_from_spec(_spec)
sys.modules["probar_alertas"] = probar_alertas
_spec.loader.exec_module(probar_alertas)


@pytest.fixture
def smtp(monkeypatch):
    with ServidorLocal() as s:
        monkeypatch.setattr(alertas, "_contexto_tls", s.contexto_cliente)
        yield s


# ------------------------------------------------------------------ F2: las 5 alertas


def test_las_5_alertas_llegan_por_smtp_real_local(conn, smtp):
    config.guardar_secretos(smtp.datos())
    config.guardar(conn, "emails_alerta", ["dueno@academia.com", "setter@academia.com"])
    assert probar_alertas.forzar_cinco(conn, datetime.now(UTC)) == [True] * 5
    asuntos = [str(make_header(decode_header(m["Subject"]))) for m in smtp.mensajes]
    assert len(asuntos) == 5 and all(" Bot de TikTok · " in a for a in asuntos)
    assert smtp.mensajes[0]["To"] == "dueno@academia.com, setter@academia.com"
    assert smtp.mensajes[0]["From"] == f"Bot de TikTok <{USUARIO}>"


def test_contrasena_mala_no_rompe_nada_y_queda_registrado(conn, smtp):
    config.guardar_secretos(smtp.datos(clave="mala"))
    config.guardar(conn, "emails_alerta", ["dueno@academia.com"])
    assert probar_alertas.forzar_cinco(conn, datetime.now(UTC))[0] is False
    assert smtp.mensajes == []
    # Auditoría 26 nov (hallazgo 17): se apunta como «alerta_fallida» (no infla «Errores del bot»), explicado en
    # lenguaje sencillo, y los siguientes avisos esperan 15 min en vez de reintentar en cada vuelta.
    fila = conn.execute("SELECT detalle FROM eventos WHERE tipo = 'alerta_fallida'").fetchone()
    assert fila and fila[0].startswith("email no enviado") and "contraseña" in fila[0]
    assert conn.execute("SELECT COUNT(*) FROM eventos WHERE tipo = 'error'").fetchone()[0] == 0


def test_guardar_un_correo_que_funciona_acaba_la_espera_por_fallos(conn, smtp):
    """Si soporte arregla el correo en el panel, los avisos no esperan los 15 min del fallo anterior."""
    from bot.freno import Alerta

    config.guardar_secretos(smtp.datos(clave="mala"))
    config.guardar(conn, "emails_alerta", ["dueno@academia.com"])
    ahora = datetime.now(UTC)
    assert alertas.enviar_alerta(conn, Alerta("frenado", "a", "x"), ahora) is False
    config.guardar_secretos(smtp.datos())
    assert alertas.enviar_alerta(conn, Alerta("frenado", "b", "x"), ahora) is False  # en espera
    alertas.correo_arreglado(conn, ahora)
    assert alertas.enviar_alerta(conn, Alerta("frenado", "c", "x"), ahora) is True
    assert len(smtp.mensajes) == 1


def test_errores_explicados_en_lenguaje_sencillo(smtp):
    assert alertas.probar_correo(smtp.datos(), ["a@b.com"]) is None
    assert "contraseña de aplicación" in alertas.probar_correo(smtp.datos(clave="mala"), ["a@b.com"])
    sin_servidor = {**smtp.datos(), "SMTP_PUERTO": "1"}
    assert "No se pudo conectar" in alertas.probar_correo(sin_servidor, ["a@b.com"])
    import smtplib
    assert "No se pudo conectar" in alertas.explicar_error(smtplib.SMTPServerDisconnected("Connection unexpectedly closed"))


# ------------------------------------------------------------------ panel: primera vez y página «Correo»


def test_primera_vez_el_panel_pide_crear_la_contrasena(conn):
    c = TestClient(panel_app.crear_app(), base_url="http://127.0.0.1")
    assert "Crear contraseña" in c.get("/entrar").text
    r = c.post("/entrar/crear", data={"nueva": "corta", "repetida": "corta"})
    assert r.status_code == 400 and "al menos 6" in r.text
    r = c.post("/entrar/crear", data={"nueva": "segura123", "repetida": "otra1234"})
    assert r.status_code == 400 and "no coinciden" in r.text
    r = c.post("/entrar/crear", data={"nueva": "segura123", "repetida": "segura123"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"  # Inicio: la lista de lo que falta
    assert config.clave_coincide("segura123", config.cargar_secretos().get("PANEL_CONTRASENA"))
    assert "segura123" not in (config.carpeta_datos() / ".env").read_text(encoding="utf-8")  # hallazgo 19
    assert c.get("/correo").status_code == 200  # ya dentro
    # Con contraseña puesta, nadie puede volver a «crearla».
    otro = TestClient(panel_app.crear_app(), base_url="http://127.0.0.1")
    otro.post("/entrar/crear", data={"nueva": "intruso99", "repetida": "intruso99"})
    assert config.clave_coincide("segura123", config.cargar_secretos().get("PANEL_CONTRASENA"))


@pytest.fixture
def panel(conn, smtp, monkeypatch):
    config.guardar_secretos({"PANEL_CONTRASENA": "segura123"})
    monkeypatch.setattr(panel_app, "GMAIL", {"SMTP_HOST": "localhost", "SMTP_PUERTO": str(smtp.puerto)})
    c = TestClient(panel_app.crear_app(), base_url="http://127.0.0.1")
    c.post("/entrar", data={"contrasena": "segura123"})
    return c


def test_inicio_avisa_si_falta_el_correo(panel):
    assert "Falta configurar el correo de alertas" in panel.get("/").text


def test_gmail_con_espacios_prueba_y_guarda(panel, conn, smtp):
    r = panel.post("/correo", data={"clave_soporte_nueva": "soporte123", "clave_soporte": "soporte123", "proveedor": "gmail", "correo": USUARIO, "clave": "abcd efgh ijkl mnop",
                                     "avisar_a": "dueno@academia.com\nsetter@academia.com"})
    assert r.status_code == 200 and "¡Listo! El correo funciona" in r.text
    assert len(smtp.mensajes) == 1 and str(make_header(decode_header(smtp.mensajes[0]["Subject"]))) == "✅ Bot de TikTok · Correo de prueba: los avisos te van a llegar"
    s = config.cargar_secretos()
    assert (s.get("SMTP_USUARIO"), s.get("SMTP_CONTRASENA")) == (USUARIO, CLAVE)
    assert config.leer(conn, "emails_alerta") == ["dueno@academia.com", "setter@academia.com"]
    pagina = panel.get("/correo").text
    assert CLAVE not in pagina and "Guardada ✓" in pagina  # la contraseña nunca vuelve a enseñarse
    assert "Falta configurar el correo" not in panel.get("/").text


def test_si_la_prueba_falla_no_se_guarda_nada(panel, smtp):
    r = panel.post("/correo", data={"clave_soporte_nueva": "soporte123", "clave_soporte": "soporte123", "proveedor": "gmail", "correo": USUARIO, "clave": "zzzz zzzz zzzz zzzz"})
    assert r.status_code == 400 and "contraseña de aplicación" in r.text
    assert config.cargar_secretos().get("SMTP_CONTRASENA") is None


def test_validaciones_amigables(panel):
    r = panel.post("/correo", data={"clave_soporte_nueva": "soporte123", "clave_soporte": "soporte123", "proveedor": "gmail", "correo": "sin-arroba", "clave": CLAVE})
    assert r.status_code == 400 and "correo completo" in r.text
    r = panel.post("/correo", data={"clave_soporte_nueva": "soporte123", "clave_soporte": "soporte123", "proveedor": "gmail", "correo": USUARIO, "clave": "mipasswordnormal"})
    assert r.status_code == 400 and "16 letras" in r.text
    r = panel.post("/correo", data={"clave_soporte_nueva": "soporte123", "clave_soporte": "soporte123", "proveedor": "gmail", "correo": USUARIO, "clave": ""})
    assert r.status_code == 400 and "Escribe la contraseña" in r.text
    r = panel.post("/correo", data={"clave_soporte_nueva": "soporte123", "clave_soporte": "soporte123", "proveedor": "otro", "correo": USUARIO, "clave": "x", "servidor": ""})
    assert r.status_code == 400 and "servidor de correo" in r.text


def test_vacio_avisa_al_mismo_correo_y_se_puede_cambiar_sin_reescribir_la_contrasena(panel, conn, smtp):
    panel.post("/correo", data={"clave_soporte_nueva": "soporte123", "clave_soporte": "soporte123", "proveedor": "gmail", "correo": USUARIO, "clave": CLAVE, "avisar_a": ""})
    assert config.leer(conn, "emails_alerta") == [USUARIO]
    r = panel.post("/correo", data={"clave_soporte_nueva": "soporte123", "clave_soporte": "soporte123", "proveedor": "gmail", "correo": USUARIO, "clave": "", "avisar_a": "nuevo@academia.com"})
    assert r.status_code == 200 and config.leer(conn, "emails_alerta") == ["nuevo@academia.com"]
    assert config.cargar_secretos().get("SMTP_CONTRASENA") == CLAVE


def test_otro_proveedor(panel, smtp):
    r = panel.post("/correo", data={"clave_soporte_nueva": "soporte123", "clave_soporte": "soporte123", "proveedor": "otro", "correo": USUARIO, "clave": CLAVE,
                                     "servidor": "localhost", "puerto": str(smtp.puerto), "avisar_a": "a@b.com"})
    assert r.status_code == 200 and "¡Listo!" in r.text
    assert config.cargar_secretos().get("SMTP_HOST") == "localhost"


def test_ya_configurado_se_vuelve_a_probar_con_un_boton_sin_escribir_la_clave(panel, conn, smtp):
    assert "Primero configura el correo" in panel.post("/correo/probar").text
    panel.post("/correo", data={"clave_soporte_nueva": "soporte123", "clave_soporte": "soporte123", "proveedor": "gmail", "correo": USUARIO, "clave": CLAVE, "avisar_a": "dueno@academia.com"})
    pagina = panel.get("/correo").text
    assert "El correo de alertas está configurado" in pagina and "Enviar correo de prueba" in pagina
    assert "Configurado por soporte" not in pagina and "solo soporte" not in pagina.lower()
    assert "dueno@academia.com" in pagina and CLAVE not in pagina
    r = panel.post("/correo/probar")
    assert r.status_code == 200 and "¡Listo! El correo funciona" in r.text and len(smtp.mensajes) == 2


def test_los_tres_pasos_de_google_tienen_su_enlace(panel):
    pagina = panel.get("/correo").text
    for enlace in ("accounts.google.com/signup", "signinoptions/twosv", "myaccount.google.com/apppasswords"):
        assert enlace in pagina



def test_el_cliente_configura_y_cambia_el_correo_sin_clave_de_soporte(panel, conn, smtp):
    """27 nov (decisión del usuario, deshace D30): el correo lo configura el cliente, sin bloqueo."""
    datos = {"proveedor": "gmail", "correo": USUARIO, "clave": CLAVE, "avisar_a": "dueno@academia.com"}
    assert "clave_soporte" not in panel.get("/correo").text  # ni crear ni pedir clave de soporte
    r = panel.post("/correo", data=datos)
    assert r.status_code == 200 and "¡Listo! El correo funciona" in r.text
    assert config.cargar_secretos().get("SOPORTE_CLAVE") is None  # el correo ya no crea clave de soporte
    pagina = panel.get("/correo").text
    assert "Cambiar (solo soporte)" not in pagina and "Configurado por soporte" not in pagina
    r = panel.post("/correo", data={**datos, "clave": "", "avisar_a": "otro@academia.com"})
    assert r.status_code == 200 and config.leer(conn, "emails_alerta") == ["otro@academia.com"]


def test_con_clave_de_soporte_ya_creada_el_correo_sigue_sin_bloqueo(panel, conn, smtp):
    """Instalaciones de antes (el bot real) ya tienen SOPORTE_CLAVE: ahora solo sirve para quitar el freno."""
    config.guardar_secretos({"SOPORTE_CLAVE": config.resumen_clave("soporte123")})
    panel.post("/correo", data={"proveedor": "gmail", "correo": USUARIO, "clave": CLAVE, "avisar_a": "a@academia.com"})
    pagina = panel.get("/correo").text
    assert "clave_soporte" not in pagina and "Configurado por soporte" not in pagina
    r = panel.post("/correo", data={"proveedor": "gmail", "correo": USUARIO, "clave": "", "avisar_a": "b@academia.com"})
    assert r.status_code == 200 and config.leer(conn, "emails_alerta") == ["b@academia.com"]
    assert config.clave_soporte_correcta("soporte123")  # la clave del freno se conserva


def test_inicio_no_dice_que_el_correo_lo_hace_soporte(panel):
    texto = panel.get("/").text
    assert "Falta configurar el correo de alertas" in texto and "lo hace soporte" not in texto


def test_gmail_que_corta_tras_rechazar_la_clave_dice_contrasena_no_conectar(monkeypatch):
    """Gmail real (25 nov): 535 al primer método y corta si se prueba otro. Antes salía «No se pudo conectar»."""
    import smtplib

    class GmailFalso:
        def __init__(self, *a, **k):
            self.esmtp_features, self.intentos = {"auth": "LOGIN PLAIN XOAUTH2"}, 0

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, **k):
            pass

        def ehlo_or_helo_if_needed(self):
            pass

        def auth_plain(self, challenge=None):
            return ""

        def auth(self, mecanismo, objeto, **k):
            self.intentos += 1
            if self.intentos > 1:
                raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")

        def login(self, u, c):  # lo que hacía smtplib: prueba un método y luego otro
            try:
                self.auth("CRAM-MD5", None)
            except smtplib.SMTPAuthenticationError:
                self.auth("PLAIN", None)

    monkeypatch.setattr(smtplib, "SMTP", GmailFalso)
    datos = {"SMTP_HOST": "smtp.gmail.com", "SMTP_PUERTO": "587", "SMTP_USUARIO": "a@gmail.com",
             "SMTP_CONTRASENA": "abcdefghijklmnop", "SMTP_REMITENTE": "a@gmail.com"}
    texto = alertas.probar_correo(datos, ["b@b.com"])
    assert "contraseña" in texto and "No se pudo conectar" not in texto

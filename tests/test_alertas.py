from datetime import timedelta

import pytest

from bot import alertas, config
from bot.freno import Alerta, Freno
from bot.ritmo import iso
from conftest import AHORA, meter_comentario


class SMTPFalso:
    enviados: list = []

    def __init__(self, host, puerto, timeout=None):
        self.host, self.puerto = host, puerto

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def starttls(self, context=None):
        pass

    def login(self, usuario, contrasena):
        self.usuario = usuario

    def send_message(self, msg):
        SMTPFalso.enviados.append(msg)


@pytest.fixture
def correo(conn, monkeypatch):
    SMTPFalso.enviados = []
    monkeypatch.setattr(alertas.smtplib, "SMTP", SMTPFalso)
    (config.carpeta_datos() / ".env").write_text(
        "SMTP_HOST=smtp.ejemplo.com\nSMTP_PUERTO=587\nSMTP_USUARIO=bot@ejemplo.com\nSMTP_CONTRASENA=x\n", encoding="utf-8")
    config.guardar(conn, "emails_alerta", ["dueno@ejemplo.com", "setter@ejemplo.com"])
    return SMTPFalso.enviados


def test_envia_alerta_con_captura_adjunta(conn, correo, tmp_path):
    captura = tmp_path / "c.png"
    captura.write_bytes(b"\x89PNG falso")
    assert alertas.enviar_alerta(conn, Alerta("frenado", "Bot frenado", "Cuerpo", str(captura)), AHORA)
    msg = correo[0]
    assert msg["Subject"] == "Bot frenado" and msg["To"] == "dueno@ejemplo.com, setter@ejemplo.com"
    assert msg["From"] == "Bot de TikTok <bot@ejemplo.com>"  # en la bandeja se ve el nombre
    assert [p.get_filename() for p in msg.iter_attachments()] == ["c.png"]
    assert conn.execute("SELECT COUNT(*) FROM eventos WHERE tipo='alerta_email'").fetchone()[0] == 1


def test_sin_smtp_o_sin_destinatarios_no_envia_y_no_rompe(conn, monkeypatch):
    monkeypatch.setattr(alertas.smtplib, "SMTP", SMTPFalso)
    SMTPFalso.enviados = []
    assert not alertas.enviar_alerta(conn, Alerta("frenado", "x", "y"), AHORA)
    assert SMTPFalso.enviados == []


def test_la_misma_alerta_no_se_repite_antes_de_1h(conn, correo):
    a = Alerta("frenado", "Bot TikTok frenado por un aviso", "x")
    assert alertas.enviar_alerta(conn, a, AHORA)
    assert not alertas.enviar_alerta(conn, a, AHORA + timedelta(minutes=30))
    assert alertas.enviar_alerta(conn, Alerta("necesita_persona", "Bot parado: captcha", "y"), AHORA + timedelta(minutes=31))
    assert alertas.enviar_alerta(conn, a, AHORA + timedelta(hours=1, minutes=1))
    assert len(correo) == 3


def test_con_el_correo_roto_no_se_reintenta_cada_minuto_ni_cuenta_como_error_del_bot(conn, correo, monkeypatch):
    """Auditoría 26 nov (hallazgo 17): con el correo roto, el aviso «no responde» y el resumen se intentaban cada
    minuto y cada intento se apuntaba como «error» del bot (inflaba «Errores del bot» del resumen). Ahora, tras un
    fallo, ningún aviso se intenta en 15 min, y el fallo se apunta aparte."""
    import smtplib

    intentos = []

    class SMTPRoto(SMTPFalso):
        def login(self, usuario, contrasena):
            intentos.append(1)
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(alertas.smtplib, "SMTP", SMTPRoto)
    with conn:
        conn.execute("UPDATE estado_bot SET modo='activo', ultimo_latido=?", (iso(AHORA - timedelta(hours=2)),))
    for minuto in range(14):  # el vigilante del panel pasa cada minuto
        alertas.vigilar_una_vez(conn, AHORA + timedelta(minutes=minuto))
    assert len(intentos) == 1
    assert conn.execute("SELECT COUNT(*) FROM eventos WHERE tipo = 'error'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM eventos WHERE tipo = 'alerta_fallida'").fetchone()[0] == 1
    alertas.vigilar_una_vez(conn, AHORA + timedelta(minutes=16))  # pasada la espera, se vuelve a intentar
    assert len(intentos) == 2


def test_el_telefono_del_lead_no_queda_en_el_registro_del_bot(conn, monkeypatch, caplog):
    """Hallazgo 18: el asunto del correo del lead lleva su teléfono y se escribía tal cual en bot.log."""
    import logging

    monkeypatch.setattr(alertas, "correo_configurado", lambda: False)
    with caplog.at_level(logging.INFO):
        for asunto in ("📞 Bot de TikTok · Lead nuevo: @ana · 801 555 1234", "Lead nuevo: @pepe · +52 1 55 1234 5678"):
            monkeypatch.setattr(alertas, "_ultimo_aviso_sin_correo", None)
            alertas._enviar(asunto, "cuerpo", ["a@b.com"])
    texto = caplog.text
    assert "1234" not in texto and "555" not in texto
    assert "@ana" in texto and "@pepe" in texto  # el resto del asunto sí, para saber qué aviso no salió


def test_alerta_real_del_freno(conn, correo):
    r = Freno(conn).aviso("captcha", "", None, AHORA)
    alertas.enviar_alerta(conn, r.alerta, AHORA)
    assert "captcha" in correo[0]["Subject"]
    assert "Reanudar" in correo[0].get_body().get_content()


def test_cola_grande_maximo_un_email_cada_12h(conn, correo):
    config.guardar(conn, "cola_alerta", 2)
    for i in range(3):
        meter_comentario(conn, f"c{i}", f"u{i}")
    assert alertas.revisar_cola(conn, AHORA)
    assert not alertas.revisar_cola(conn, AHORA + timedelta(hours=11))
    assert alertas.revisar_cola(conn, AHORA + timedelta(hours=12, minutes=1))
    assert "3" in correo[0]["Subject"]


def test_cola_pequena_no_avisa(conn, correo):
    meter_comentario(conn, "c1", "ana")
    assert not alertas.revisar_cola(conn, AHORA)


def test_bot_sin_actividad(conn, correo):
    with conn:
        conn.execute("UPDATE estado_bot SET modo='activo', ultimo_latido=?", (iso(AHORA - timedelta(minutes=10)),))
    assert not alertas.revisar_latido(conn, AHORA)
    with conn:
        conn.execute("UPDATE estado_bot SET ultimo_latido=?", (iso(AHORA - timedelta(minutes=16)),))
    assert alertas.revisar_latido(conn, AHORA)
    assert correo[0]["Subject"].startswith("🔴 Bot de TikTok · No responde desde hace")


def test_sin_actividad_no_avisa_si_esta_frenado_o_descansando(conn, correo):
    for modo in ("frenado", "descansando"):
        with conn:
            conn.execute("UPDATE estado_bot SET modo=?, ultimo_latido=?", (modo, iso(AHORA - timedelta(hours=3))))
        assert not alertas.revisar_latido(conn, AHORA)


def test_resumen_diario_a_las_9_de_utah_una_vez(conn, correo):
    # AHORA = 18:00 UTC = 11:00 en Utah (MST, UTC-7) → ya pasó la hora del resumen
    ayer = AHORA - timedelta(days=1)
    with conn:
        conn.execute("INSERT INTO acciones (tipo, resultado, hecho_en) VALUES ('dm','ok',?), ('dm','cerrado',?), ('respuesta_alt','ok',?)",
                     (iso(ayer), iso(ayer), iso(ayer)))
    meter_comentario(conn, "c1", "ana", hace_min=60 * 24)
    assert alertas.revisar_resumen(conn, AHORA)
    assert not alertas.revisar_resumen(conn, AHORA + timedelta(hours=1))
    cuerpo = correo[0].get_body().get_content()
    assert "Mensajes privados enviados: 1" in cuerpo and "Personas con los mensajes cerrados: 1 (50 %" in cuerpo
    assert "Ahora mismo" in cuerpo
    assert correo[0]["Subject"].startswith("📊 Bot de TikTok · Ayer: 1 mensaje y 1 respuesta")


def test_resumen_no_sale_antes_de_las_9(conn, correo):
    temprano = AHORA.replace(hour=14)  # 07:00 en Utah
    assert not alertas.revisar_resumen(conn, temprano)


def test_vigilante_revisa_todo_y_no_muere_si_algo_falla(conn, correo, monkeypatch):
    with conn:
        conn.execute("UPDATE estado_bot SET modo='activo', ultimo_latido=?", (iso(AHORA - timedelta(hours=1)),))
    monkeypatch.setattr(alertas, "revisar_cola", lambda c, a: 1 / 0)
    alertas.vigilar_una_vez(conn, AHORA)
    asuntos = [m["Subject"] for m in correo]
    assert any("No responde" in a for a in asuntos) and any(a.startswith("📊") for a in asuntos)

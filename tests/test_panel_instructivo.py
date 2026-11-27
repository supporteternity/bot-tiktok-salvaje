"""Panel · instructivo del cliente (§17): Leads, textos nuevos, ajustes nuevos y palabra clave."""

from bot import config, leads, textos
from conftest import AHORA, meter_comentario
from test_panel import cliente  # noqa: F401 — fixture


def test_paginas_nuevas_responden(cliente):  # noqa: F811
    for ruta in ("/leads", "/leads?filtro=sin_hablar", "/leads?filtro=hablados", "/leads.csv"):
        assert cliente.get(ruta).status_code == 200
    assert 'href="/leads"' in cliente.get("/").text  # en el menú


def test_leads_lista_marca_hablado_y_exporta(cliente, conn, monkeypatch):  # noqa: F811
    monkeypatch.setattr(leads, "_correo", lambda *a: True)
    with conn:
        conn.execute("UPDATE reels SET palabra_clave = 'VENTAS' WHERE id = 'r1'")
    lead_id = leads.registrar(conn, "u:ana", "8015551234", AHORA)
    html = cliente.get("/leads").text
    assert "@ana" in html and "801 555 1234" in html and "VENTAS" in html and "wa.me/8015551234" in html
    r = cliente.post(f"/leads/{lead_id}/hablado", data={"vendedor": "Carlos"}, follow_redirects=False)
    assert r.status_code == 303
    assert conn.execute("SELECT vendedor FROM leads").fetchone()[0] == "Carlos"
    assert conn.execute("SELECT etapa FROM contactos WHERE user_id = 'u:ana'").fetchone()[0] == "D"
    assert conn.execute("SELECT tipo FROM envios").fetchone()[0] == "seguimiento"
    assert "Ya hablamos · Carlos" in cliente.get("/leads").text
    csv = cliente.get("/leads.csv").text.lstrip("﻿")
    assert csv.splitlines()[0].startswith("fecha,usuario_tiktok") and "@ana" in csv and "8015551234" in csv


def test_personas_que_esperan_al_equipo(cliente, conn):  # noqa: F811
    with conn:
        conn.execute("UPDATE contactos SET humano = 1, humano_motivo = 'pregunta algo que el bot no contesta: «precio?»' "
                     "WHERE user_id = 'u:ana'")
    assert "precio?" in cliente.get("/leads").text
    assert "persona espera" in cliente.get("/").text
    cliente.post("/leads/persona/u:ana/atendida")
    assert conn.execute("SELECT humano FROM contactos WHERE user_id = 'u:ana'").fetchone()[0] == 2  # sigue sin bot
    assert "precio?" not in cliente.get("/leads").text
    cliente.post("/leads/persona/u:ana/devolver")
    assert conn.execute("SELECT humano FROM contactos WHERE user_id = 'u:ana'").fetchone()[0] == 0


def test_textos_nuevos_y_numero_nunca_en_publico(cliente, conn):  # noqa: F811
    textos.sembrar(conn)
    html = cliente.get("/textos").text
    for titulo in ("Recordatorio", "Cuando deja su número", "Seguimiento", "Bienvenida"):
        assert titulo in html
    assert "¿cómo te fue con Carlos?" in html  # vista previa con {vendedor}
    r = cliente.post("/textos/nuevo", data={"tipo": "respuesta", "contenido": "Llámame al 801 555 1234"},
                     follow_redirects=True)
    assert "parece llevar un número de teléfono" in r.text
    assert conn.execute("SELECT COUNT(*) FROM textos WHERE contenido LIKE '%801 555%'").fetchone()[0] == 0
    # en un mensaje privado sí se puede
    cliente.post("/textos/nuevo", data={"tipo": "pedir_numero", "contenido": "Pásame tu número, ej. 801 555 1234"})
    assert conn.execute("SELECT COUNT(*) FROM textos WHERE contenido LIKE '%801 555%'").fetchone()[0] == 1


def test_ajustes_nuevos_se_guardan_y_validan(cliente, conn):  # noqa: F811
    base = {"modo_publica": "todos", "landing": "https://academia.com", "caducidad_dias": "0",
            "descanso_desde": "02:00", "descanso_hasta": "04:00"}
    r = cliente.post("/ajustes", data={**base, "orden_cola": "videos_recientes", "recordatorio_cada_dias": "2",
                                       "leer_bandeja": "1", "max_respuestas_auto": "4", "palabras_humano": "precio\nqueja",
                                       "palabras_interes": "me interesa", "emails_leads": "ventas@academia.com",
                                       "webhook_leads": "https://services.leadconnectorhq.com/hooks/x",
                                       "seguimiento_espera_h": "2", "bienvenida_seguidores": "1",
                                       "enlace_youtube": "https://youtu.be/abc"})
    assert r.status_code == 200
    assert config.leer(conn, "orden_cola") == "videos_recientes"
    assert config.leer(conn, "recordatorio_cada_dias") == 2 and config.leer(conn, "max_respuestas_auto") == 4
    assert config.leer(conn, "palabras_humano") == ["precio", "queja"]
    assert config.leer(conn, "emails_leads") == ["ventas@academia.com"]
    assert config.leer(conn, "enlace_youtube") == "https://youtu.be/abc"
    assert config.leer(conn, "seguimiento_espera_h") == 2 and config.leer(conn, "leer_bandeja") is True
    for malo in ({"webhook_leads": "ftp://x"}, {"emails_leads": "no-es-correo"}, {"recordatorio_cada_dias": "-1"},
                 {"enlace_youtube": "youtu.be"}):
        r = cliente.post("/ajustes", data={**base, **malo})
        assert r.status_code == 400, malo
    assert config.leer(conn, "enlace_youtube") == "https://youtu.be/abc"


def test_palabra_clave_del_reel(cliente, conn):  # noqa: F811
    meter_comentario(conn, "c3", "pepe", reel="r1", texto="¡¡VENTAS!! 🔥")
    r = cliente.post("/reels/r1/palabra", data={"palabra": '"Ventas"'}, follow_redirects=False)
    assert r.status_code == 303
    assert conn.execute("SELECT palabra_clave FROM reels WHERE id = 'r1'").fetchone()[0] == "Ventas"
    assert conn.execute("SELECT trae_palabra FROM comentarios WHERE id = 'c3'").fetchone()[0] == 1
    assert conn.execute("SELECT trae_palabra FROM comentarios WHERE id = 'c1'").fetchone()[0] == 0
    assert "1 de" in cliente.get("/reels").text


def test_ajustes_cuenta_cambiar_y_cerrar_sesion(cliente, conn, monkeypatch):  # noqa: F811
    """En Ajustes se ve la cuenta conectada y se puede cambiar de cuenta o cerrar sesión, volviendo a Ajustes."""
    from bot import cuenta, navegador

    llamadas = []
    abierta = {"v": False}
    monkeypatch.setattr(navegador, "ventana_sesion_abierta", lambda: abierta["v"])
    monkeypatch.setattr(cuenta, "conectar", lambda: llamadas.append("conectar") or abierta.update(v=True) or True)
    monkeypatch.setattr(cuenta, "listo", lambda: llamadas.append("listo") or abierta.update(v=False) or True)

    def desconectar(c, ahora):
        llamadas.append("desconectar")
        config.guardar(c, "cuenta", "")
        with c:
            c.execute("UPDATE estado_bot SET modo = 'sin_cuenta' WHERE id = 1")

    monkeypatch.setattr(cuenta, "desconectar", desconectar)
    with conn:
        conn.execute("UPDATE estado_bot SET modo = 'activo' WHERE id = 1")

    html = cliente.get("/ajustes").text
    assert "Tu cuenta de TikTok" in html and "@academia" in html
    assert "Cambiar de cuenta" in html and "Cerrar sesión" in html

    r = cliente.post("/cuenta/desconectar", data={"volver": "ajustes"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/ajustes#cuenta"
    assert llamadas == ["desconectar"] and "Sin conectar" in cliente.get("/ajustes").text

    config.guardar(conn, "cuenta", "academia")
    with conn:
        conn.execute("UPDATE estado_bot SET modo = 'activo' WHERE id = 1")
    r = cliente.post("/cuenta/cambiar", data={"volver": "ajustes"})
    assert llamadas == ["desconectar", "desconectar", "conectar"]
    assert "Ya entré" in r.text and "código QR" in r.text
    r = cliente.post("/cuenta/listo", data={"volver": "ajustes"})
    assert llamadas[-1] == "listo" and "Sesión guardada" in r.text
    # desde la página Cuenta sigue volviendo a Cuenta
    r = cliente.post("/cuenta/desconectar", follow_redirects=False)
    assert r.headers["location"] == "/cuenta"


def test_panel_con_datos_de_prueba_no_toca_el_bot_real(conn, monkeypatch):
    """Una vista de prueba del panel (otra carpeta de datos) nunca para ni arranca los servicios de verdad, y
    «Conectar» funciona aunque la carpeta de datos aún no tenga «logs»."""
    import shutil
    import subprocess

    from bot import cuenta, instalador, navegador

    assert instalador.servicio_instalado("bot") is False  # BOT_TIKTOK_DATOS puesto (como en las vistas de prueba)
    shutil.rmtree(config.carpeta_datos() / "logs", ignore_errors=True)
    lanzados = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: lanzados.append(a))
    abierta = iter([False, True])
    monkeypatch.setattr(navegador, "ventana_sesion_abierta", lambda: next(abierta, True))
    assert cuenta.conectar() is True
    assert lanzados and (config.carpeta_datos() / "logs" / "sesion.log").is_file()

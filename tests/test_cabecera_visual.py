"""La cabecera del panel, abierta en un navegador de verdad: nada se monta ni se sale, a ningún ancho."""
import socket
from datetime import UTC, datetime
import threading
import time

import pytest
import uvicorn

from bot import config
from panel.app import crear_app

playwright = pytest.importorskip("playwright.sync_api")

ANCHOS = [340, 360, 390, 430, 480, 520, 560, 600, 640, 720, 768, 900, 1024, 1100, 1119, 1120, 1200, 1280, 1366, 1440, 1920]

MEDIR = """(() => {
  const d = document.querySelector('.cabecera-dentro'); const caja = d.getBoundingClientRect();
  const piezas = [...d.querySelectorAll('.marca-placa, .marca-sub, .nav-escritorio a, .pildora-estado, .boton-tema, .menu-movil summary')]
    .filter(e => e.getBoundingClientRect().width > 0);
  const malos = [];
  for (let i = 0; i < piezas.length; i++) {
    const a = piezas[i].getBoundingClientRect();
    if (a.right > caja.right - 2 || a.left < caja.left + 2) malos.push('se sale: ' + piezas[i].className + piezas[i].textContent.trim());
    for (let j = i + 1; j < piezas.length; j++) {
      const b = piezas[j].getBoundingClientRect();
      if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1)
        malos.push('se montan: ' + piezas[i].textContent.trim() + ' / ' + piezas[j].textContent.trim());
    }
    if (!piezas[i].matches('.marca-sub') && (a.width < 40 || a.height < 40)) malos.push('demasiado pequeño para tocar: ' + piezas[i].className);
  }
  const nav = d.querySelector('.nav-escritorio');
  if (nav.offsetHeight && new Set([...nav.children].map(x => Math.round(x.getBoundingClientRect().top))).size > 1)
    malos.push('el menú se parte en dos líneas');
  if (document.documentElement.scrollWidth > window.innerWidth) malos.push('la página se desborda');
  return malos;
})()"""


@pytest.fixture
def panel(conn):
    (config.carpeta_datos() / ".env").write_text("PANEL_CONTRASENA=clave-panel\n", encoding="utf-8")
    with conn:   # bot trabajando, con señal reciente: la píldora de estado sale con su texto más largo habitual
        conn.execute("UPDATE estado_bot SET modo = 'activo', ultimo_latido = ? WHERE id = 1",
                     (datetime.now(UTC).isoformat(timespec="seconds"),))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        puerto = s.getsockname()[1]
    servidor = uvicorn.Server(uvicorn.Config(crear_app(), host="127.0.0.1", port=puerto, log_level="error"))
    hilo = threading.Thread(target=servidor.run, daemon=True)
    hilo.start()
    while not servidor.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{puerto}"
    servidor.should_exit = True
    hilo.join(5)


@pytest.mark.parametrize("letra", [16, 20])
@pytest.mark.parametrize("tema", ["light", "dark"])
def test_cabecera_sin_solapes_a_ningun_ancho(panel, tema, letra):
    with playwright.sync_playwright() as p:
        navegador = p.chromium.launch()
        pagina = navegador.new_page(viewport={"width": 1280, "height": 800}, color_scheme=tema)
        pagina.goto(panel + "/entrar")
        pagina.fill("input[name=contrasena]", "clave-panel")
        with pagina.expect_navigation():
            pagina.keyboard.press("Enter")
        pagina.evaluate("document.fonts.ready")
        errores = {}
        assert pagina.locator(".pildora-estado").count() == 1, "sin píldora de estado"
        # letra del navegador más grande (Chrome → Ajustes → Aspecto → Tamaño de fuente): el menú debe adaptarse
        pagina.context.new_cdp_session(pagina).send("Page.setFontSizes", {"fontSizes": {"standard": letra, "fixed": 13}})
        pagina.reload()
        pagina.evaluate("document.fonts.ready")
        for ancho in ANCHOS:
            pagina.set_viewport_size({"width": ancho, "height": 800})
            if malos := pagina.evaluate(MEDIR):
                errores[ancho] = malos
        navegador.close()
    assert errores == {}

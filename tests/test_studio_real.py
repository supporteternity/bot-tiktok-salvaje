"""TikTok Studio (C1) con datos reales capturados el 25 nov: JSON de commentsV2 y HTML de la lista."""

import gzip
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from bot.pantallas.reel import es_lista_comentarios, leer_api_comentarios
from bot.selectores import sel

FIX = Path(__file__).parent / "fixtures"


def test_json_real_de_studio_da_los_tres_comentarios_con_su_reel():
    datos = json.loads((FIX / "studio_commentsV2.json").read_text(encoding="utf-8"))
    assert es_lista_comentarios(datos)
    leidos, hay_mas = leer_api_comentarios(datos)
    assert hay_mas is False
    assert [c.texto for c in leidos] == ["Me interesa", "Info", "Diagnóstico"]
    c = leidos[0]
    assert (c.id, c.reel_id, c.usuario, c.nombre, c.user_id) == (
        "7100000000000000001", "7100000000000000002", "persona.uno", "persona.uno", "7000000000000000002")
    assert c.comentado_en.year == 2026


def test_selectores_de_studio_en_el_html_real():
    html = gzip.decompress((FIX / "studio_comentarios.html.gz").read_bytes()).decode("utf-8")
    with sync_playwright() as p:
        nav = p.chromium.launch(channel="chrome")
        try:
            pagina = nav.new_page(java_script_enabled=False)
            pagina.set_content(html)
            assert pagina.locator(sel("studio_lista_comentarios").css).count() == 1
            assert pagina.locator(sel("studio_comentario").css).count() == 3
        finally:
            nav.close()

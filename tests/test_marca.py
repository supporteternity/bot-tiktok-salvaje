"""Marca SALVAJE en el panel: archivos del logo, fuente, colores y dónde aparecen."""
import hashlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bot import config
from panel.app import crear_app

ESTATICOS = Path(__file__).resolve().parent.parent / "panel" / "estaticos"
MARCA = ESTATICOS / "marca"

# nombre: (ancho, alto) exacto, o None si solo se exige que sea PNG válido
ARCHIVOS_MARCA = {
    "salvaje-nombre.png": None,
    "salvaje-logo.png": None,
    "icono-32.png": (32, 32),
    "icono-180.png": (180, 180),
}


@pytest.fixture
def anonimo(conn):
    (config.carpeta_datos() / ".env").write_text("PANEL_CONTRASENA=clave-panel\n", encoding="utf-8")
    return TestClient(crear_app(), base_url="http://127.0.0.1")


def png_info(ruta: Path) -> tuple[int, int, int]:
    """(ancho, alto, tipo de color) leídos de la cabecera IHDR; tipo 6 = RGBA."""
    datos = ruta.read_bytes()[:33]
    assert datos[:8] == b"\x89PNG\r\n\x1a\n", ruta.name
    return int.from_bytes(datos[16:20], "big"), int.from_bytes(datos[20:24], "big"), datos[25]


def test_archivos_de_marca_existen_y_pesan_poco():
    total = 0
    for nombre, tam in ARCHIVOS_MARCA.items():
        ruta = MARCA / nombre
        assert ruta.is_file(), nombre
        ancho, alto, color = png_info(ruta)
        if tam:
            assert (ancho, alto) == tam, nombre
        if nombre == "salvaje-nombre.png":
            # ~2,5× de lo que se ve (28 px); letras con su negro y borde difuminado (RGBA)
            assert alto == 72 and color == 6
        if nombre == "salvaje-logo.png":
            assert 400 <= ancho <= 460                         # tamaño original, sin ampliar
        total += ruta.stat().st_size
    # Casco de gladiador del logo (pedido del cliente, 26 nov): 3× de lo que se ve (32 px), con su negro (RGBA).
    ancho, alto, color = png_info(MARCA / "salvaje-casco.png")
    assert alto == 96 and 70 <= ancho <= 100 and color == 6
    total += (MARCA / "salvaje-casco.png").stat().st_size
    assert not (MARCA / "salvaje-escudo.svg").exists()  # el escudo con la «S» ya no se usa
    assert total < 150_000
    assert not (MARCA / "original.jpg").exists() and not (MARCA / "salvaje-s.png").exists()


def test_estaticos_de_marca_se_sirven(anonimo):
    r = anonimo.get("/estaticos/marca/salvaje-nombre.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    r = anonimo.get("/estaticos/marca/salvaje-casco.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"


def test_fuente_local_y_sin_fraunces(anonimo):
    pagina = anonimo.get("/entrar").text
    assert "/estaticos/fuentes/montserrat.woff2" in pagina
    css = (ESTATICOS / "estilo.css").read_text(encoding="utf-8")
    assert "Montserrat" in css
    assert "Fraunces" not in css and "fonts.googleapis" not in css and "--serif" not in css
    fuente = ESTATICOS / "fuentes" / "montserrat.woff2"
    assert fuente.is_file() and fuente.stat().st_size < 80_000
    assert not list((ESTATICOS / "fuentes").glob("fraunces*"))


def bloque_tokens(css: str, selector: str) -> dict[str, str]:
    """Variables --x: #hex de la primera regla cuyo selector es exactamente `selector`."""
    import re
    inicio = css.index(selector + " {") + len(selector) + 2
    cuerpo = css[inicio:css.index("\n}", inicio)]
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})\b", cuerpo))


def contraste(a: str, b: str) -> float:
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    x, y = sorted((lum(a), lum(b)), reverse=True)
    return (x + 0.05) / (y + 0.05)


PARES_TEXTO = [("tinta", "fondo"), ("tinta", "superficie"), ("tenue", "fondo"), ("tenue", "superficie"),
               ("tenue", "superficie-2"), ("acento", "superficie"), ("acento", "fondo"), ("bronce-texto", "superficie")]


def test_css_sin_colores_viejos():
    css = (ESTATICOS / "estilo.css").read_text(encoding="utf-8").upper()
    for viejo in ("#8E0D1C", "#6F0714", "#3E040B", "#1A0306", "#D6A84F", "#5A0611", "#2A0509", "--VINO", "--ORO"):
        assert viejo not in css, viejo


def test_contraste_de_la_paleta_en_claro_y_oscuro():
    css = (ESTATICOS / "estilo.css").read_text(encoding="utf-8")
    claro = bloque_tokens(css, ":root")
    oscuro = {**claro, **bloque_tokens(css, ':root[data-tema="oscuro"]')}
    for nombre, t in (("claro", claro), ("oscuro", oscuro)):
        for texto, fondo in PARES_TEXTO:
            assert contraste(t[texto], t[fondo]) >= 4.5, (nombre, texto, fondo)
    # botón principal: texto blanco sobre los dos extremos del degradado cobre
    for color in (claro["cobre-boton-arriba"], claro["cobre-boton-abajo"]):
        assert contraste("#FFFFFF", color) >= 4.5, color
    # los dos bloques oscuros (automático y elegido con el botón) dicen lo mismo
    inicio = css.index("@media (prefers-color-scheme: dark)")
    auto = bloque_tokens(css[inicio:], '  :root:not([data-tema="claro"])')
    assert auto == bloque_tokens(css, ':root[data-tema="oscuro"]')


# Huellas (sha256) de los nombres que no pueden salir en lo que ve el cliente: así esta prueba no los escribe.
NOMBRES_PROHIBIDOS = {"0c08c0d223af7f43cbf3543b4a3559cd0cc0b37893c38a2fc8319e204e80c2c2"}


def _huellas(texto: str) -> set[str]:
    return {hashlib.sha256(p.encode()).hexdigest() for p in re.findall(r"[a-záéíóúñ]+", texto)}


def test_nada_de_la_agencia_en_el_panel():
    for ruta in [ESTATICOS / "estilo.css", ESTATICOS / "panel.js", *(ESTATICOS.parent / "plantillas").glob("*.html")]:
        texto = ruta.read_text(encoding="utf-8").lower()
        assert "agencia" not in texto and not (_huellas(texto) & NOMBRES_PROHIBIDOS), ruta.name


def test_cabecera_con_marca(anonimo):
    anonimo.post("/entrar", data={"contrasena": "clave-panel"})
    pagina = anonimo.get("/").text
    assert "/estaticos/marca/salvaje-nombre.png" in pagina and 'alt="SALVAJE"' in pagina
    assert "/estaticos/marca/salvaje-casco.png" in pagina and "salvaje-escudo" not in pagina
    assert 'aria-label="SALVAJE, ir a Inicio"' in pagina
    assert "<title>Inicio · SALVAJE</title>" in pagina
    # el diálogo de «Apagar el bot» está en la página, no dentro del título
    assert pagina.index('<dialog id="confirmar-apagar"') > pagina.index("<main")
    assert '/estaticos/marca/icono-32.png' in pagina and 'rel="apple-touch-icon" href="/estaticos/marca/icono-180.png' in pagina
    assert "data:image/svg+xml" not in pagina           # fuera el avión de papel


def test_entrar_con_logo(anonimo):
    pagina = anonimo.get("/entrar").text
    assert "/estaticos/marca/salvaje-logo.png" in pagina and 'alt="SALVAJE"' in pagina
    assert "· SALVAJE</title>" in pagina and "/estaticos/marca/icono-32.png" in pagina


def test_todos_los_iconos_usados_existen():
    import re
    plantillas = ESTATICOS.parent / "plantillas"
    comun = (plantillas / "_comun.html").read_text(encoding="utf-8")
    definidos = set(re.findall(r'<symbol id="i-([\w-]+)"', comun))
    usados = set()
    for ruta in plantillas.glob("*.html"):
        texto = ruta.read_text(encoding="utf-8")
        usados |= set(re.findall(r"c\.icono\('([\w-]+)'", texto)) | set(re.findall(r"'icono': '([\w-]+)'", texto))
    assert usados - definidos == set()


def test_imagenes_de_marca_con_version_para_la_cache(anonimo):
    """Si se cambian los archivos de marca/, el navegador no debe seguir mostrando los viejos."""
    import re
    anonimo.post("/entrar", data={"contrasena": "clave-panel"})
    for pagina in (anonimo.get("/").text, anonimo.get("/entrar").text):
        for url in re.findall(r'"(/estaticos/marca/[^"]+)"', pagina):
            assert "?v=" in url, url

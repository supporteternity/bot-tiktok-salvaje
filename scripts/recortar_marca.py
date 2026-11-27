"""Prepara los archivos de marca del panel a partir del logo del cliente (se ejecuta una vez, a mano).

Uso (desde la carpeta del bot):
    uv run --with pillow python "scripts/recortar_marca.py" "ruta/al/logo.jpg"

Espera el logo cuadrado de 640 px sobre fondo negro (casco arriba, palabra SALVAJE abajo).
Escribe en panel/estaticos/marca/:
- salvaje-nombre.png: la palabra SALVAJE con su propio negro y el borde difuminado (va sobre la placa negra).
  No se quita el fondo por luminosidad: al reducirla, eso borraba las caras oscuras de las letras.
- salvaje-logo.png: casco + palabra, fondo transparente (portada oscura de «Entrar»).
- salvaje-casco.png: el casco de gladiador del logo, con su propio negro y el borde difuminado (va en la placa
  negra de la cabecera, delante de la palabra; pedido del cliente el 26 nov: antes era un escudo con la «S»).
- icono-32.png e icono-180.png: el casco sobre fondo negro (pestaña del navegador y acceso del móvil).
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

DESTINO = Path(__file__).resolve().parent.parent / "panel" / "estaticos" / "marca"
LADO = 640
ZONA_NOMBRE = (60, 455, 580, 610)    # franja donde está la palabra SALVAJE
UMBRAL = (28, 70)                    # luminosidad: por debajo = fondo, por encima = opaco (solo el logo completo)
ALTO_NOMBRE = 72                     # se muestra a 28 px: ~2,5× para pantallas Retina
ZONA_CASCO = (0, 0, LADO, 450)       # parte de arriba: el casco (la palabra empieza más abajo)
ALTO_CASCO = 96                      # se muestra a 32 px: 3× para pantallas Retina


def alfa_por_luz(im: Image.Image) -> Image.Image:
    """Fondo negro → transparente, con borde suave entre los dos umbrales."""
    bajo, alto = UMBRAL
    alfa = im.convert("L").point(lambda v: 0 if v <= bajo else 255 if v >= alto else (v - bajo) * 255 // (alto - bajo))
    salida = im.convert("RGBA")
    salida.putalpha(alfa)
    return salida


def caja_visible(im_rgba: Image.Image) -> tuple[int, int, int, int]:
    return im_rgba.getchannel("A").point(lambda v: 255 if v > 40 else 0).getbbox()


def nombre(im: Image.Image) -> Image.Image:
    zona = im.crop(ZONA_NOMBRE)
    x0, y0, x1, y1 = zona.convert("L").point(lambda v: 255 if v > 60 else 0).getbbox()
    margen = 10
    zona = zona.crop((x0 - margen, y0 - margen, x1 + margen, y1 + margen))
    zona = zona.resize((round(zona.width * ALTO_NOMBRE / zona.height), ALTO_NOMBRE), Image.LANCZOS)
    alfa = Image.new("L", zona.size, 0)
    ImageDraw.Draw(alfa).rectangle((5, 4, zona.width - 6, zona.height - 5), fill=255)
    salida = zona.convert("RGBA")
    salida.putalpha(alfa.filter(ImageFilter.GaussianBlur(3)))
    return salida


def _caja_casco(im: Image.Image) -> tuple[int, int, int, int]:
    zona = im.crop(ZONA_CASCO)
    return zona.convert("L").point(lambda v: 255 if v > 70 else 0).getbbox()


def casco(im: Image.Image) -> Image.Image:
    x0, y0, x1, y1 = _caja_casco(im)
    margen = 8
    zona = im.crop((x0 - margen, y0 - margen, x1 + margen, y1 + margen))
    zona = zona.resize((round(zona.width * ALTO_CASCO / zona.height), ALTO_CASCO), Image.LANCZOS)
    alfa = Image.new("L", zona.size, 0)
    ImageDraw.Draw(alfa).ellipse((2, 2, zona.width - 3, zona.height - 3), fill=255)
    salida = zona.convert("RGBA")
    salida.putalpha(alfa.filter(ImageFilter.GaussianBlur(4)))
    return salida


def iconos_del_casco(im: Image.Image) -> None:
    """Cuadrado negro con el casco centrado (la pestaña del navegador y el icono del móvil)."""
    x0, y0, x1, y1 = _caja_casco(im)
    lado = max(x1 - x0, y1 - y0) + 24
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    cuadro = im.crop((cx - lado // 2, cy - lado // 2, cx + lado // 2, cy + lado // 2))
    for medida in (32, 180):
        icono = cuadro.resize((medida, medida), Image.LANCZOS)
        icono.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(
            DESTINO / f"icono-{medida}.png", optimize=True)  # 128 colores: pesa poco y no se nota


def main(origen: Path) -> None:
    im = Image.open(origen).convert("RGB")
    if im.size != (LADO, LADO):
        sys.exit(f"Se esperaba un logo de {LADO}×{LADO} px y es {im.width}×{im.height}.")
    DESTINO.mkdir(parents=True, exist_ok=True)
    nombre(im).save(DESTINO / "salvaje-nombre.png", optimize=True)
    todo = alfa_por_luz(im)
    todo.crop(caja_visible(todo)).quantize(colors=256, method=Image.Quantize.FASTOCTREE,
                                           dither=Image.Dither.NONE).save(DESTINO / "salvaje-logo.png", optimize=True)
    casco(im).save(DESTINO / "salvaje-casco.png", optimize=True)
    iconos_del_casco(im)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))

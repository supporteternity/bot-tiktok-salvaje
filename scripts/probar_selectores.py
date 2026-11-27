"""Calibración (B1): abre las pantallas de TikTok con el Chrome del bot y dice OK / FALLA por selector.

NO actúa: no escribe, no envía, no publica, no fija. Solo navega y mira (abrir el chat = pulsar «Mensaje»).
El vigía revisa cada página: si sale un aviso, captcha o verificación, se para y lo dice.
Necesita la sesión del bot iniciada (python -m bot iniciar-sesion). Si el bot está instalado como
servicio, se para mientras dura la prueba y se vuelve a arrancar al final.

Uso (desde BOT-TIKTOK-APP):
    uv run python scripts/probar_selectores.py --usuario OTRA_CUENTA [--reel ID_DEL_REEL]

--usuario: una cuenta ajena para mirar su perfil y el chat con ella (en pruebas, la cuenta C).
--reel:    un reel de la cuenta propia (si no se da, el más reciente guardado en la base).
Los selectores que solo aparecen al actuar (botón de enviar, menú de fijar…) o sin sesión salen como
«SIN COMPROBAR»: se calibran en las pruebas reales.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot import selectores as S  # noqa: E402
from bot.pantallas import AvisoTikTok, chat, perfil, vigia  # noqa: E402
from bot.pantallas.comun import Escucha  # noqa: E402

# Qué se mira en cada pantalla (nombres de selectores.py).
PAGINAS = {
    "perfil propio": ("perfil_pagina", "perfil_usuario", "perfil_nombre", "icono_perfil_propio", "icono_mensajes"),
    "perfil ajeno": ("perfil_pagina", "boton_mensaje", "boton_seguir"),
    "chat": ("chat_caja", "chat_usuario", "chat_lista_mensajes", "chat_zona_escribir", "chat_editor",
             "chat_conversacion"),
    "chat (si hay mensajes nuestros)": ("chat_mensaje", "chat_mensaje_texto", "chat_mensaje_propio", "chat_aviso_mensaje"),
    "reel": ("reel_abrir_comentarios", "reel_lista_comentarios", "reel_comentario", "reel_comentario_usuario", "reel_comentario_responder",
             "reel_comentario_mas", "reel_editor_comentario", "reel_boton_publicar"),
    "studio": ("studio_lista_comentarios", "studio_comentario", "studio_buscar", "studio_comentario_responder"),
}


@dataclass
class Fila:
    pagina: str
    nombre: str
    resultado: str  # OK | FALLA | SIN COMPROBAR | AVISO
    estado: str = ""  # confirmado | por_calibrar | (datos)
    detalle: str = ""


def probar(pantallas, usuario: str, reel_id: str | None) -> list[Fila]:
    ctx = pantallas.contexto()
    filas: list[Fila] = []

    def mirar(pagina: str) -> None:
        for nombre in PAGINAS[pagina]:
            s = S.sel(nombre)
            loc = ctx.loc(s, cuenta=ctx.cuenta) if "{" in s.css else ctx.loc(s)
            try:  # basta con que exista en la página (dm-warning, p. ej., está vacío y no se «ve»)
                loc.first.wait_for(state="attached", timeout=ctx.elemento_ms)
                visto = True
            except Exception:  # noqa: BLE001 — TimeoutError
                visto = False
            filas.append(Fila(pagina, nombre, "OK" if visto else "FALLA", s.estado, s.css))

    def dato(pagina: str, nombre: str, ok: bool, detalle: str) -> None:
        filas.append(Fila(pagina, nombre, "OK" if ok else "FALLA", "datos", detalle))

    try:
        ctx.ir(S.url_perfil(ctx.cuenta))
        en_sesion = vigia.usuario_en_sesion(ctx.pagina.content())
        dato("perfil propio", "sesión en el JSON", en_sesion is not None, f"{S.JSON_SESION}.user.uniqueId = @{en_sesion}")
        mirar("perfil propio")

        ctx.ir(S.url_perfil(usuario))
        mirar("perfil ajeno")
        datos = perfil.leer_datos(ctx.pagina.content())
        dato("perfil ajeno", "ID estable en el JSON", bool(datos and datos.user_id), f"{S.JSON_PERFIL}.userInfo.user.id")
        abierto = chat.abrir(ctx, usuario)
        dato("chat", "se puede escribir", abierto, "editor visible (False = mensajes cerrados)")
        mirar("chat")
        if ctx.loc(S.sel("chat_mensaje_propio"), cuenta=ctx.cuenta).count():
            mirar("chat (si hay mensajes nuestros)")

        if reel_id:
            with Escucha(ctx.pagina, S.API_COMENTARIOS_REEL) as escucha:
                ctx.ir(S.url_reel(ctx.cuenta, reel_id))
                abrir = ctx.loc(S.sel("reel_abrir_comentarios"))
                if ctx.aparece(abrir):
                    abrir.first.click()  # la página abre «Podría interesarte»: se pasa a «Comentarios»
                    ctx.revisar()
                mirar("reel")
                ctx.pagina.wait_for_timeout(2_000)
                dato("reel", "API de comentarios", bool(escucha.nuevos_json()), S.API_COMENTARIOS_REEL)
            dato("reel", "JSON del reel", S.JSON_REEL in ctx.pagina.content(), S.JSON_REEL)
        else:
            filas.append(Fila("reel", "(todo el reel)", "SIN COMPROBAR", detalle="sin --reel y sin reels en la base"))

        with Escucha(ctx.pagina, S.API_STUDIO_COMENTARIOS) as escucha:
            ctx.ir(S.URL_STUDIO_COMENTARIOS)
            mirar("studio")
            ctx.pagina.wait_for_timeout(2_000)
            dato("studio", "API de comentarios", bool(escucha.nuevos_json()), S.API_STUDIO_COMENTARIOS)
    except AvisoTikTok as aviso:
        filas.append(Fila("—", aviso.clase, "AVISO", detalle=f"{aviso.detalle} (captura: {aviso.captura})"))
        return filas
    finally:
        pantallas.en_blanco()

    vistos = {f.nombre for f in filas}
    for s in S.SELECTORES.values():
        if s.nombre not in vistos:
            filas.append(Fila(s.pantalla, s.nombre, "SIN COMPROBAR", s.estado, "solo aparece al actuar o sin sesión"))
    return filas


def imprimir(filas: list[Fila]) -> None:
    for f in filas:
        print(f"{f.resultado:<14} {f.pagina:<32} {f.nombre:<28} {f.estado:<13} {f.detalle}")
    resumen = {r: sum(f.resultado == r for f in filas) for r in ("OK", "FALLA", "SIN COMPROBAR", "AVISO")}
    print("\n" + " · ".join(f"{k}: {v}" for k, v in resumen.items()))


def main(argv: list[str] | None = None) -> int:  # pragma: no cover — se usa en la calibración con TikTok
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--usuario", required=True, help="cuenta ajena para mirar perfil y chat (en pruebas, C)")
    parser.add_argument("--reel", help="ID de un reel de la cuenta propia")
    args = parser.parse_args(argv)

    from bot import config, db, instalador, navegador
    from bot.pantallas.tiktok import PantallasTikTok

    conn = db.preparar()
    if not config.leer(conn, "cuenta"):
        print("Falta la cuenta propia: ponla en el panel (Ajustes) antes de probar.")
        return 1
    reel = args.reel
    if reel is None:
        fila = conn.execute("SELECT id FROM reels ORDER BY publicado_en DESC LIMIT 1").fetchone()
        reel = fila["id"] if fila else None

    servicio = instalador.servicio_instalado("bot")
    if servicio:
        print("Parando el bot mientras dura la prueba…")
        instalador.parar("bot")
    try:
        with navegador.Navegador(visible=True) as nav:
            filas = probar(PantallasTikTok(nav, conn), args.usuario.lstrip("@"), reel)
    except navegador.PerfilOcupado as e:
        print(e)
        return 1
    finally:
        if servicio:
            print("Volviendo a arrancar el bot…")
            instalador.arrancar("bot")
    imprimir(filas)
    return 0 if all(f.resultado in ("OK", "SIN COMPROBAR") for f in filas) else 1


if __name__ == "__main__":
    sys.exit(main())

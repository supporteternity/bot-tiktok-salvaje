"""Arranque: python -m bot [orden]"""

import argparse
import sys

from bot import __version__, db


def salida_a_archivo(servicio: str) -> None:
    """Windows arranca los servicios con pythonw (sin consola): sys.stdout y sys.stderr son None.
    Sin esto el panel se cae al arrancar (uvicorn llama a sys.stdout.isatty()) y cualquier error se pierde.
    Se mandan a logs/<servicio>.salida.log y .errores.log, como hace launchd en Mac."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    from bot import config

    logs = config.carpeta_datos(crear=True) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None:
        sys.stdout = open(logs / f"{servicio}.salida.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(logs / f"{servicio}.errores.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115


def main(argv: list[str] | None = None) -> int:
    salida_a_archivo("panel" if "panel" in (sys.argv[1:] if argv is None else argv) else "bot")
    parser = argparse.ArgumentParser(prog="python -m bot", description="Bot TikTok comentario → mensaje directo")
    parser.add_argument("--version", action="version", version=f"bot-tiktok {__version__}")
    parser.add_argument("--solo-preparar", action="store_true",
                        help="Solo crea la carpeta de datos y la base; no abre Chrome ni arranca el bot")
    sub = parser.add_subparsers(dest="orden")
    sub.add_parser("iniciar-sesion", help="Abre Chrome con el perfil del bot para iniciar sesión en TikTok a mano")
    sub.add_parser("panel", help="Arranca el panel web en http://127.0.0.1:8765")
    p_inst = sub.add_parser("instalar", help="Instala el arranque automático (bot y panel) y los accesos directos")
    p_inst.add_argument("--solo-mostrar", action="store_true", help="Solo enseña lo que instalaría, sin tocar nada")
    p_des = sub.add_parser("desinstalar", help="Quita el arranque automático y los accesos directos (los datos se quedan)")
    p_des.add_argument("--apartar-datos", action="store_true",
                       help="Además renombra la carpeta de datos (no la borra): una instalación nueva empieza vacía")
    p_act = sub.add_parser("actualizar", help="Pasa a usar ESTA carpeta (versión nueva): copia la base, migra y rearranca")
    p_act.add_argument("--restaurar", metavar="COPIA", help="Vuelve atrás: restaura esa copia de bot.db y usa esta carpeta")
    sub.add_parser("clave-soporte", help="Crea o cambia la clave de soporte (la pide «Quitar el freno» en el panel)")
    args = parser.parse_args(argv)

    if args.orden == "clave-soporte":
        return crear_clave_soporte()

    if args.orden == "iniciar-sesion":
        from bot import instalador, navegador

        # El Chrome del bot no se puede abrir dos veces: se para el servicio del bot mientras tanto.
        servicio = instalador.servicio_instalado("bot")
        if servicio:
            print("Parando el bot mientras inicias sesión…")
            instalador.parar("bot")
        try:
            # Recién parado el servicio, su Chrome puede tardar unos segundos en soltar el perfil.
            navegador.iniciar_sesion(espera_perfil_s=20 if servicio else 0)
        except (navegador.PerfilOcupado, navegador.FaltaNavegador) as e:
            print(e)
            return 1
        finally:
            if servicio:
                print("Volviendo a arrancar el bot…")
                instalador.arrancar("bot")
        return 0

    if args.orden == "actualizar":
        from pathlib import Path

        from bot import actualizar

        return actualizar.restaurar(Path(args.restaurar)) if args.restaurar else actualizar.actualizar()

    if args.orden == "instalar":
        from bot import instalador

        return instalador.instalar(solo_mostrar=args.solo_mostrar)

    if args.orden == "desinstalar":
        from bot import instalador

        return instalador.desinstalar(apartar=args.apartar_datos)

    if args.orden == "panel":
        from panel.app import arrancar

        arrancar()
        return 0

    if args.solo_preparar:
        conn = db.preparar()  # crea la carpeta de datos y bot.db si faltan
        conn.close()
        print(f"Base de datos lista: {db.ruta_db()}")
        return 0

    return correr_bot()


def crear_clave_soporte() -> int:
    """Solo soporte (27 nov): el correo ya no crea esta clave; la pide «Quitar el freno». Se guarda solo su resumen."""
    import getpass

    from bot import config

    while True:
        clave = getpass.getpass("Nueva clave de soporte (al menos 6 caracteres): ")
        if len(clave) < 6:
            print("La clave tiene que tener al menos 6 caracteres.")
            continue
        if getpass.getpass("Repítela: ") != clave:
            print("Las dos claves no coinciden. Escríbela otra vez.")
            continue
        config.guardar_secretos({"SOPORTE_CLAVE": config.resumen_clave(clave)})
        print("Clave de soporte guardada. Guárdala tú: el cliente no la necesita.")
        return 0


def correr_bot() -> int:  # pragma: no cover — se prueba en la fase de integración (E3, 2 h con la cuenta P)
    """Sin orden: el bot de verdad. Chrome con el perfil del bot + pantallas reales + detector + bucle.
    Si Chrome se cierra, el proceso termina y el servicio del sistema lo vuelve a arrancar."""
    import logging
    import signal

    from bot import config, navegador, registro
    from bot.bucle import Bucle, esperar_encendido
    from bot.detector import DetectorTikTok
    from bot.pantallas.tiktok import NavegadorCerrado, PantallasTikTok

    conn = db.preparar()
    registro.configurar(config.carpeta_datos(crear=True) / "logs")
    log = logging.getLogger("bot")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # parar el servicio = cierre limpio de Chrome
    esperar_encendido(conn)  # «Apagar el bot» en el panel: no se abre nada hasta que lo enciendan
    try:
        nav = navegador.Navegador(visible=True, minimizada=True).abrir()
    except (navegador.PerfilOcupado, navegador.FaltaNavegador) as e:
        log.error(str(e))
        conn.close()
        return 1
    try:
        pantallas = PantallasTikTok(nav, conn)
        Bucle(conn, pantallas, DetectorTikTok(conn, pantallas)).correr()
    except NavegadorCerrado:
        log.warning("Se cerró la ventana de Chrome del bot: el proceso termina y el servicio lo vuelve a abrir")
        return 2
    except KeyboardInterrupt:
        log.info("Bot parado a mano")
    finally:
        nav.cerrar()
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

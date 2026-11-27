#!/bin/bash
# Bot de TikTok para Mac: ACTUALIZAR. Doble clic aquí.
# Lo usa soporte: abrir desde la carpeta de la versión NUEVA.
# Solo es el botón: el trabajo lo hace scripts/actualizar.command, común a todas las versiones.
BOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ ! -f "$BOT/scripts/actualizar.command" ] || [ ! -f "$BOT/pyproject.toml" ]; then
  echo "No encuentro el resto del bot junto a esta carpeta."
  echo "Descomprime el .zip entero (doble clic sobre él) y abre la carpeta MAC que sale dentro."
  read -n 1 -s -r -p "Pulsa cualquier tecla para cerrar esta ventana..."
  exit 1
fi
exec /bin/bash "$BOT/scripts/actualizar.command" "$@"

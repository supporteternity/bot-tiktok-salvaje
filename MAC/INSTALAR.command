#!/bin/bash
# Bot de TikTok para Mac: INSTALAR. Doble clic aquí.
# Instala el bot (una sola vez). Si se mueve la carpeta, se vuelve a abrir.
# Solo es el botón: el trabajo lo hace scripts/instalar_mac.command, común a todas las versiones.
BOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ ! -f "$BOT/scripts/instalar_mac.command" ] || [ ! -f "$BOT/pyproject.toml" ]; then
  echo "No encuentro el resto del bot junto a esta carpeta."
  echo "Descomprime el .zip entero (doble clic sobre él) y abre la carpeta MAC que sale dentro."
  read -n 1 -s -r -p "Pulsa cualquier tecla para cerrar esta ventana..."
  exit 1
fi
exec /bin/bash "$BOT/scripts/instalar_mac.command" "$@"

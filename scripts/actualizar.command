#!/bin/bash
# Actualizar (Mac): ejecutar desde la carpeta de la versión NUEVA. Para volver atrás, desde la carpeta
# ANTERIOR:  scripts/actualizar.command --restaurar "<ruta de la copia>"
set -e
source "$(dirname "$0")/_lugar_seguro_mac.sh"
export PATH="$HOME/.local/bin:$PATH"
# Si algo falla, la ventana NO se cierra sola: se avisa y se espera (como en Windows), para poder enviar la captura.
codigo=0
uv sync --frozen --no-dev && uv run --no-dev python -m bot actualizar "$@" || codigo=$?
echo
if [ "$codigo" -ne 0 ]; then
  echo "Algo ha fallado. Haz una captura de esta ventana y envíala al soporte."
fi
read -n 1 -s -r -p "Pulsa cualquier tecla para cerrar esta ventana..."
exit "$codigo"

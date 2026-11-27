#!/bin/bash
# Instalador para Mac (se abre desde MAC/INSTALAR.command).
# Instala uv si falta, las dependencias exactas, el navegador del bot y el arranque automático.
source "$(dirname "$0")/_lugar_seguro_mac.sh"
export PATH="$HOME/.local/bin:$PATH"

# Si algo falla, la ventana no se cierra sola: se explica y se espera, para poder enviar la captura.
fallo() {
  echo
  [ -n "$1" ] && echo "$1"
  echo "Algo ha fallado. Haz una captura de esta ventana y envíala al soporte."
  read -n 1 -s -r -p "Pulsa cualquier tecla para cerrar esta ventana..."
  exit 1
}

if ! command -v uv >/dev/null 2>&1; then
  echo "Instalando uv (el programa que prepara Python para el bot)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh \
    || fallo "No se pudo descargar uv. Comprueba la conexión a internet y vuelve a abrir el instalador."
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null 2>&1 \
    || fallo "uv no quedó instalado. Comprueba la conexión a internet y vuelve a abrir el instalador."
fi
echo "Instalando lo que necesita el bot (la primera vez tarda unos minutos)…"
uv sync --frozen --no-dev \
  || fallo "No se pudo instalar lo que necesita el bot. Comprueba la conexión a internet y vuelve a abrir el instalador."
uv run --frozen --no-dev python -m bot instalar || fallo
echo
read -n 1 -s -r -p "Pulsa cualquier tecla para cerrar esta ventana..."

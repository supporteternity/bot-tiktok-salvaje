#!/bin/bash
# Desinstalar (Mac): doble clic. Para el bot y el panel, quita el arranque automático y los accesos directos.
# Los datos NUNCA se borran: se quedan donde están o, si se elige, se apartan con otro nombre.
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
echo "Esto desinstala el bot de TikTok de esta computadora."
read -r -p "¿Apartar también tus datos para que una instalación nueva empiece vacía? (s/N) " RESP
EXTRA=""
case "$RESP" in s|S|si|SI|sí|Sí) EXTRA="--apartar-datos" ;; esac
if [ -x ".venv/bin/python" ]; then
  .venv/bin/python -m bot desinstalar $EXTRA
else
  uv run --no-dev python -m bot desinstalar $EXTRA
fi
echo
read -n 1 -s -r -p "Pulsa cualquier tecla para cerrar esta ventana..."

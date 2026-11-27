#!/bin/bash
# Abre Chrome con el perfil del bot para iniciar sesión en TikTok a mano (para el bot mientras tanto).
cd "$(dirname "$0")/.." || exit 1
".venv/bin/python" -m bot iniciar-sesion
echo
read -n 1 -s -r -p "Pulsa cualquier tecla para cerrar esta ventana..."

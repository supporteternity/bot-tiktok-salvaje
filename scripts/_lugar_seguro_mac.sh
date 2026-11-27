# Se incluye (source) desde instalar_mac.command y actualizar.command.
# macOS no deja que los programas que arrancan solos (el bot y el panel) lean Documentos, Escritorio,
# Descargas ni iCloud Drive. Si la carpeta está ahí, se copia a ~/Applications/Bot TikTok/ y se sigue
# desde la copia. La carpeta original no se toca (sirve para volver atrás).
APP="$(cd "$(dirname "$0")/.." && pwd)"
case "$APP/" in
  "$HOME/Documents/"*|"$HOME/Desktop/"*|"$HOME/Downloads/"*|"$HOME/Library/Mobile Documents/"*|"$HOME/Library/CloudStorage/"*)
    DESTINO="$HOME/Applications/Bot TikTok/BOT-TIKTOK-APP-$(date +%Y%m%d-%H%M%S)"
    echo "macOS no deja que el bot funcione desde Documentos, Escritorio o Descargas."
    echo "Lo copio a una carpeta segura: $DESTINO"
    mkdir -p "$DESTINO"
    rsync -a --exclude ".venv" --exclude "__pycache__" "$APP/" "$DESTINO/"
    # Con /bin/bash: funciona aunque al descomprimir se haya perdido el permiso de ejecución del script.
    exec /bin/bash "$DESTINO/scripts/$(basename "$0")" "$@"
    ;;
esac
cd "$APP"
# Lo descargado de internet lleva una marca de macOS (cuarentena) que bloquea cada script al abrirlo.
# La persona ya aceptó abrir el instalador: se quita la marca de la carpeta del bot para que los demás
# (iniciar sesión, desinstalar, actualizar) se abran sin el mismo aviso.
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true

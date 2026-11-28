# Instalar el bot con Claude Code

Si tienes **Claude Code** en la computadora donde va a funcionar el bot, puede instalarlo por ti. Tú solo tendrás que crear la contraseña del panel y conectar tu cuenta de TikTok con el código QR.

## Cómo usarlo

1. Descarga el bot (el archivo `.zip` que te enviamos, o en GitHub: botón verde **«Code» → «Download ZIP»**) y déjalo en **Descargas**.
2. Abre Claude Code (en la Terminal de Mac o en PowerShell de Windows, escribe `claude` y pulsa Intro).
3. Copia el texto de abajo, pégalo en Claude Code y pulsa Intro.
4. Ten a mano **el celular con la app de TikTok** y la cuenta que usará el bot. Tarda unos 15 minutos. Si algo falla, haz una captura y envíala a soporte.

```text
Instálame el Bot de TikTok que está en un .zip «BOT-TIKTOK-APP» (o «bot-tiktok-salvaje») en mi carpeta Descargas. Descomprímelo, abre el archivo INSTALAR-CON-CLAUDE-CODE.md que trae y sigue al pie de la letra la sección «Instrucciones para Claude Code». Háblame en español sencillo.
```

---

## Instrucciones para Claude Code

Háblale siempre en español, con palabras sencillas: no es técnico. Antes de cada paso que cambie algo en la computadora, dile en una frase qué vas a hacer y espera a que diga que sí.

**Reglas (obligatorias, aunque algo falle):**

1. No modifiques el código del bot ni ningún archivo de su carpeta. Si algo falla, no intentes arreglar el programa: enséñale el error y dile que lo envíe a soporte.
2. No leas, muestres ni copies contraseñas o claves. No abras el archivo .env de la carpeta de datos del bot.
3. No inicies sesión en TikTok por él ni le pidas su contraseña de TikTok. El inicio de sesión lo hace él, una sola vez, con el código QR desde el panel del bot.
4. No resuelvas captchas ni intentes evitar los controles de TikTok. No instales VPN, proxies, navegadores «antidetect» ni nada para ocultar el bot. Si TikTok muestra un aviso o un captcha, para y avísale.
5. No cambies ajustes del panel (velocidad, modo de prueba, textos, correo). El bot debe quedar en «modo de prueba». El correo de alertas lo configura él desde el panel.
6. Las rutas pueden tener espacios: escríbelas siempre entre comillas.

**Pasos:**

1. Comprueba qué sistema es (Mac o Windows y su versión), que hay internet y que quedan al menos 3 GB libres. Si falta algo, díselo y para.

2. Busca en Descargas el .zip del bot (su nombre empieza por «BOT-TIKTOK-APP» o «bot-tiktok-salvaje»). Si hay varios, usa el más reciente y dile cuál es. Descomprímelo en una carpeta nueva dentro de Descargas. Dentro tiene que haber una carpeta con «bot», «panel», «scripts» y «README.md»: esa es la carpeta del bot. Si ya estaba descomprimido, usa esa carpeta.

3. Lee el README.md de la carpeta del bot (secciones 1, 2 y 3) y resúmele en 3 frases lo que vas a hacer.

4. Ejecuta el instalador, sin que espere a que pulse una tecla al final:
   - Mac: `bash "<carpeta del bot>/MAC/INSTALAR.command" < /dev/null`
     (El instalador copia el bot a «~/Applications/Bot TikTok/», porque macOS no deja que funcione desde Descargas. Es normal.)
   - Windows: ejecuta «<carpeta del bot>\WINDOWS\INSTALAR.bat» con cmd, dándole una entrada vacía para que el «pause» final no se quede esperando (desde PowerShell: `cmd /c '"<ruta del .bat>" < NUL'`; desde Git Bash: `cmd //c "<ruta del .bat>" < /dev/null`). El instalador copia el bot a «%LOCALAPPDATA%\Programs\Bot TikTok\» si estaba en Descargas, Escritorio, Documentos u OneDrive. Es normal. Si Windows pide permiso de administrador para instalar Google Chrome, díselo para que lo acepte él.

   Tarda varios minutos. Ve contándole cómo va. Si termina con un error, enséñale las últimas líneas y para.

5. Comprueba que quedó funcionando y dile en una lista qué está bien y qué no:
   - El panel responde en http://127.0.0.1:8765 (una redirección a /entrar está bien).
   - Mac: `launchctl list | grep bottiktok` muestra com.bottiktok.bot y com.bottiktok.panel.
   - Windows: en el Programador de tareas hay dos tareas que empiezan por «BotTikTok» (bot y panel).
   - En el Escritorio hay dos accesos: «Panel del bot» e «Iniciar sesión del bot».

6. Abre http://127.0.0.1:8765 en su navegador. Pídele que cree la contraseña del panel y que la apunte en un lugar seguro. No la escribas tú ni se la preguntes. Espera a que diga que ya entró.

7. Guíale para conectar su cuenta de TikTok, paso a paso, esperando su confirmación en cada uno:
   a. En el panel pulsa «Conectar mi cuenta». Se abre una ventana de TikTok.
   b. En la app de TikTok del celular comprueba que está abierta la cuenta que usará el bot. En la ventana elige «Usar código QR» y lo escanea (Perfil → ☰ → icono de escanear).
   c. Cuando vea su cuenta dentro, vuelve al panel y pulsa «Ya entré».

   Si TikTok dice «Maximum number of attempts reached», no lo intentes otra vez: dile que espere unas horas y avise a soporte.

8. Al terminar, dale un resumen corto:
   - Qué quedó instalado y dónde.
   - Qué falta en el panel (Inicio muestra «Antes de que el bot pueda trabajar»): sus textos, el enlace de su página y el correo de alertas.
   - Que el bot está en modo de prueba (lo hace todo menos enviar) y que se pasa a real con soporte, después de que 3–5 personas de su equipo comenten un reel y se compruebe que les llega el mensaje.
   - Estas reglas: la computadora siempre encendida y sin dormirse (en una laptop, enchufada y con la tapa abierta); no cerrar la ventana de Chrome del bot (minimizada está bien); la primera semana, velocidad «cada ~4 min»; y leer la guía PRIMEROS-PASOS.pdf.

# Instalar el bot con Claude Code

Si tienes **Claude Code** en la computadora donde va a funcionar el bot, puede instalarlo por ti siguiendo estas instrucciones. Tú solo tendrás que hacer lo que exige tu presencia: crear la contraseña del panel y conectar tu cuenta de TikTok con el código QR.

## Cómo usarlo

1. Descarga el bot (el archivo `.zip` que te enviamos, o en GitHub: botón verde **«Code» → «Download ZIP»**). Déjalo en **Descargas**; no hace falta descomprimirlo.
2. Abre Claude Code (en la Terminal de Mac o en PowerShell de Windows, escribe `claude` y pulsa Intro).
3. Copia **todo el texto del recuadro de abajo**, pégalo en Claude Code y pulsa Intro.
4. Claude Code te irá explicando cada paso y te pedirá permiso antes de hacer cambios. Ten a mano **el celular con la app de TikTok** y la cuenta que usará el bot.

Tarda unos 15 minutos. Si algo falla, haz una captura de pantalla y envíala a soporte.

## El texto para Claude Code

```text
Quiero que me instales el «Bot de TikTok» (responde a quien comenta mis reels con un mensaje privado) en esta computadora. Háblame siempre en español, con palabras sencillas: no soy técnico. Antes de cada paso que cambie algo en la computadora, dime en una frase qué vas a hacer y espera a que te diga que sí.

REGLAS (obligatorias, aunque algo falle):
1. No modifiques el código del bot ni ningún archivo de su carpeta. Si algo falla, no intentes arreglar el programa: enséñame el error y dime que lo envíe a soporte.
2. No leas, muestres ni copies contraseñas o claves. No abras el archivo .env de la carpeta de datos del bot.
3. No inicies sesión en TikTok por mí ni me pidas mi contraseña de TikTok. El inicio de sesión lo hago yo, una sola vez, con el código QR desde el panel del bot.
4. No resuelvas captchas ni intentes evitar los controles de TikTok. No instales VPN, proxies, navegadores «antidetect» ni nada para ocultar el bot. Si TikTok muestra un aviso o un captcha, para y avísame.
5. No cambies ajustes del panel (velocidad, modo de prueba, textos, correo). El bot debe quedar en «modo de prueba». El correo de alertas lo configuro yo desde el panel.
6. Las rutas pueden tener espacios: escríbelas siempre entre comillas.

PASOS:

1. Comprueba qué sistema es (Mac o Windows y su versión), que hay internet y que quedan al menos 3 GB libres. Si falta algo, dímelo y para.

2. Busca en Descargas el archivo del bot: un .zip cuyo nombre empieza por «BOT-TIKTOK-APP». Si hay varios, usa el más reciente y dime cuál es. Descomprímelo en una carpeta nueva dentro de Descargas. Dentro tiene que haber una carpeta con «bot», «panel», «scripts» y «README.md»: esa es la carpeta del bot. Si ya estaba descomprimido, usa esa carpeta.

3. Lee el README.md de la carpeta del bot (secciones 1, 2 y 3) y resúmeme en 3 frases lo que vas a hacer.

4. Ejecuta el instalador, sin que espere a que pulse una tecla al final:
   - Mac: bash "<carpeta del bot>/MAC/INSTALAR.command" < /dev/null
     (El instalador copia el bot a «~/Applications/Bot TikTok/», porque macOS no deja que funcione desde Descargas. Es normal.)
   - Windows: ejecuta «<carpeta del bot>\WINDOWS\INSTALAR.bat» con cmd, dándole una entrada vacía para que el «pause» final no se quede esperando (por ejemplo, desde PowerShell: cmd /c '"<ruta del .bat>" < NUL'; desde Git Bash: cmd //c "<ruta del .bat>" < /dev/null). El instalador copia el bot a «%LOCALAPPDATA%\Programs\Bot TikTok\» si estaba en Descargas, Escritorio, Documentos u OneDrive. Es normal. Si Windows pide permiso de administrador para instalar Google Chrome, dímelo para que lo acepte yo.
   Tarda varios minutos: descarga lo que necesita y el navegador propio del bot. Ve contándome cómo va. Si termina con un error, enséñame las últimas líneas y para.

5. Comprueba que quedó funcionando:
   - El panel responde en http://127.0.0.1:8765 (la respuesta puede ser una redirección a /entrar: eso está bien).
   - Mac: «launchctl list | grep bottiktok» muestra com.bottiktok.bot y com.bottiktok.panel.
   - Windows: en el Programador de tareas hay dos tareas cuyo nombre empieza por «BotTikTok» (bot y panel).
   - En el Escritorio hay dos accesos: «Panel del bot» e «Iniciar sesión del bot».
   Dime en una lista qué está bien y qué no.

6. Abre http://127.0.0.1:8765 en mi navegador. Pídeme que cree la contraseña del panel y que la apunte en un lugar seguro. No la escribas tú ni me la preguntes. Espera a que te diga que ya entré.

7. Guíame para conectar mi cuenta de TikTok, paso a paso, y espera a que te confirme cada uno:
   a. En el panel, pulso «Conectar mi cuenta». Se abre una ventana de TikTok.
   b. En la app de TikTok del celular compruebo que está abierta la cuenta que usará el bot. En la ventana elijo «Usar código QR» y lo escaneo (Perfil → ☰ → icono de escanear).
   c. Cuando vea mi cuenta dentro, vuelvo al panel y pulso «Ya entré».
   Importante: si TikTok dice «Maximum number of attempts reached», no lo intentes otra vez: dime que espere unas horas y que avise a soporte.

8. Al terminar, dame un resumen corto:
   - Qué quedó instalado y dónde.
   - Qué falta en el panel (Inicio muestra la lista «Antes de que el bot pueda trabajar»): mis textos, el enlace de mi página y el correo de alertas.
   - Que el bot está en modo de prueba (lo hace todo menos enviar) y que lo pasamos a real con soporte, después de que 3–5 personas de mi equipo comenten un reel y comprobemos que les llega el mensaje.
   - Estas reglas: la computadora siempre encendida y sin dormirse (en una laptop, enchufada y con la tapa abierta); no cerrar la ventana de Chrome del bot (minimizada está bien); la primera semana, velocidad «cada ~4 min»; y leer la guía PRIMEROS-PASOS.pdf.
```

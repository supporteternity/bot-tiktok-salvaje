"""«TikTok falso» para probar las pantallas reales con Chrome SIN conectarse a TikTok.

Se instala con `context.route("**/*")`: las peticiones a www.tiktok.com las contesta este módulo con
páginas mínimas que imitan la estructura vista en las páginas reales (data-e2e de selectores.py y el JSON
de rehidratación); cualquier otra petición se corta. Lo que la página «envía» queda apuntado en
`self.enviados` / `self.publicados` para comprobar que en modo de prueba no se envía nada.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from bot import selectores as S

E = S.e2e


@dataclass
class Persona:
    usuario: str
    user_id: str
    nombre: str = ""
    # abiertos | cerrados | no_entrega (sale el aviso en el mensaje) | sin_eco | bloqueado (TikTok contesta 7195) | se_pierde (sale en pantalla
    # pero TikTok no lo guarda: al volver a abrir el chat ya no está, como pasó de verdad el 25–26 nov)
    mensajes: str = "abiertos"
    previos: int = 0  # mensajes nuestros ya en el chat
    previos_texto: str = "Hola de antes, mira https://academia.com"  # texto de esos mensajes
    guardados: list[str] = field(default_factory=list)  # lo que TikTok guardó de verdad (sale al reabrir)
    nueva: bool = False  # nunca habíais hablado: la cabecera del chat sale vacía («@»), como en la real
    perfil_en_chat: str | None = None  # @ del perfil que trae la página del chat (por defecto, el suyo)
    # Chat que carga despacio: los mensajes ya guardados salen pasados estos ms (0 = enseguida)
    carga_lenta_ms: int = 0
    conv_abierta: str | None = None  # conversación que TikTok deja seleccionada (por defecto, la suya)


@dataclass
class Comentario:
    cid: str
    usuario: str
    texto: str
    create_time: int = 1_764_000_000


@dataclass
class FalsoTikTok:
    cuenta: str = "academia"
    sesion: str | None = "academia"  # cuenta con la sesión iniciada (None = sin sesión)
    personas: dict[str, Persona] = field(default_factory=dict)
    reels: dict[str, list[Comentario]] = field(default_factory=dict)
    publicar_responde: bool = True
    publicar_rechaza: bool = False  # 200 con status_code ≠ 0 (como TikTok cuando rechaza)
    publicar_corta: bool = False  # TikTok la publica pero la conexión se corta: no llega respuesta
    studio_no_vacia: bool = False  # Studio no vacía el cuadro tras publicar (aunque TikTok diga que sí)
    publicado: int = 1_763_900_000  # createTime de los reels
    aviso: str | None = None  # texto de un aviso (toast) que aparece en todas las páginas
    enviados: list[dict] = field(default_factory=list)
    publicados: list[dict] = field(default_factory=list)
    visitas: list[str] = field(default_factory=list)

    def instalar(self, contexto) -> None:
        contexto.route("**/*", self._ruta)

    # ------------------------------------------------------------ servidor

    def _ruta(self, ruta) -> None:
        url = urlparse(ruta.request.url)
        if url.hostname != "www.tiktok.com":
            ruta.abort()
            return
        self.visitas.append(ruta.request.url)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        camino = url.path.rstrip("/")
        if camino == "/api/im/send":
            datos = json.loads(ruta.request.post_data or "{}")
            self.enviados.append(datos)
            p = next((x for x in self.personas.values() if x.user_id == datos.get("u")), None)
            if p is not None and p.mensajes == "bloqueado":
                # Como la real (26 nov): TikTok contesta 7195 «sending_ban» y no lo guarda, aunque sale en pantalla.
                ruta.fulfill(json={"status_code": 7195, "status_msg": "Puede ser que este mensaje infrinja las Normas "
                                   "de la comunidad, y no se envió para proteger nuestra comunidad",
                                   "extra": {"scene": "sending_ban"}})
                return
            if p is not None and p.mensajes != "se_pierde":
                p.guardados.append(datos.get("texto", ""))
            ruta.fulfill(json={"status_code": 0})
        elif camino == "/api/comment/publish":
            datos = json.loads(ruta.request.post_data or "{}")
            self.publicados.append(datos)
            if self.publicar_corta:
                ruta.abort()
            elif self.publicar_rechaza:
                ruta.fulfill(json={"status_code": 8, "status_msg": "rechazado"})
            elif self.publicar_responde:
                ruta.fulfill(json={"status_code": 0, "comment": {"cid": f"nuevo{len(self.publicados)}"}})
            else:
                ruta.fulfill(status=500, body="")
        elif camino == "/tiktokstudio/api/web/commentsV2":
            ruta.fulfill(json=self._api_studio(int(q.get("cursor", 0)), q.get("q", "")))
        elif camino == "/tiktokstudio/comment":
            ruta.fulfill(content_type="text/html", body=self._studio())
        elif camino == "/api/comment/list":
            ruta.fulfill(json=self._api_comentarios(q.get("aweme_id", ""), int(q.get("cursor", 0))))
        elif camino == "/messages":
            ruta.fulfill(content_type="text/html", body=self._chat(q.get("u")))
        elif camino.startswith("/@") and "/video/" in camino:
            ruta.fulfill(content_type="text/html", body=self._reel(camino.rsplit("/", 1)[1]))
        elif camino.startswith("/@"):
            ruta.fulfill(content_type="text/html", body=self._perfil(camino[2:]))
        else:
            ruta.fulfill(content_type="text/html", body=self._pagina("<p>inicio</p>"))

    # ------------------------------------------------------------ páginas

    def _pagina(self, cuerpo: str, detalle: dict | None = None, video: dict | None = None) -> str:
        app = {"user": {"uniqueId": self.sesion}} if self.sesion else {}
        ambito = {"webapp.app-context": app}
        if detalle is not None:
            ambito["webapp.user-detail"] = detalle
        if video is not None:
            ambito[S.JSON_REEL] = video
        datos = json.dumps({S.JSON_AMBITO: ambito})
        cabecera = (f'<a data-e2e="profile-icon" href="/@{self.cuenta}">yo</a><a data-e2e="top-dm-icon" href="/messages">m</a>'
                    if self.sesion else '<button data-e2e="top-login-button">Iniciar sesión</button>')
        aviso = f'<div class="toast" role="alert">{self.aviso}</div>' if self.aviso else ""
        return (f'<!doctype html><html><head><meta charset="utf-8"><script id="{S.ID_JSON_REHIDRATACION}" '
                f'type="application/json">{datos}</script></head><body>{cabecera}{aviso}{cuerpo}</body></html>')

    def _perfil(self, usuario: str) -> str:
        p = self.personas.get(usuario.casefold())
        if usuario.casefold() == self.cuenta:
            p = Persona(self.cuenta, "1", "Academia")
        if p is None:
            return self._pagina("<p>No se pudo encontrar esta cuenta</p>", {"statusCode": 10221, "userInfo": {}})
        detalle = {"statusCode": 0, "userInfo": {"user": {"id": p.user_id, "uniqueId": p.usuario, "nickname": p.nombre}}}
        cuerpo = (f'<div data-e2e="user-page"><h1 data-e2e="user-title">{p.usuario}</h1>'
                  f'<h2 data-e2e="user-subtitle">{p.nombre}</h2>'
                  f'<button data-e2e="follow-button">Seguir</button>'
                  f'<a href="/messages?lang=es-419&u={p.user_id}"><button {E("message-button")[1:-1]}>Mensaje</button></a>'
                  f'</div>')
        return self._pagina(cuerpo, detalle)

    def _chat(self, user_id: str | None) -> str:
        if user_id is None:
            lista = "".join(f'<div data-e2e="dm-new-conversation-item">{p.usuario}</div>' for p in self.personas.values())
            return self._pagina(f'<div data-e2e="dm-new-conversation-list">{lista}</div>')
        p = next(x for x in self.personas.values() if x.user_id == user_id)
        propio = (f'<div data-e2e="dm-new-chat-item"><a href="/@{self.cuenta}"><span data-e2e="chat-avatar"></span></a>'
                  '<div data-e2e="dm-new-message-text"><p>{texto}</p></div><div data-e2e="dm-warning">{aviso}</div></div>')
        previos = "".join(propio.format(texto=p.previos_texto, aviso="") for _ in range(p.previos))
        previos += "".join(propio.format(texto=t.replace("<", "&lt;"), aviso="") for t in p.guardados)
        lento = ""
        if p.carga_lenta_ms and previos:
            lento = (f"<script>setTimeout(() => {{ document.querySelector('[role=log]').innerHTML = {json.dumps(previos)}; }}, "
                     f"{p.carga_lenta_ms});</script>")
            previos = ""
        if p.mensajes == "cerrados":
            abajo = '<div data-e2e="dm-new-chat-bottom"><p>No puedes enviar mensajes a esta cuenta</p></div>'
        else:
            abajo = ('<div data-e2e="dm-new-chat-bottom"><div data-e2e="message-input-area"><div data-e2e="dm-new-input-editor">'
                     '<div contenteditable="true" role="textbox" aria-label="Enviar un mensaje…"></div></div></div></div>')
        script = """
<script>
const modo = %(modo)s, plantilla = %(plantilla)s;
const editor = document.querySelector('[role=textbox]');
if (editor) editor.addEventListener('keydown', async (e) => {
  if (e.key !== 'Enter' || e.shiftKey) return;
  e.preventDefault();
  const texto = editor.innerText;
  editor.innerHTML = '';
  await fetch('/api/im/send', {method: 'POST', body: JSON.stringify({u: %(uid)s, texto})});
  if (modo === 'sin_eco') return;
  const aviso = modo === 'no_entrega' ? 'Este mensaje no se pudo enviar' : '';
  const div = document.createElement('div');
  div.innerHTML = plantilla.replace('{texto}', texto.replace(/</g, '&lt;')).replace('{aviso}', aviso);
  document.querySelector('[role=log]').appendChild(div.firstChild);
});
</script>""" % {"modo": json.dumps(p.mensajes), "plantilla": json.dumps(propio), "uid": json.dumps(p.user_id)}
        # Como la real (27 nov): cada conversación lleva su id «0:1:<id de la persona>:<id propio>» y la abierta,
        # aria-selected="true". `conv_abierta` = de quién es la conversación que TikTok deja seleccionada.
        abierta = self.personas.get(p.conv_abierta or "", p)
        conversaciones = "".join(
            f'<div data-e2e="dm-new-conversation-item" data-conv-id="0:1:{x.user_id}:1" '
            f'aria-selected="{"true" if x is abierta else "false"}">{x.usuario}</div>' for x in self.personas.values())
        cuerpo = (f'<div data-e2e="dm-new-conversation-list">{conversaciones}</div>'
                  f'<div data-e2e="dm-new-chatbox"><p data-e2e="chat-uniqueid">@{"" if p.nueva else p.usuario}</p>'
                  f'<div data-e2e="dm-new-message-list" role="log" aria-live="polite">{previos}</div>{abajo}</div>{script}{lento}')
        # Como la real: al llegar desde su perfil, la página aún trae los datos de ese perfil.
        quien = p.perfil_en_chat or p.usuario
        uid = p.user_id if quien == p.usuario else f"id-{quien}"
        detalle = {"statusCode": 0, "userInfo": {"user": {"id": uid, "uniqueId": quien}}}
        return self._pagina(cuerpo, detalle)

    def _api_comentarios(self, reel_id: str, cursor: int, tam: int = 20) -> dict:
        lista = self.reels.get(reel_id, [])
        trozo = lista[cursor:cursor + tam]
        return {
            "status_code": 0, "cursor": cursor + len(trozo), "has_more": int(cursor + tam < len(lista)),
            "comments": [{"cid": c.cid, "text": c.texto, "create_time": c.create_time, "aweme_id": reel_id,
                          "user": {"uid": f"id-{c.usuario}", "unique_id": c.usuario, "nickname": c.usuario.title()}}
                         for c in trozo],
        }

    def _api_studio(self, cursor: int, buscar: str = "", tam: int = 10) -> dict:
        """Studio: todos los comentarios de todos los reels, del más nuevo al más viejo, en otro formato.
        Con `buscar`, nada si se busca un @usuario (como el buscador real, 25 nov)."""
        todos = sorted(((r, c) for r, lista in self.reels.items() for c in lista
                        if not buscar), key=lambda x: -x[1].create_time)
        trozo = todos[cursor:cursor + tam]
        # Formato real de /tiktokstudio/api/web/commentsV2 (capturado el 25 nov 2026).
        return {"hasMore": cursor + tam < len(todos), "cursor": str(cursor + len(trozo)),
                "comments": [{"commentId": c.cid, "text": c.texto, "createTime": c.create_time, "item": {"itemId": r, "createTime": str(self.publicado)},
                              "user": {"uid": f"id-{c.usuario}", "uniqueId": c.usuario, "nickname": c.usuario}}
                             for r, c in trozo]}

    def _studio(self) -> str:
        """Como la real (25 nov): buscador, lista virtual, «Responder» abre un textarea y el botón de enviar
        aparece al escribir. «Eliminar» está al lado (el bot nunca lo pulsa: si lo hiciera, se apunta)."""
        script = """
<script>
const lista = document.querySelector('[data-tt=components_CommentList_Absolute]');
const buscador = document.querySelector('[data-tt=components_SearchBar_TUXTextInput]');
let cursor = 0, hayMas = true, cargando = false, q = '';
function item(c) {
  const d = document.createElement('div');
  d.setAttribute('data-tt', 'components_CommentList_Container');
  d.innerHTML = `<a href="/@${c.user.uniqueId}">${c.user.uniqueId}</a><span>${c.text}</span>
    <span class="resp">Responder</span> <span class="elim">Eliminar</span><div class="zona"></div>`;
  d.querySelector('.elim').onclick = () => fetch('/eliminado', {method: 'POST'});
  d.querySelector('.resp').onclick = () => {
    const z = d.querySelector('.zona');
    z.innerHTML = '<div data-tt="components_CommentInputEditor_FlexRow"><textarea placeholder="Responder al comentario"></textarea></div>';
    const ta = z.querySelector('textarea');
    ta.oninput = () => {
      if (ta.value && !z.querySelector('button')) {
        const b = document.createElement('button'); b.setAttribute('data-tt', 'components_CommentInputEditor_TUXButton');
        b.onclick = async () => {
          const r = await fetch('/api/comment/publish/', {method: 'POST', body: JSON.stringify({reel: c.item.itemId, texto: ta.value, responde: true})});
          if (r.ok && !NO_VACIA) z.innerHTML = '';
        };
        z.firstChild.appendChild(b);
      }
    };
  };
  return d;
}
async function cargar(reiniciar) {
  if (reiniciar) { cursor = 0; hayMas = true; lista.innerHTML = ''; }
  if (cargando || !hayMas) return; cargando = true;
  const r = await (await fetch(`/tiktokstudio/api/web/commentsV2?cursor=${cursor}&q=${encodeURIComponent(q)}`)).json();
  r.comments.forEach(c => lista.appendChild(item(c)));
  cursor = r.cursor; hayMas = r.hasMore; cargando = false;
}
buscador.addEventListener('keydown', e => { if (e.key === 'Enter') { q = buscador.value; cargar(true); } });
window.addEventListener('wheel', () => cargar());
cargar();
</script>""".replace("NO_VACIA", json.dumps(self.studio_no_vacia))
        return self._pagina('<input data-tt="components_SearchBar_TUXTextInput" placeholder="Busca un comentario o nombre de usuario">'
                            '<div data-tt="components_CommentList_Absolute" style="min-height:50px"></div>' + script)

    def _reel(self, reel_id: str) -> str:
        script = """
<script>
const reel = %(reel)s, cuenta = %(cuenta)s;
const lista = document.querySelector('[data-e2e=comment-list]');
const editor = document.querySelector('[data-e2e=comment-input] [contenteditable]');
let cursor = 0, hayMas = true, cargando = false, respondiendoA = null;
function item(c) {
  const d = document.createElement('div');
  d.setAttribute('data-e2e', 'comment-level-1');
  d.innerHTML = `<a data-e2e="comment-username-1" href="/@${c.user.unique_id}">${c.user.unique_id}</a>
    <span>${c.text}</span><button data-e2e="comment-reply-1">Responder</button>
    <button data-e2e="comment-more-1">…</button><div class="respuestas"></div>`;
  d.querySelector('[data-e2e=comment-reply-1]').onclick = () => { respondiendoA = d; editor.focus(); };
  d.querySelector('[data-e2e=comment-more-1]').onclick = () => {
    const m = document.createElement('div'); m.setAttribute('role', 'menu');
    m.innerHTML = '<div role="menuitem">Fijar</div>';
    m.firstChild.onclick = () => { d.insertAdjacentHTML('afterbegin', '<span>Fijado</span>'); m.remove(); };
    document.body.appendChild(m);
  };
  return d;
}
async function cargar() {
  if (cargando || !hayMas) return; cargando = true;
  const r = await (await fetch(`/api/comment/list/?aweme_id=${reel}&cursor=${cursor}`)).json();
  r.comments.forEach(c => lista.appendChild(item(c)));
  cursor = r.cursor; hayMas = !!r.has_more; cargando = false;
}
window.addEventListener('wheel', () => cargar());
cargar();
document.querySelector('[data-e2e=comment-post]').onclick = async () => {
  const texto = editor.innerText;
  const r = await fetch('/api/comment/publish/', {method: 'POST', body: JSON.stringify({reel, texto, responde: !!respondiendoA})});
  if (!r.ok) return;
  editor.innerHTML = '';
  const c = {user: {unique_id: cuenta}, text: texto};
  if (respondiendoA) respondiendoA.querySelector('.respuestas').appendChild(item(c));
  else lista.prepend(item(c));
  respondiendoA = null;
};
</script>""" % {"reel": json.dumps(reel_id), "cuenta": json.dumps(self.cuenta)}
        # Como la real: el vídeo abre en «Podría interesarte»; los comentarios salen al pulsar el icono.
        cuerpo = ('<button data-e2e="comment-icon" onclick="document.getElementById(\'zona\').hidden=false">💬</button>'
                  '<div id="zona" hidden style="height:400px;overflow:auto"><div data-e2e="comment-list"></div></div>'
                  '<div data-e2e="comment-input"><div contenteditable="true"></div></div>'
                  f'<button data-e2e="comment-post">Publicar</button>{script}')
        return self._pagina(cuerpo, video={"itemInfo": {"itemStruct": {"id": reel_id, "createTime": self.publicado}}})

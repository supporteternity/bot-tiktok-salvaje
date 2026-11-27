/* Panel del bot: mejoras progresivas. Sin esto, todo el panel sigue funcionando. */
(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const sinMovimiento = matchMedia('(prefers-reduced-motion: reduce)').matches;
  document.documentElement.classList.add('con-js');  // muestra lo que solo funciona con JS (chips, buscador)

  /* Botón de modo: cada clic cambia entre claro y oscuro. Si no se ha pulsado nunca, se sigue a la computadora.
   La elección se guarda en este navegador. */
const botonTema = $('[data-boton-tema]');
if (botonTema) {
  const raiz = document.documentElement;
  const sistemaOscuro = matchMedia('(prefers-color-scheme: dark)');
  const esOscuro = () => raiz.dataset.tema ? raiz.dataset.tema === 'oscuro' : sistemaOscuro.matches;
  const rotular = () => {
    const oscuro = esOscuro();
    raiz.classList.toggle('se-ve-oscuro', oscuro);
    const texto = oscuro ? 'Modo claro' : 'Modo oscuro';
    $('.boton-tema-texto', botonTema).textContent = texto;
    botonTema.setAttribute('aria-label', oscuro ? 'Cambiar a modo claro' : 'Cambiar a modo oscuro');
    botonTema.title = `Cambiar a ${texto.toLowerCase()}`;
  };
  botonTema.addEventListener('click', () => {
    const nuevo = esOscuro() ? 'claro' : 'oscuro';
    raiz.dataset.tema = nuevo;
    try { localStorage.setItem('tema-panel', nuevo); } catch (e) { /* sin almacenamiento: dura hasta recargar */ }
    rotular();
  });
  sistemaOscuro.addEventListener?.('change', rotular);
  rotular();
}

/* Menú del celular: cerrar con Escape o tocando fuera. */
  const menu = $('.menu-movil');
  if (menu) {
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && menu.open) { menu.open = false; $('summary', menu).focus(); }
    });
    document.addEventListener('click', e => { if (menu.open && !menu.contains(e.target)) menu.open = false; });
  }

  /* Botones de envío: «Guardando…» y sin doble clic. */
  document.addEventListener('submit', e => {
    const form = e.target;
    if (e.defaultPrevented) return;
    const boton = e.submitter || $('button[type=submit], button:not([type])', form);
    form.classList.remove('con-cambios');
    form.dataset.enviando = '1';
    if (boton && boton.dataset.ocupado) {
      // Se aplaza para que el navegador lea el valor del botón antes de desactivarlo.
      setTimeout(() => {
        boton.dataset.textoOriginal = boton.lastElementChild.textContent;
        boton.setAttribute('aria-busy', 'true'); boton.disabled = true; boton.lastElementChild.textContent = boton.dataset.ocupado;
      }, 0);
    }
  });

  window.addEventListener('pageshow', e => {
    if (!e.persisted) return;
    $$('button[aria-busy="true"]').forEach(b => {
      b.disabled = false; b.removeAttribute('aria-busy');
      if (b.dataset.textoOriginal) b.lastElementChild.textContent = b.dataset.textoOriginal;
    });
    $$('form').forEach(f => { f.removeAttribute('data-enviando'); f.removeAttribute('data-confirmado'); });
  });

  /* Confirmación con <dialog> para acciones que suben el riesgo. */
  $$('form[data-confirmar]').forEach(form => {
    const dialogo = document.getElementById(form.dataset.confirmar);
    if (!dialogo || typeof dialogo.showModal !== 'function') {
      // Sin <dialog> con ese id: el atributo trae la pregunta (p. ej. «¿Desconectar @cuenta?»).
      form.addEventListener('submit', e => { if (!window.confirm(form.dataset.confirmar)) e.preventDefault(); });
      return;
    }
    form.addEventListener('submit', e => {
      if (form.dataset.confirmado) return;
      e.preventDefault();
      dialogo.showModal();
    });
    $('[data-aceptar]', dialogo).addEventListener('click', () => {
      dialogo.close(); form.dataset.confirmado = '1'; form.requestSubmit();
    });
    $('[data-cancelar]', dialogo).addEventListener('click', () => dialogo.close());
  });

  /* Formularios con «cambios sin guardar». */
  const vigilados = $$('form[data-vigilar]');
  vigilados.forEach(form => {
    const marcar = () => form.classList.add('con-cambios');
    form.addEventListener('input', marcar);
    form.addEventListener('change', marcar);
  });
  if (vigilados.length) {
    window.addEventListener('beforeunload', e => {
      if (vigilados.some(f => f.classList.contains('con-cambios') && !f.dataset.enviando)) { e.preventDefault(); e.returnValue = ''; }
    });
  }

  /* Mostrar u ocultar contraseñas. */
  $$('[data-ver-clave]').forEach(boton => {
    const campo = document.getElementById(boton.dataset.verClave);
    boton.addEventListener('click', () => {
      const ver = campo.type === 'password';
      campo.type = ver ? 'text' : 'password';
      boton.setAttribute('aria-pressed', String(ver));
      boton.lastElementChild.textContent = ver ? 'Ocultar' : 'Mostrar';
    });
  });

  /* Cifras: animación breve cuando cambian. */
  function animarCifra(el, desde, hasta) {
    el.classList.remove('cambio'); void el.offsetWidth; el.classList.add('cambio');
    if (sinMovimiento || !Number.isFinite(desde) || !Number.isFinite(hasta) || desde === hasta) { el.textContent = hasta; return; }
    const t0 = performance.now(), dur = 600;
    const paso = t => {
      const p = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(desde + (hasta - desde) * e);
      if (p < 1) requestAnimationFrame(paso);
    };
    requestAnimationFrame(paso);
  }

  /* Inicio: se actualiza solo cada 20 s (vuelve a pedir la misma página y cambia los bloques marcados). */
  const vivos = $$('[data-vivo]');
  if (vivos.length) {
    const anuncio = $('#anuncio-estado');
    let ultima = Date.now();
    const pintarSello = () => {
      const sello = $('[data-actualizado]');
      if (!sello) return;
      const s = Math.round((Date.now() - ultima) / 1000);
      sello.textContent = s < 5 ? 'actualizado ahora' : `actualizado hace ${s < 60 ? s + ' s' : Math.round(s / 60) + ' min'}`;
    };
    async function refrescar() {
      if (document.hidden) return;
      if (document.querySelector('dialog[open]')) return;
      try {
        const r = await fetch(location.pathname, { headers: { 'Accept': 'text/html' }, cache: 'no-store', credentials: 'same-origin' });
        if (!r.ok || r.redirected) return;
        const nuevo = new DOMParser().parseFromString(await r.text(), 'text/html');
        vivos.forEach(viejo => {
          const otro = nuevo.getElementById(viejo.id);
          if (!otro || viejo.contains(document.activeElement)) return;
          const selloViejo = $('[data-actualizado]', viejo), selloNuevo = $('[data-actualizado]', otro);
          if (selloViejo && selloNuevo) selloNuevo.textContent = selloViejo.textContent;
          if (otro.innerHTML === viejo.innerHTML && otro.className === viejo.className) return;
          const antes = viejo.dataset.vivoClave, despues = otro.dataset.vivoClave;
          const cifras = new Map($$('[data-cifra]', viejo).map(c => [c.dataset.cifra, Number(c.textContent)]));
          viejo.className = otro.className;
          viejo.dataset.vivoClave = despues || '';
          viejo.innerHTML = otro.innerHTML;
          $$('[data-cifra]', viejo).forEach(c => {
            const previo = cifras.get(c.dataset.cifra), ahora = Number(c.textContent);
            if (previo !== undefined && previo !== ahora) animarCifra(c, previo, ahora);
          });
          if (antes && despues && antes !== despues && anuncio) {
            anuncio.textContent = 'El estado del bot cambió: ' + ($('[data-titulo-estado]', viejo)?.textContent || despues);
          }
        });
        const pildora = $('.pildora-estado'), pildoraNueva = nuevo.querySelector('.pildora-estado');
        if (pildora && pildoraNueva) { pildora.className = pildoraNueva.className; pildora.innerHTML = pildoraNueva.innerHTML; }
        ultima = Date.now(); pintarSello();
      } catch (_) { /* sin conexión con el panel: se reintenta en la siguiente vuelta */ }
    }
    setInterval(refrescar, 20000);
    setInterval(pintarSello, 5000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden && Date.now() - ultima > 20000) refrescar(); });
  }

  /* Textos: insertar {nombre}/{landing}, contador y aviso de {landing} en vivo. */
  $$('form[data-texto]').forEach(form => {
    const area = $('textarea', form);
    const contador = $('[data-contador]', form);
    const avisoLanding = $('[data-aviso-landing]', form);
    const vista = $('[data-vista]', form);
    const actualizar = () => {
      if (contador) contador.textContent = `${area.value.length} caracteres`;
      if (avisoLanding) avisoLanding.hidden = area.value.includes('{landing}') || !area.value.trim();
      if (vista) vista.classList.toggle('desactualizada', area.value !== area.defaultValue);
    };
    let tuvoFoco = false;
    area.addEventListener('focus', () => { tuvoFoco = true; });
    $$('[data-insertar]', form).forEach(b => b.addEventListener('click', () => {
      const i = tuvoFoco ? area.selectionStart : area.value.length, f = tuvoFoco ? area.selectionEnd : i;
      const antes = i > 0 && !/\s/.test(area.value[i - 1]) ? ' ' : '';
      const despues = f < area.value.length && !/\s/.test(area.value[f]) ? ' ' : '';
      area.setRangeText(antes + b.dataset.insertar + despues, i, f, 'end'); area.focus();
      area.dispatchEvent(new Event('input', { bubbles: true }));
    }));
    area.addEventListener('input', actualizar);
    actualizar();
  });

  /* Registro: buscador instantáneo sobre las filas cargadas. */
  const buscador = $('[data-buscar]');
  if (buscador) {
    const filas = $$('[data-fila]');
    const contador = $('[data-cuantas]');
    const vacio = $('[data-sin-resultados]');
    const normal = s => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
    const indice = filas.map(f => normal(f.textContent));
    buscador.addEventListener('input', () => {
      const q = normal(buscador.value.trim());
      let n = 0;
      filas.forEach((f, i) => { const ver = !q || indice[i].includes(q); f.hidden = !ver; n += ver; });
      if (contador) contador.textContent = n;
      if (vacio) vacio.hidden = n > 0;
    });
  }

  /* Crear contraseña: comprobar en vivo que las dos coinciden. */
  const repetida = $('[data-repite]');
  if (repetida) {
    const nueva = document.getElementById(repetida.dataset.repite);
    const nota = $('[data-coinciden]');
    const comprobar = () => {
      if (!repetida.value) { nota.hidden = true; repetida.setCustomValidity(''); return; }
      const ok = repetida.value === nueva.value;
      nota.hidden = false;
      nota.className = 'coinciden ' + (ok ? 'si' : 'no');
      nota.textContent = ok ? 'Las dos contraseñas coinciden.' : 'Todavía no coinciden.';
      repetida.setCustomValidity(ok ? '' : 'Las dos contraseñas no coinciden.');
    };
    repetida.addEventListener('input', comprobar);
    nueva.addEventListener('input', comprobar);
  }
})();

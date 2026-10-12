/* tablas.js — ordenar + filtrar tablas sin recargar la pagina (jdemonasterio 2026-10-12).
 *
 * Vanilla JS, sin librerias externas. Opt-in por markup, nada se activa solo:
 *
 *   <table class="tabla-interactiva" data-tabla-id="licitaciones" data-limite="200">
 *     <thead><tr>
 *       <th data-tipo="cat">Semaforo</th>                 columna con filtro (menu de checkboxes)
 *       <th data-tipo="fecha">Fecha</th>                   columna ordenable (fecha dd/mm/aaaa o ISO)
 *       <th data-tipo="num">Monto</th>                      columna ordenable (numero, $, %, "M", "mil M")
 *       <th data-tipo="texto">Proceso</th>                  columna ordenable (texto, alfabetico es-AR)
 *       <th data-tipo="cat" data-separador=", ">Productos</th>  celda con varios valores (lista separada)
 *       <th>Accion</th>                                     sin data-tipo: no se toca (default "ninguno")
 *     </tr></thead>
 *     <tbody>
 *       <tr>...<td data-valor="1234.5">$ 1.234,50</td>...</tr>   data-valor: valor crudo si el texto
 *                                                                 mostrado esta formateado (preferido
 *                                                                 para plata/porcentaje/fecha: usar el
 *                                                                 numero/ISO que ya esta en el contexto
 *                                                                 de Jinja, antes del filtro |ars/|millones/
 *                                                                 |pct/|fecha)
 *       <tr data-fila-detalle>...</tr>                       fila de detalle: se mueve/oculta SIEMPRE junto
 *                                                             con la fila principal que la precede (no
 *                                                             necesita id propio: alcanza con venir
 *                                                             inmediatamente despues en el <tbody>)
 *     </tbody>
 *   </table>
 *
 * Que hace:
 *   - Click/Enter en un encabezado con data-tipo="num|fecha|texto" ordena asc/desc (flecha + aria-sort).
 *     Vacio ("—", "-", "") siempre al final, en los dos sentidos.
 *   - data-tipo="cat" agrega un boton (embudo) con un menu: buscador, "Todos"/"Ninguno" y un checkbox por
 *     valor distinto (con cuenta de filas). Celdas con data-separador se parten en varios valores (p.ej.
 *     "PROVEEDOR A, PROVEEDOR B"). Los filtros de varias columnas se combinan con Y (AND).
 *   - Arriba de la tabla aparece "Mostrando X de Y filas" + "Limpiar filtros".
 *   - Todo sobrevive a un refresco en el lugar de soft-nav.js (evento 'softnav:swapped'): el estado
 *     (orden + filtros activos) se guarda en sessionStorage por pagina+tabla y se vuelve a aplicar solo.
 *
 * Que NO hace (a proposito): no pagina, no toca filtros de servidor, no suma/agrupa. Es solo sobre las
 * filas YA cargadas (si el servidor limito la lista con LIMIT, data-limite lo avisa en la linea de estado).
 */
(function () {
    if (window.Tablas) return;

    // ── Parseo de celdas (formatos de sobreprecios/web/format.py) ──────────────────────────────

    function esVacio(s) {
        if (s == null) return true;
        const t = String(s).trim();
        return t === '' || t === '—' || t === '-';
    }

    function parseNum(raw) {
        if (esVacio(raw)) return null;
        let s = String(raw).trim();
        s = s.replace(/^(US\$|AR\$|\$)\s*/i, '');
        let neg = false;
        if (/^[-−]/.test(s)) { neg = true; s = s.slice(1).trim(); }
        else if (s[0] === '+') { s = s.slice(1).trim(); }
        let mult = 1;
        if (/\bmil\s*M\b/i.test(s)) { mult = 1e9; s = s.replace(/\bmil\s*M\b/i, '').trim(); }
        else if (/\bM\b/i.test(s)) { mult = 1e6; s = s.replace(/\bM\b/i, '').trim(); }
        s = s.replace(/%/g, '').trim();
        if (!s) return null;
        s = s.replace(/\./g, '').replace(',', '.');
        const n = parseFloat(s);
        if (!isFinite(n)) return null;
        return (neg ? -n : n) * mult;
    }

    // data-valor es el numero CRUDO de Python/Jinja (p.ej. "25.0", "-8.9"): punto decimal
    // estandar, SIN separador de miles — no pasa por el formato argentino de parseNum (que
    // asumiria el punto como separador de miles y rompería el valor, p.ej. "25.0" -> 250).
    function parseNumCrudo(raw) {
        if (esVacio(raw)) return null;
        const n = parseFloat(String(raw).trim());
        return isFinite(n) ? n : null;
    }

    function parseFecha(raw) {
        if (esVacio(raw)) return null;
        const s = String(raw).trim();
        let m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(s);
        if (m) return Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0));
        m = /^(\d{2})\/(\d{2})\/(\d{4})(?:\s+(\d{2}):(\d{2}))?/.exec(s);
        if (m) return Date.UTC(+m[3], +m[2] - 1, +m[1], +(m[4] || 0), +(m[5] || 0));
        const t = Date.parse(s);
        return isFinite(t) ? t : null;
    }

    function parseTexto(raw) {
        if (esVacio(raw)) return null;
        return String(raw).trim();
    }

    function celda(main, idx) { return main.children[idx] || null; }

    function valorCrudo(td) {
        return td.hasAttribute('data-valor') ? td.getAttribute('data-valor') : td.textContent;
    }

    function valorOrden(td, tipo) {
        if (!td) return null;
        const tieneValor = td.hasAttribute('data-valor');
        const raw = tieneValor ? td.getAttribute('data-valor') : td.textContent;
        if (tipo === 'num') return tieneValor ? parseNumCrudo(raw) : parseNum(raw);
        if (tipo === 'fecha') return parseFecha(raw);   // ISO o dd/mm/aaaa: el mismo parser sirve para los dos casos
        return parseTexto(raw);
    }

    const NA_LABEL = '(sin dato)';

    function valoresDeCelda(main, idx, separador) {
        const td = celda(main, idx);
        if (!td) return [NA_LABEL];
        const raw = valorCrudo(td);
        let partes = separador ? String(raw).split(separador) : [String(raw)];
        partes = partes.map(s => s.trim()).filter(s => !esVacio(s));
        return partes.length ? partes : [NA_LABEL];
    }

    // ── Filas agrupadas: una fila principal + sus filas de detalle (data-fila-detalle) ─────────

    function grupos(table) {
        const tbody = table.tBodies[0];
        if (!tbody) return [];
        const out = [];
        let actual = null;
        Array.from(tbody.rows).forEach(tr => {
            if (tr.hasAttribute('data-fila-detalle')) {
                if (actual) actual.details.push(tr);
            } else {
                actual = { main: tr, details: [] };
                out.push(actual);
            }
        });
        return out;
    }

    // ── Estado por tabla (en memoria + sessionStorage) ──────────────────────────────────────────

    const estados = new WeakMap();

    function claveEstado(tableId) {
        try { return 'tablas:' + location.pathname + '::' + tableId; }
        catch (e) { return 'tablas:' + tableId; }
    }

    function cargarEstado(tableId) {
        try {
            const raw = sessionStorage.getItem(claveEstado(tableId));
            if (!raw) return null;
            const persist = JSON.parse(raw);
            const filtros = {};
            Object.entries(persist.filtros || {}).forEach(([k, arr]) => { filtros[k] = new Set(arr); });
            return { sortCol: persist.sortCol, sortDir: persist.sortDir, filtros };
        } catch (e) { return null; }
    }

    function guardarEstado(table) {
        const estado = estados.get(table);
        if (!estado) return;
        try {
            const filtros = {};
            Object.entries(estado.filtros).forEach(([k, set]) => { filtros[k] = Array.from(set); });
            sessionStorage.setItem(claveEstado(estado.tableId),
                JSON.stringify({ sortCol: estado.sortCol, sortDir: estado.sortDir, filtros }));
        } catch (e) { /* sin sessionStorage (privado/bloqueado): sigue funcionando solo en memoria */ }
    }

    // ── Orden ────────────────────────────────────────────────────────────────────────────────────

    function haceComparador(idx, tipo, dir) {
        return (g1, g2) => {
            const v1 = valorOrden(celda(g1.main, idx), tipo);
            const v2 = valorOrden(celda(g2.main, idx), tipo);
            const n1 = v1 == null, n2 = v2 == null;
            if (n1 && n2) return 0;
            if (n1) return 1;    // vacio siempre al final, en los dos sentidos
            if (n2) return -1;
            let c;
            if (tipo === 'texto') c = String(v1).localeCompare(String(v2), 'es', { sensitivity: 'base', numeric: true });
            else c = v1 < v2 ? -1 : (v1 > v2 ? 1 : 0);
            return dir === 'desc' ? -c : c;
        };
    }

    function ordenarPor(table, idx, dir) {
        const estado = estados.get(table);
        const tbody = table.tBodies[0];
        if (!estado || !tbody) return;
        const tipo = estado.columnas[idx].tipo;
        const filas = grupos(table);
        filas.sort(haceComparador(idx, tipo, dir));
        filas.forEach(g => { tbody.appendChild(g.main); g.details.forEach(d => tbody.appendChild(d)); });
        estado.sortCol = idx;
        estado.sortDir = dir;
        estado.ths.forEach((th, i) => {
            const t = estado.columnas[i].tipo;
            if (t !== 'num' && t !== 'fecha' && t !== 'texto') return;
            th.setAttribute('aria-sort', i === idx ? (dir === 'asc' ? 'ascending' : 'descending') : 'none');
        });
        guardarEstado(table);
    }

    function alternarOrden(table, idx) {
        const estado = estados.get(table);
        let dir = 'asc';
        if (estado.sortCol === idx) dir = estado.sortDir === 'asc' ? 'desc' : 'asc';
        ordenarPor(table, idx, dir);
    }

    function armarHeaderOrdenable(table, th, idx) {
        th.innerHTML = '<button type="button" class="tabla-th-btn">' + th.innerHTML +
            '<span class="tabla-th-arrow" aria-hidden="true"></span></button>';
        th.setAttribute('aria-sort', 'none');
        th.querySelector('.tabla-th-btn').addEventListener('click', () => alternarOrden(table, idx));
    }

    // ── Filtro categorico ────────────────────────────────────────────────────────────────────────

    function distintos(table, idx, separador) {
        const conteo = new Map();
        grupos(table).forEach(g => {
            new Set(valoresDeCelda(g.main, idx, separador)).forEach(v => conteo.set(v, (conteo.get(v) || 0) + 1));
        });
        return conteo;
    }

    function filaVisible(estado, main) {
        for (const key of Object.keys(estado.filtros)) {
            const set = estado.filtros[key];
            if (!set) continue;
            const idx = Number(key);
            const vals = valoresDeCelda(main, idx, estado.columnas[idx].separador);
            if (!vals.some(v => set.has(v))) return false;
        }
        return true;
    }

    function aplicarFiltros(table) {
        const estado = estados.get(table);
        if (!estado) return;
        const filas = grupos(table);
        let visibles = 0;
        filas.forEach(g => {
            const ok = filaVisible(estado, g.main);
            g.main.style.display = ok ? '' : 'none';
            g.details.forEach(d => { d.style.display = ok ? '' : 'none'; });
            if (ok) visibles++;
        });
        actualizarStatus(table, visibles, filas.length);
    }

    function actualizarBadge(table, idx) {
        const estado = estados.get(table);
        const th = estado.ths[idx];
        const badge = th && th.querySelector('.tabla-filtro-count');
        if (!badge) return;
        const set = estado.filtros[idx];
        if (!set) { badge.hidden = true; badge.textContent = '0'; }
        else { badge.hidden = false; badge.textContent = String(set.size); }
    }

    function armarHeaderFiltrable(table, th, idx) {
        const etiqueta = th.textContent.trim();
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'tabla-filtro-btn';
        btn.setAttribute('aria-haspopup', 'true');
        btn.setAttribute('aria-expanded', 'false');
        btn.setAttribute('aria-label', 'Filtrar por ' + etiqueta);
        btn.innerHTML = SVG_EMBUDO;
        const cuenta = document.createElement('span');
        cuenta.className = 'tabla-filtro-count';
        cuenta.hidden = true;
        cuenta.textContent = '0';
        btn.appendChild(cuenta);
        th.appendChild(document.createTextNode(' '));
        th.appendChild(btn);
        btn.addEventListener('click', (ev) => { ev.stopPropagation(); togglePanel(table, idx, btn); });
    }

    const SVG_EMBUDO = '<svg width="11" height="11" viewBox="0 0 16 16" aria-hidden="true" focusable="false">' +
        '<path d="M1.5 1.5h13a.5.5 0 0 1 .39.81L10 8.69v4.8a.5.5 0 0 1-.28.45l-2.5 1.2A.5.5 0 0 1 6.5 ' +
        '14.7V8.7L1.1 2.32a.5.5 0 0 1 .4-.82z" fill="currentColor"/></svg>';

    // Panel singleton (fixed sobre el viewport: asi no lo recorta el scroll horizontal de .table-wrap
    // ni se sale de la pantalla en mobile).
    let panelEl = null;
    let abierto = null; // {table, idx, btn}

    function panel() {
        if (panelEl) return panelEl;
        panelEl = document.createElement('div');
        panelEl.className = 'tabla-filtro-panel';
        panelEl.setAttribute('role', 'dialog');
        panelEl.hidden = true;
        const buscador = document.createElement('input');
        buscador.type = 'search';
        buscador.className = 'tabla-filtro-busqueda';
        buscador.placeholder = 'Buscar...';
        buscador.setAttribute('aria-label', 'Buscar valor');
        buscador.addEventListener('input', buscarEnPanel);
        const acciones = document.createElement('div');
        acciones.className = 'tabla-filtro-acciones';
        const btnTodos = document.createElement('button');
        btnTodos.type = 'button'; btnTodos.className = 'btn btn-sm'; btnTodos.textContent = 'Todos';
        btnTodos.dataset.accion = 'todos';
        const btnNinguno = document.createElement('button');
        btnNinguno.type = 'button'; btnNinguno.className = 'btn btn-sm'; btnNinguno.textContent = 'Ninguno';
        btnNinguno.dataset.accion = 'ninguno';
        acciones.appendChild(btnTodos); acciones.appendChild(btnNinguno);
        const lista = document.createElement('ul');
        lista.className = 'tabla-filtro-lista';
        const pie = document.createElement('div');
        pie.className = 'tabla-filtro-footer';
        const btnCerrar = document.createElement('button');
        btnCerrar.type = 'button'; btnCerrar.className = 'btn btn-sm'; btnCerrar.textContent = 'Cerrar';
        btnCerrar.dataset.accion = 'cerrar';
        pie.appendChild(btnCerrar);
        panelEl.appendChild(buscador);
        panelEl.appendChild(acciones);
        panelEl.appendChild(lista);
        panelEl.appendChild(pie);
        document.body.appendChild(panelEl);

        panelEl.addEventListener('click', (ev) => {
            const accionBtn = ev.target.closest('[data-accion]');
            if (!accionBtn || !abierto) return;
            if (accionBtn.dataset.accion === 'todos') fijarFiltro(abierto.table, abierto.idx, null);
            else if (accionBtn.dataset.accion === 'ninguno') fijarFiltro(abierto.table, abierto.idx, new Set());
            else if (accionBtn.dataset.accion === 'cerrar') cerrarPanel(true);
        });
        panelEl.addEventListener('change', (ev) => {
            const input = ev.target.closest('input[type=checkbox]');
            if (!input || !abierto) return;
            alternarValor(abierto.table, abierto.idx, input.value, input.checked);
        });
        document.addEventListener('keydown', (ev) => {
            if (ev.key === 'Escape' && abierto) cerrarPanel(true);
        });
        document.addEventListener('mousedown', (ev) => {
            if (!abierto) return;
            if (panelEl.contains(ev.target) || ev.target === abierto.btn || abierto.btn.contains(ev.target)) return;
            cerrarPanel(false);
        });
        // Reposicionar (no cerrar) ante scroll/resize de la pagina: cerrar aca es fragil, porque
        // el propio foco del buscador (abajo) puede disparar un scroll minimo del documento para
        // traerlo a la vista, lo que cerraria el panel al toque de abrirlo.
        window.addEventListener('scroll', (ev) => {
            if (!abierto || panelEl.contains(ev.target)) return;
            posicionarPanel(panelEl, abierto.btn);
        }, true);
        window.addEventListener('resize', () => { if (abierto) posicionarPanel(panelEl, abierto.btn); });
        return panelEl;
    }

    function buscarEnPanel(ev) {
        const q = ev.target.value.trim().toLowerCase();
        panel().querySelectorAll('.tabla-filtro-lista li').forEach(li => {
            li.hidden = q.length > 0 && !li.textContent.toLowerCase().includes(q);
        });
    }

    function togglePanel(table, idx, btn) {
        if (abierto && abierto.table === table && abierto.idx === idx) { cerrarPanel(false); return; }
        const estado = estados.get(table);
        const sep = estado.columnas[idx].separador;
        const conteo = distintos(table, idx, sep);
        const activos = estado.filtros[idx];
        const p = panel();
        const lista = p.querySelector('.tabla-filtro-lista');
        lista.innerHTML = '';
        Array.from(conteo.keys())
            .sort((a, b) => a.localeCompare(b, 'es', { sensitivity: 'base', numeric: true }))
            .forEach(valor => {
                const marcado = !activos || activos.has(valor);
                const li = document.createElement('li');
                li.className = 'tabla-filtro-item';
                const label = document.createElement('label');
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.value = valor;              // propiedad del DOM: nunca se interpreta como HTML
                checkbox.checked = marcado;
                const texto = document.createElement('span');
                texto.className = 'tabla-filtro-texto';
                texto.textContent = valor;           // dato puede venir de scraping externo: siempre textContent
                const cuenta = document.createElement('span');
                cuenta.className = 'muted small';
                cuenta.textContent = ' (' + conteo.get(valor) + ')';
                label.appendChild(checkbox);
                label.appendChild(document.createTextNode(' '));
                label.appendChild(texto);
                label.appendChild(cuenta);
                li.appendChild(label);
                lista.appendChild(li);
            });
        p.querySelector('.tabla-filtro-busqueda').value = '';
        lista.scrollTop = 0;
        abierto = { table, idx, btn };
        p.hidden = false;
        posicionarPanel(p, btn);
        btn.setAttribute('aria-expanded', 'true');
        p.querySelector('.tabla-filtro-busqueda').focus();
    }

    function posicionarPanel(p, btn) {
        const rect = btn.getBoundingClientRect();
        const ancho = p.offsetWidth || 280;
        const alto = p.offsetHeight || 220;
        let left = rect.left;
        const maxLeft = window.innerWidth - ancho - 8;
        if (left > maxLeft) left = Math.max(8, maxLeft);
        if (left < 8) left = 8;
        let top = rect.bottom + 6;
        if (top + alto > window.innerHeight - 8) top = Math.max(8, rect.top - alto - 6);
        p.style.left = left + 'px';
        p.style.top = top + 'px';
    }

    function cerrarPanel(devolverFoco) {
        if (!abierto) return;
        const { btn } = abierto;
        panel().hidden = true;
        btn.setAttribute('aria-expanded', 'false');
        abierto = null;
        if (devolverFoco) btn.focus();
    }

    function alternarValor(table, idx, valor, marcado) {
        const estado = estados.get(table);
        let set = estado.filtros[idx];
        if (!set) {
            const sep = estado.columnas[idx].separador;
            set = new Set(distintos(table, idx, sep).keys());
        }
        if (marcado) set.add(valor); else set.delete(valor);
        estado.filtros[idx] = set;
        aplicarFiltros(table);
        actualizarBadge(table, idx);
        guardarEstado(table);
    }

    function fijarFiltro(table, idx, set) {
        const estado = estados.get(table);
        if (set === null) delete estado.filtros[idx];
        else estado.filtros[idx] = set;
        if (abierto && abierto.table === table && abierto.idx === idx) {
            panel().querySelectorAll('.tabla-filtro-lista input[type=checkbox]').forEach(cb => {
                cb.checked = !estado.filtros[idx] || estado.filtros[idx].has(cb.value);
            });
        }
        aplicarFiltros(table);
        actualizarBadge(table, idx);
        guardarEstado(table);
    }

    // ── Linea de estado ("Mostrando X de Y filas" + Limpiar filtros) ───────────────────────────────

    function insertarStatus(table) {
        if (table.tablasStatusBar && table.tablasStatusBar.isConnected) return table.tablasStatusBar;
        const bar = document.createElement('p');
        bar.className = 'small muted tabla-status';
        const texto = document.createElement('span');
        texto.className = 'tabla-status-texto';
        const limpiar = document.createElement('button');
        limpiar.type = 'button';
        limpiar.className = 'btn btn-sm';
        limpiar.textContent = 'Limpiar filtros';
        limpiar.hidden = true;
        limpiar.addEventListener('click', () => limpiarFiltros(table));
        bar.appendChild(texto);
        bar.appendChild(limpiar);
        const wrap = table.closest('.table-wrap') || table;
        wrap.parentNode.insertBefore(bar, wrap);
        table.tablasStatusBar = bar;
        return bar;
    }

    function actualizarStatus(table, visibles, total) {
        const bar = insertarStatus(table);
        let msg = 'Mostrando ' + visibles + ' de ' + total + (total === 1 ? ' fila.' : ' filas.');
        const limite = table.dataset.limite;
        if (limite && total >= Number(limite)) msg += ' Se cargaron como maximo las primeras ' + limite + ': puede haber mas.';
        bar.querySelector('.tabla-status-texto').textContent = msg;
        const estado = estados.get(table);
        const hayFiltro = Object.values(estado.filtros).some(s => s != null);
        bar.querySelector('.btn').hidden = !hayFiltro;
    }

    function limpiarFiltros(table) {
        const estado = estados.get(table);
        estado.filtros = {};
        estado.columnas.forEach((c, idx) => { if (c.tipo === 'cat') actualizarBadge(table, idx); });
        if (abierto && abierto.table === table) {
            panel().querySelectorAll('.tabla-filtro-lista input[type=checkbox]').forEach(cb => { cb.checked = true; });
        }
        aplicarFiltros(table);
        guardarEstado(table);
    }

    // ── Init ─────────────────────────────────────────────────────────────────────────────────────

    function initTabla(table) {
        if (table.dataset.tablasInit === '1') return;
        const tableId = table.dataset.tablaId;
        if (!tableId) return;   // opt-in estricto: sin data-tabla-id no se toca
        const headRow = table.tHead && table.tHead.rows[table.tHead.rows.length - 1];
        if (!headRow) return;
        const ths = Array.from(headRow.cells);
        const columnas = ths.map(th => ({
            tipo: th.dataset.tipo || 'ninguno',
            separador: th.dataset.separador || null,
        }));

        const guardado = cargarEstado(tableId);
        const filtros = {};
        if (guardado) {
            Object.entries(guardado.filtros).forEach(([k, set]) => {
                const i = Number(k);
                if (columnas[i] && columnas[i].tipo === 'cat') filtros[i] = set;
            });
        }
        let sortCol = guardado && guardado.sortCol != null ? guardado.sortCol : null;
        const sortDir = (guardado && guardado.sortDir) || 'asc';
        if (sortCol != null) {
            const t = columnas[sortCol] && columnas[sortCol].tipo;
            if (t !== 'num' && t !== 'fecha' && t !== 'texto') sortCol = null;
        }

        const estado = { tableId, columnas, ths, sortCol: null, sortDir: 'asc', filtros };
        estados.set(table, estado);

        ths.forEach((th, idx) => {
            const tipo = columnas[idx].tipo;
            if (tipo === 'num' || tipo === 'fecha' || tipo === 'texto') armarHeaderOrdenable(table, th, idx);
            else if (tipo === 'cat') armarHeaderFiltrable(table, th, idx);
        });

        if (sortCol != null) ordenarPor(table, sortCol, sortDir);
        aplicarFiltros(table);
        columnas.forEach((c, idx) => { if (c.tipo === 'cat') actualizarBadge(table, idx); });

        table.dataset.tablasInit = '1';
    }

    function initAll() {
        document.querySelectorAll('table.tabla-interactiva').forEach(initTabla);
    }

    document.addEventListener('DOMContentLoaded', initAll);
    document.addEventListener('softnav:swapped', initAll);

    window.Tablas = { init: initAll };
})();

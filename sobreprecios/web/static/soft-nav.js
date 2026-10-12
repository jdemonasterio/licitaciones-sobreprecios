/* soft-nav.js — in-place refresh for the WHOLE webui (jdemonasterio 2026-10-10).
 *
 * Rule ("the backend never resets the page"):
 * an action that runs in the backend never reloads the page, never jumps to
 * the top and never wipes what the user is doing (selections, typed text,
 * numbers, open panels). It runs, and when it is done the page refreshes IN
 * PLACE and a toast tells the result.
 *
 * How:
 *   - base.html marks <main data-soft-root>. Every POST <form> inside it goes
 *     through fetch. The server keeps answering with its usual 303 redirect.
 *     If the redirect lands on the SAME page, the new root replaces the old
 *     one in place; if it lands on another page, it is a normal navigation.
 *   - Background-job pollers call SoftNav.load(url) instead of location.href /
 *     location.reload().
 *
 * What survives a swap:
 *   - Fields the user changed and did not submit: text, textarea, select,
 *     checkbox/radio. "Changed" = differs from what the server last rendered.
 *     Three-way rule: if the server ALSO changed that field in the new render
 *     (fresh data, e.g. a regenerated draft), the server wins.
 *   - Fields of the form just submitted: the server wins (what it processed is
 *     done; never re-fill a message that was just sent). Fields marked
 *     data-soft-keep are kept anyway (settings like "dias habiles", offering).
 *   - Focus + caret, if the user was typing when a job finished.
 *   - <details> open state (keyed by the closest [data-soft-key] / id).
 *   - Scroll, anchored to what the user was looking at: the clicked form,
 *     else its closest [data-soft-key] / [id] ancestor. Banners and messages
 *     appearing at the top no longer push the page down.
 *   - Page JS state registered with SoftNav.keep(key, save, restore)
 *     (current view, sort, active tab...).
 *   - Query params listed in SoftNav.keepParams([...]) (sort / order) when the
 *     server redirects to a URL without them.
 *   - Out-of-order responses are dropped: a poll that started before a click
 *     never overwrites the page the click produced.
 *
 * Inline <script>s inside the root RE-RUN after each swap, so they must be
 * idempotent: IIFEs, no top-level const/let, timers via SoftNav.interval /
 * SoftNav.timeout (cleared at each swap). A page-lifetime script (top-level
 * state, its own polling loop) is marked <script data-soft-once>: it runs only
 * on the first load, so it must re-query elements at call time, use event
 * delegation, and re-apply its view on the "softnav:swapped" event.
 *
 * Opt out per form with data-soft="off" (or by calling preventDefault in its
 * own onsubmit, like the streaming upload forms do).
 */
(function () {
    if (window.SoftNav) return;

    const hooks = new Map();
    let timers = [];
    let keepParamNames = [];
    let seqStarted = 0, seqApplied = 0;
    let toastTimer = null;

    const rootEl = () => document.querySelector('[data-soft-root]');

    // ── toast ──
    function toast(msg, opts) {
        if (!msg) return;
        let t = document.getElementById('soft-toast');
        if (!t) {
            t = document.createElement('div');
            t.id = 'soft-toast';
            t.setAttribute('role', 'status');
            document.body.appendChild(t);
        }
        t.textContent = msg;   // never innerHTML: messages carry text from external sources
        t.classList.toggle('soft-toast-error', /^ERROR|fall[oó]|❌|✗/i.test(msg));
        t.style.display = 'block';
        clearTimeout(toastTimer);
        const ms = (opts && opts.ms) || 7000;
        if (ms > 0) toastTimer = setTimeout(() => { t.style.display = 'none'; }, ms);
    }

    // ── identity of forms / fields / anchors across swaps ──
    const SKIP_TYPES = ['hidden', 'submit', 'button', 'reset', 'image', 'file'];
    function formKey(form) {
        // a form with an id is that id; otherwise its action + the hidden
        // inputs that tell rows apart (slug, state, variant...). Hidden inputs
        // filled by JS at submit time must not count: mark them data-soft-dynamic.
        if (form.id) return '#' + form.id;
        const hid = Array.from(form.querySelectorAll('input[type=hidden]'))
            .filter(i => i.name && !i.hasAttribute('data-soft-dynamic'))
            .map(i => i.name + '=' + i.value).join('&');
        return (form.getAttribute('action') || '') + '?' + hid;
    }
    function fieldKey(el) {
        // flatpickr turns the real date input into type=hidden (the visible one
        // is a nameless clone): it is still the user's value, so it counts.
        const picker = el && el.classList && el.classList.contains('flatpickr-input') && el.name;
        if (!el || (SKIP_TYPES.includes(el.type) && !picker) || el.hasAttribute('data-soft-skip')) return null;
        const box = el.type === 'checkbox' || el.type === 'radio';
        if (el.form && el.name) return formKey(el.form) + '#' + el.name + (box ? '=' + el.value : '');
        if (el.name) return 'nf:' + el.name + (box ? '=' + el.value : '');
        if (el.id) return 'id:' + el.id;
        return null;
    }
    const fields = root => Array.from(root.querySelectorAll('input, select, textarea'));
    const isBox = el => el.type === 'checkbox' || el.type === 'radio';
    function defaultOf(el) {
        if (isBox(el)) return el.defaultChecked;
        if (el.tagName === 'SELECT') {
            let def = el.options.length ? el.options[0].value : '';
            Array.from(el.options).forEach(o => { if (o.defaultSelected) def = o.value; });
            return def;
        }
        return el.defaultValue;
    }
    const valueOf = el => isBox(el) ? el.checked : el.value;
    function captureFields(root, submittedForm) {
        const saved = new Map();
        fields(root).forEach(el => {
            const k = fieldKey(el);
            if (!k || valueOf(el) === defaultOf(el)) return;
            if (submittedForm && el.form === submittedForm && !el.hasAttribute('data-soft-keep')) return;
            saved.set(k, {v: valueOf(el), d: defaultOf(el), keep: el.hasAttribute('data-soft-keep')});
        });
        return saved;
    }
    function restoreFields(root, saved) {
        if (!saved.size) return;
        fields(root).forEach(el => {
            const k = fieldKey(el);
            const s = k && saved.get(k);
            if (!s) return;
            // three-way: the server changed this field too → the server wins
            if (!s.keep && defaultOf(el) !== s.d) return;
            if (isBox(el)) el.checked = s.v;
            else if (el.tagName !== 'SELECT' || Array.from(el.options).some(o => o.value === s.v)) el.value = s.v;
        });
    }
    function captureFocus(root) {
        const a = document.activeElement;
        const k = a && root.contains(a) ? fieldKey(a) : null;
        if (!k) return null;
        let sel = null;
        try { if (a.selectionStart !== null && a.selectionStart !== undefined) sel = [a.selectionStart, a.selectionEnd, a.selectionDirection]; } catch (e) { /* no caret */ }
        return {k, sel};
    }
    function restoreFocus(root, f) {
        if (!f) return;
        const el = fields(root).find(e => fieldKey(e) === f.k);
        if (!el || el.disabled) return;
        el.focus({preventScroll: true});
        if (f.sel) try { el.setSelectionRange(f.sel[0], f.sel[1], f.sel[2]); } catch (e) { /* no caret */ }
    }
    const hostKey = n => n.getAttribute('data-soft-key') || (n.id ? '#' + n.id : null);
    function detailsKey(d) {
        const host = d.parentElement && d.parentElement.closest('[data-soft-key], [id]');
        const hk = host ? hostKey(host) : 'root';
        const scope = host || rootEl();
        return hk + '#' + Array.from(scope.querySelectorAll('details')).indexOf(d);
    }
    function captureDetails(root) {
        const m = new Map();
        root.querySelectorAll('details').forEach(d => m.set(detailsKey(d), d.open));
        return m;
    }
    function restoreDetails(root, m) {
        root.querySelectorAll('details').forEach(d => {
            const k = detailsKey(d);
            if (m.has(k)) d.open = m.get(k);
        });
    }
    function anchorChain(el) {
        const chain = [];
        const form = el.closest('form');
        if (form) chain.push({form: formKey(form), top: form.getBoundingClientRect().top});
        let n = el.closest('[data-soft-key], [id]');
        while (n && n !== document.body) {
            const k = hostKey(n);
            if (k && n.getAttribute('data-soft-root') === null) chain.push({key: k, top: n.getBoundingClientRect().top});
            n = n.parentElement && n.parentElement.closest('[data-soft-key], [id]');
        }
        return chain;
    }
    // Anchor on what the user is LOOKING AT: the clicked button / focused field
    // only when they are on screen (a toolbar at the top of the page, scrolled
    // away, would pull the page around); else the element at reading height.
    function onScreen(el) {
        const r = el.getBoundingClientRect();
        return r.height > 0 && r.bottom > 0 && r.top < window.innerHeight;
    }
    function captureAnchor(root, submitter) {
        let el = submitter && root.contains(submitter) && onScreen(submitter) ? submitter : null;
        const a = document.activeElement;
        if (!el && a && a !== document.body && root.contains(a) && onScreen(a)) el = a;
        const hit = document.elementFromPoint(window.innerWidth / 2, Math.min(140, window.innerHeight / 3));
        const reading = hit && root.contains(hit) ? anchorChain(hit) : [];
        // fallback: the element at reading height, for when the clicked thing
        // disappears with the refresh (e.g. the batch toolbar hides itself)
        return (el ? anchorChain(el) : []).concat(reading);
    }
    function findAnchor(root, a) {
        if (a.form !== undefined) return Array.from(root.querySelectorAll('form')).find(f => formKey(f) === a.form);
        if (a.key.startsWith('#')) return document.getElementById(a.key.slice(1));
        return root.querySelector('[data-soft-key="' + CSS.escape(a.key) + '"]');
    }
    function restoreAnchor(root, chain) {
        for (const a of chain) {
            const el = findAnchor(root, a);
            // skip anchors that lost their layout (display:none after the swap)
            if (el && root.contains(el) && el.getClientRects().length) {
                window.scrollBy(0, el.getBoundingClientRect().top - a.top);
                return;
            }
        }
    }

    function runScripts(root) {
        root.querySelectorAll('script').forEach(old => {
            if (old.hasAttribute('data-soft-once')) return;
            const type = (old.getAttribute('type') || '').toLowerCase();
            if (type && type !== 'text/javascript' && type !== 'module') return;  // JSON data blocks stay inert
            const s = document.createElement('script');
            for (const attr of old.attributes) s.setAttribute(attr.name, attr.value);
            s.textContent = old.textContent;
            old.replaceWith(s);
        });
    }
    function clearTimers() {
        timers.forEach(t => { clearInterval(t); clearTimeout(t); });
        timers = [];
    }

    function samePage(url) {
        const u = new URL(url, location.href);
        return u.origin === location.origin && u.pathname === location.pathname;
    }
    // The server redirects to e.g. "/?sync_message=..." without the page's
    // sort params: carry the kept ones over.
    function withKeptParams(url) {
        if (!keepParamNames.length) return url;
        const u = new URL(url, location.href);
        const cur = new URLSearchParams(location.search);
        let changed = false;
        keepParamNames.forEach(p => {
            if (cur.has(p) && !u.searchParams.has(p)) { u.searchParams.set(p, cur.get(p)); changed = true; }
        });
        return changed ? u.toString() : url;
    }

    function swap(html, url, submitter, submittedForm) {
        const doc = new DOMParser().parseFromString(html, 'text/html');
        const fresh = doc.querySelector('[data-soft-root]');
        const root = rootEl();
        if (!fresh || !root) { location.assign(url); return; }
        const y = window.scrollY;
        const anchor = captureAnchor(root, submitter);
        const saved = captureFields(root, submittedForm);
        const focus = captureFocus(root);
        const open = captureDetails(root);
        const states = new Map();
        hooks.forEach((h, k) => { try { states.set(k, h.save()); } catch (e) { console.warn('SoftNav save', k, e); } });
        clearTimers();
        root.innerHTML = fresh.innerHTML;
        if (doc.title) document.title = doc.title;
        restoreFields(root, saved);   // before the scripts: they may count checkboxes
        restoreDetails(root, open);
        runScripts(root);
        states.forEach((st, k) => {
            const h = hooks.get(k);
            if (h) try { h.restore(st); } catch (e) { console.warn('SoftNav restore', k, e); }
        });
        history.replaceState(null, '', url);
        window.scrollTo(0, y);
        restoreAnchor(root, anchor);
        restoreFocus(root, focus);
        document.dispatchEvent(new CustomEvent('softnav:swapped', {detail: {url}}));
        const params = new URL(url, location.href).searchParams;
        toast(params.get('message') || params.get('sync_message'));
    }

    async function load(url, opts) {
        opts = opts || {};
        const seq = ++seqStarted;
        let r;
        try { r = await fetch(url, {credentials: 'same-origin'}); }
        catch (err) { toast('ERROR de red: ' + err); return; }
        if (seq < seqApplied) return;
        seqApplied = seq;
        if (!samePage(r.url)) { location.assign(r.url); return; }
        const kept = withKeptParams(r.url);
        if (kept !== r.url) { return load(kept, opts); }
        swap(await r.text(), r.url, opts.submitter || null, opts.form || null);
    }

    document.addEventListener('submit', async (e) => {
        const form = e.target;
        const root = rootEl();
        if (e.defaultPrevented || !root || !root.contains(form)) return;
        const btn = e.submitter;
        const method = btn?.getAttribute('formmethod') ?? form.getAttribute('method') ?? '';
        if (method.toLowerCase() !== 'post') return;
        // getAttribute, never form.action / form.target: an <input name="action">
        // (the tasks batch form has one) shadows those properties.
        const target = btn?.getAttribute('formtarget') ?? form.getAttribute('target');
        if (form.getAttribute('data-soft') === 'off' || target) return;
        const action = new URL(btn?.getAttribute('formaction') ?? form.getAttribute('action') ?? location.href, location.href);
        if (action.origin !== location.origin) return;
        e.preventDefault();
        const fd = btn ? new FormData(form, btn) : new FormData(form);
        const label = btn ? btn.textContent : null;
        if (btn) { btn.disabled = true; btn.textContent = '…'; }
        const slow = setTimeout(() => toast('⏳ procesando… podes seguir trabajando', {ms: 0}), 1500);
        const seq = ++seqStarted;
        let r;
        try {
            r = await fetch(action.toString(), {method: 'POST', body: fd, credentials: 'same-origin'});
        } catch (err) {
            clearTimeout(slow);
            // Not re-submitted: the request may have reached the server, and a
            // second POST could repeat the action.
            toast('ERROR de red: ' + err + ' — revisa antes de reintentar');
            if (btn) { btn.disabled = false; btn.textContent = label; }
            return;
        }
        clearTimeout(slow);
        if (seq < seqApplied) return;
        seqApplied = seq;
        const ctype = r.headers.get('content-type') || '';
        if (!samePage(r.url)) { location.assign(r.url); return; }
        if (!ctype.includes('text/html')) {
            toast(r.ok ? 'Listo' : 'ERROR ' + r.status);
            return load(location.href);
        }
        const kept = withKeptParams(r.url);
        if (kept !== r.url) return load(kept, {submitter: btn, form});
        swap(await r.text(), r.url, btn, form);
    });

    window.SoftNav = {
        /** GET `url` and refresh in place (same page), keeping the user's work. */
        load,
        /** Page JS state that is not a form field: save() before, restore(v) after a swap. */
        keep(key, save, restore) { hooks.set(key, {save, restore}); },
        /** Query params carried over when the server redirects without them. */
        keepParams(names) { keepParamNames = Array.from(new Set(keepParamNames.concat(names))); },
        /** setInterval / setTimeout cleared at the next swap (for re-run scripts). */
        interval(fn, ms) { const t = setInterval(fn, ms); timers.push(t); return t; },
        timeout(fn, ms) { const t = setTimeout(fn, ms); timers.push(t); return t; },
        toast,
        /** URL of the current page with `message` set (and transient params dropped). */
        urlWithMessage(msg, param) {
            const u = new URL(location.href);
            ['message', 'sync_message', 'batch_started'].forEach(p => u.searchParams.delete(p));
            if (msg) u.searchParams.set(param || 'message', msg);
            return u.toString();
        },
    };

    // First load: show the redirect's message as a toast too.
    document.addEventListener('DOMContentLoaded', () => {
        const p = new URLSearchParams(location.search);
        toast(p.get('message') || p.get('sync_message'));
    });
})();

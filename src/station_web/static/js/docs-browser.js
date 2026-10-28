// The documentation browser's list: which pages show, in what groups, with
// what markers.  It reads GET /api/living-docs/list's answer and builds DOM
// nodes; every string from the answer goes in through textContent.
//
// Default view per root: the pages its INDEX.md links, INDEX.md first.  A
// root without INDEX.md (or without index facts, in fallback) shows every
// ordinary page.  "Show everything" adds the unindexed pages, then the
// archive and superpowers sections under their own subheadings.
(function () {
    'use strict';

    const AUDIENCES = ['operator', 'scientist', 'contributor', 'all'];
    const NO_AUDIENCE = 'no audience given';
    const SECTIONS = ['archive', 'superpowers'];
    const DEFAULT_PREFS = { showAll: false, groupBy: 'component' };

    function loadPrefs() {
        const prefs = { ...DEFAULT_PREFS };
        try {
            const all = localStorage.getItem('docs.showAll');
            if (all === '1' || all === '0') prefs.showAll = all === '1';
        } catch (e) { /* storage blocked: keep the default */ }
        try {
            const by = localStorage.getItem('docs.groupBy');
            if (by === 'component' || by === 'audience') prefs.groupBy = by;
        } catch (e) { /* storage blocked: keep the default */ }
        return prefs;
    }

    function savePrefs(prefs) {
        try { localStorage.setItem('docs.showAll', prefs.showAll ? '1' : '0'); } catch (e) { /* ignore */ }
        try { localStorage.setItem('docs.groupBy', prefs.groupBy); } catch (e) { /* ignore */ }
    }

    function isIndex(d) { return d.name === 'INDEX.md'; }

    // INDEX.md first; otherwise keep the listing's order.
    function indexFirst(docs) {
        return docs.filter(isIndex).concat(docs.filter(d => !isIndex(d)));
    }

    // The pages one root shows: { main: [...], sections: [[name, [...]], ...], note }.
    function rootView(root, showAll) {
        const docs = root.documents || [];
        const ordinary = docs.filter(d => !d.section);
        const useIndex = root.has_index === true && docs.some(d => d.indexed);
        let main;
        if (useIndex) {
            const indexed = indexFirst(ordinary.filter(d => d.indexed || isIndex(d)));
            main = showAll ? indexed.concat(ordinary.filter(d => !(d.indexed || isIndex(d)))) : indexed;
        } else {
            main = indexFirst(ordinary);
        }
        const sections = [];
        if (showAll) {
            const names = SECTIONS.concat([...new Set(docs.map(d => d.section).filter(
                s => s && !SECTIONS.includes(s)))]);
            names.forEach(s => {
                const inS = docs.filter(d => d.section === s);
                if (inS.length) sections.push([s, inS]);
            });
        }
        const note = root.has_index === false ? 'no INDEX.md: showing every page' : null;
        return { main, sections, note };
    }

    function visibleRoots(data) {
        return (data.roots || []).filter(r => r.listed !== false);
    }

    // Groups to draw: [{ head, notes, unavailable, main: [[root, doc]], sections: [[name, [[root, doc]]]] }]
    function groups(data, prefs) {
        const roots = visibleRoots(data);
        if (prefs.groupBy !== 'audience') {
            return roots.map(r => {
                const v = r.present ? rootView(r, prefs.showAll) : { main: [], sections: [], note: null };
                return {
                    head: r.name,
                    notes: v.note ? [v.note] : [],
                    unavailable: !r.present,
                    main: v.main.map(d => [r, d]),
                    sections: v.sections.map(([s, ds]) => [s, ds.map(d => [r, d])]),
                };
            });
        }
        const views = roots.filter(r => r.present).map(r => [r, rootView(r, prefs.showAll)]);
        const notes = views.filter(([, v]) => v.note).map(([r, v]) => `${r.name}: ${v.note}`);
        const audienceOf = d => d.audience || NO_AUDIENCE;
        const seen = new Set();
        views.forEach(([, v]) => {
            v.main.forEach(d => seen.add(audienceOf(d)));
            v.sections.forEach(([, ds]) => ds.forEach(d => seen.add(audienceOf(d))));
        });
        const extra = [...seen].filter(a => !AUDIENCES.includes(a) && a !== NO_AUDIENCE);
        const order = AUDIENCES.concat(extra, [NO_AUDIENCE]);
        const out = order.map(a => {
            const main = [];
            const secs = new Map();
            views.forEach(([r, v]) => {
                v.main.filter(d => audienceOf(d) === a).forEach(d => main.push([r, d]));
                v.sections.forEach(([s, ds]) => {
                    ds.filter(d => audienceOf(d) === a).forEach(d => {
                        if (!secs.has(s)) secs.set(s, []);
                        secs.get(s).push([r, d]);
                    });
                });
            });
            return { head: a, notes: [], unavailable: false, main, sections: [...secs] };
        }).filter(g => g.main.length || g.sections.length);
        if (notes.length && out.length) out[0].notes = notes;
        else if (notes.length) out.push({ head: '', notes, unavailable: false, main: [], sections: [] });
        return out;
    }

    function firstEntry(data, prefs) {
        for (const g of groups(data, prefs)) {
            const e = g.main[0] || (g.sections[0] && g.sections[0][1][0]);
            if (e) return { root: e[0].name, doc: e[1].name };
        }
        return null;
    }

    function el(tag, cls, text) {
        const n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined) n.textContent = String(text);
        return n;
    }

    function ageText(d, staleDays) {
        if (d.age_days === null || d.age_days === undefined) return 'age unknown';
        if (d.age_days > staleDays) {
            const day = (d.last_commit || '').slice(0, 10) || 'an unknown date';
            return `not edited since ${day}`;
        }
        return null;
    }

    function entry(root, d, ctx) {
        const li = el('li', 'db-entry');
        li.setAttribute('data-root', root.name);
        li.setAttribute('data-doc', d.name);
        if (ctx.current && ctx.current.root === root.name && ctx.current.doc === d.name) {
            li.className = 'db-entry db-current';
        }
        if (d.canonical) li.appendChild(el('span', 'db-star', '★'));
        const a = el('a', 'db-title', d.title || d.name);
        a.href = '?' + new URLSearchParams({ root: root.name, doc: d.name }).toString();
        a.title = d.name;
        a.addEventListener('click', ev => {
            if (ev && (ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.button === 1)) return;
            if (ev && ev.preventDefault) ev.preventDefault();
            ctx.onOpen(root.name, d.name);
        });
        li.appendChild(a);
        if (ctx.audience) li.appendChild(el('span', 'db-root', root.name));
        if (d.status) {
            const muted = d.status === 'historical' || d.status === 'pointer';
            li.appendChild(el('span', muted ? 'db-status db-muted' : 'db-status', d.status));
        }
        const age = ageText(d, ctx.staleDays);
        if (age) li.appendChild(el('span', 'db-age', age));
        return li;
    }

    function list(pairs, ctx) {
        const ul = el('ul', 'db-list');
        pairs.forEach(([r, d]) => ul.appendChild(entry(r, d, ctx)));
        return ul;
    }

    function render(container, data, prefs, onOpen, current) {
        const ctx = {
            onOpen, current: current || null, audience: prefs.groupBy === 'audience',
            staleDays: typeof data.stale_days === 'number' ? data.stale_days : 90,
        };
        const nodes = [];
        if (data.source === 'fallback') {
            nodes.push(el('div', 'db-banner',
                `Page facts unavailable (${data.reason || 'no reason given'}): `
                + 'showing the fixed folders without ages or index.'));
        }
        const gs = groups(data, prefs);
        gs.forEach(g => {
            const box = el('section', 'db-group');
            if (g.head) {
                box.appendChild(el('h3', 'db-group-head', g.head));
            }
            if (g.unavailable) box.appendChild(el('p', 'db-note', 'folder not present on this station'));
            g.notes.forEach(n => box.appendChild(el('p', 'db-note', n)));
            if (g.main.length) box.appendChild(list(g.main, ctx));
            g.sections.forEach(([s, pairs]) => {
                box.appendChild(el('h4', 'db-section-head', s));
                box.appendChild(list(pairs, ctx));
            });
            nodes.push(box);
        });
        if (!gs.length) nodes.push(el('p', 'db-note', 'No documents listed.'));
        container.replaceChildren(...nodes);
    }

    window.DocsBrowser = { render, firstEntry, loadPrefs, savePrefs, DEFAULT_PREFS };
})();

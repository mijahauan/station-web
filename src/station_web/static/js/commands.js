/**
 * The Commands page ("/commands"): every command the station's programs
 * offer, with the examples people wrote, and one search box over commands
 * and documentation headings.
 *
 * Every server string reaches the page as text content.  The one exception
 * is an example's markdown, which the same renderer docs.html uses (marked)
 * turns into markup; examples come from the docs roots, at the docs
 * browser's own trust level.  Without marked, an example shows as plain text.
 */
const CommandsPage = (() => {
    const MAX_HITS = 50;

    function el(tag, props = {}, kids = []) {
        const n = document.createElement(tag);
        if (props.cls) n.className = props.cls;
        if (props.text !== undefined) n.textContent = String(props.text);
        for (const [k, v] of Object.entries(props.attrs || {})) n.setAttribute(k, v);
        for (const k of kids) n.appendChild(k);
        return n;
    }

    /** "5 s", "12 min", "3 h", "2 d": how long ago, from seconds. */
    function ageText(seconds) {
        if (seconds === null || seconds === undefined || !isFinite(seconds)) return 'unknown';
        const s = Math.max(0, Math.round(seconds));
        if (s < 90) return `${s} s`;
        if (s < 5400) return `${Math.round(s / 60)} min`;
        if (s < 129600) return `${Math.round(s / 3600)} h`;
        return `${Math.round(s / 86400)} d`;
    }

    /** The fragment a command's section carries: its path, spaces as "-". */
    function anchorId(path) {
        return String(path).replace(/\s+/g, '-');
    }

    /** Index entries whose text, context or flags hold ``query`` (case-insensitive). */
    function filter(index, query) {
        const q = String(query || '').trim().toLowerCase();
        if (!q) return [];
        return (index || []).filter(e =>
            String(e.text || '').toLowerCase().includes(q) ||
            String(e.context || '').toLowerCase().includes(q) ||
            String(e.flags || '').toLowerCase().includes(q));
    }

    function renderHits(box, index, query) {
        const hits = filter(index, query);
        if (!String(query || '').trim()) { box.replaceChildren(); return; }
        if (!hits.length) {
            box.replaceChildren(el('div', { cls: 'sp-note', text: 'Nothing matches.' }));
            return;
        }
        const list = el('ul', { cls: 'cmd-hits' });
        for (const h of hits.slice(0, MAX_HITS)) {
            const a = el('a', { text: h.text, attrs: { href: h.href } });
            list.appendChild(el('li', { cls: `cmd-hit cmd-hit-${h.kind}` }, [
                el('span', { cls: 'cmd-kind', text: h.kind === 'command' ? 'command' : 'doc' }),
                document.createTextNode(' '), a,
                el('span', { cls: 'sp-note', text: h.context ? ` - ${h.context}` : '' })]));
        }
        const kids = [list];
        if (hits.length > MAX_HITS) {
            kids.push(el('div', { cls: 'sp-note', text: `${hits.length - MAX_HITS} more; narrow the search.` }));
        }
        box.replaceChildren(...kids);
    }

    function renderFreshness(box, f, programCount) {
        let msg;
        if (!f || !f.available) {
            msg = `No command reference yet: ${(f && f.error) || 'snapshot unavailable'}.`;
        } else if (f.error) {
            msg = `The command reference holds nothing usable: ${f.error}.`;
        } else if (programCount === 0) {
            msg = 'No program is recorded yet. Run `smd admin cli-reference build` ' +
                  'on the station to record them.';
        } else {
            msg = `Reference built ${ageText(f.age_s)} ago` +
                  (f.stale ? ' (stale: the snapshot timer has not refreshed it)' : '') + '.';
        }
        box.replaceChildren(el('span', { cls: 'sp-note', text: msg }));
    }

    function argRow(a) {
        const names = (a.flags && a.flags.length) ? a.flags.join(', ') : (a.name || '');
        const extra = [];
        if (a.required) extra.push('required');
        if (a.choices && a.choices.length) extra.push(`choices: ${a.choices.join(', ')}`);
        if (a.default !== null && a.default !== undefined) extra.push(`default: ${a.default}`);
        if (a.nargs) extra.push(`nargs: ${a.nargs}`);
        return el('tr', {}, [
            el('td', { cls: 'cmd-arg-name', text: names + (a.metavar ? ` ${a.metavar}` : '') }),
            el('td', { text: a.kind || '' }),
            el('td', { text: a.help || '' }),
            el('td', { text: extra.join('; ') })]);
    }

    function argTable(args) {
        const head = el('tr', {}, ['Argument', 'Kind', 'Help', 'Notes'].map(t => el('th', { text: t })));
        return el('table', { cls: 'sp-table cmd-args' }, [
            el('thead', {}, [head]), el('tbody', {}, args.map(argRow))]);
    }

    function exampleBlock(md, markdown) {
        const box = el('div', { cls: 'cmd-example' });
        if (typeof markdown === 'function') {
            box.innerHTML = markdown(md);
        } else {
            box.appendChild(el('pre', { text: md }));
        }
        return box;
    }

    function commandBlock(c, markdown) {
        const kids = [el('h4', { cls: 'cmd-path', text: c.path })];
        if (c.help) kids.push(el('div', { cls: 'cmd-help', text: c.help }));
        if (c.description && c.description !== c.help) {
            kids.push(el('div', { cls: 'sp-note', text: c.description }));
        }
        if (c.arguments && c.arguments.length) kids.push(argTable(c.arguments));
        if (c.example) kids.push(exampleBlock(c.example, markdown));
        return el('div', { cls: 'cmd', attrs: { id: anchorId(c.path) } }, kids);
    }

    function programCard(p, markdown) {
        const title = [el('span', { cls: 'card-title', text: p.program })];
        if (p.version) title.push(el('span', { cls: 'sp-note', text: ` ${p.version}` }));
        if (p.component) title.push(el('span', { cls: 'sp-note', text: ` (${p.component})` }));
        if (p.stale) title.push(el('span', { cls: 'badge st-warn cmd-stale', text: 'stale' }));
        const kids = [el('div', { cls: 'card-header' }, [el('div', {}, title)])];
        if (p.aliases && p.aliases.length) {
            kids.push(el('div', { cls: 'sp-note cmd-aliases', text: `also: ${p.aliases.join(', ')}` }));
        }
        if (p.error) {
            kids.push(el('div', { cls: 'sp-note cmd-unavailable',
                                  text: `Reference unavailable: ${p.error}` }));
        } else if (!p.commands.length) {
            kids.push(el('div', { cls: 'sp-note cmd-unavailable',
                                  text: 'Reference unavailable: the walk found no commands.' }));
        }
        for (const c of p.commands) kids.push(commandBlock(c, markdown));
        return el('div', { cls: 'card cmd-program', attrs: { id: `program-${anchorId(p.program)}` } }, kids);
    }

    function listCard(title, items) {
        return el('div', { cls: 'card cmd-closing' }, [
            el('div', { cls: 'card-header' }, [el('div', { cls: 'card-title', text: title })]),
            items.length ? el('ul', {}, items.map(t => el('li', { text: t })))
                         : el('div', { cls: 'sp-note', text: 'None.' })]);
    }

    /** Draw the reference into ``box``; ``opts.markdown`` renders an example. */
    function renderReference(box, ref, opts = {}) {
        const markdown = opts.markdown || null;
        const kids = (ref.programs || []).map(p => programCard(p, markdown));
        if ((ref.programs || []).length) {
            kids.push(listCard('Commands without an example', ref.missing_examples || []));
            kids.push(listCard('Example sections that match no command',
                (ref.unmatched_examples || []).map(u => `${u.program}: ${u.heading}`)));
        }
        box.replaceChildren(...kids);
    }

    async function getJSON(url) {
        const r = await fetch(url);
        if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
        return r.json();
    }

    async function init() {
        const err = document.getElementById('error-container');
        const fail = m => { err.textContent = m; err.style.display = m ? 'block' : 'none'; };
        let index = [];
        try {
            const [ref, idx] = await Promise.all([
                getJSON('/commands/reference'), getJSON('/commands/search-index')]);
            index = idx;
            renderFreshness(document.getElementById('cmd-fresh'), ref.freshness,
                            (ref.programs || []).length);
            renderReference(document.getElementById('cmd-programs'), ref, {
                markdown: (typeof marked !== 'undefined') ? (md => marked.parse(md)) : null });
            const want = decodeURIComponent((window.location.hash || '').slice(1));
            const target = want && document.getElementById(want);
            if (target && target.scrollIntoView) target.scrollIntoView();
        } catch (e) {
            fail(`Could not load the command reference: ${e.message}`);
        }
        const input = document.getElementById('cmd-search');
        const results = document.getElementById('cmd-results');
        input.addEventListener('input', () => renderHits(results, index, input.value));
    }

    if (typeof document !== 'undefined' && document.body && document.body.getAttribute &&
        document.body.getAttribute('data-page') === 'commands') {
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
        else init();
    }

    return { filter, renderHits, renderFreshness, renderReference, anchorId, ageText };
})();
if (typeof window !== 'undefined') window.CommandsPage = CommandsPage;

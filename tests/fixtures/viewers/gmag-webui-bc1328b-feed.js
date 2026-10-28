// Fixture: js/feed.js from https://github.com/HamSCI/gmag_webui (gmag-webui) at commit bc1328b (sigmond v3.82 pin); verbatim below this line.
/**
 * The feed this page's own server proxies at /ws (see ts/magFeed.ts).  Same
 * origin, so it works over the LAN, a relay or RAC without the operator
 * knowing which -- and without typing an IPv6 literal, which the URL field
 * rejects unless it is bracketed.
 * @param {{ protocol: string, host: string }} loc the page's location
 * @returns {string}
 */
export function sameOriginFeed(loc) {
    const scheme = loc.protocol === "https:" ? "wss://" : "ws://";
    return scheme + loc.host + "/ws";
}

/**
 * Points every WebSocket source with a BLANK url at `url`.  A URL someone
 * chose is left alone.
 *
 * An operator who has opened the page before already has a saved source with
 * url:"" in localStorage, and defaulting new sources alone would never reach
 * them -- they would keep seeing "disconnected" after the fix.
 * @param {any[]} sources
 * @param {string} url
 */
export function fillBlankFeedUrls(sources, url) {
    for (const src of sources) {
        if (src && src.type === "websocket"
            && src.websocket && !src.websocket.url) {
            src.websocket.url = url;
        }
    }
}

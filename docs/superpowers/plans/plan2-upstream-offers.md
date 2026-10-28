# Plan 2: two upstream patches on offer

Written 2026-10-28.  Michael may send either patch or neither.  Plan 2 opens
no pull request and contacts no one.

Both viewers open a WebSocket at the server root.  station-web's front door
serves each viewer under a prefix (`/viewer/<name>/`) and works around the
problem today by rewriting one line as the file passes through.  If upstream
takes the patch, the rewrite becomes unnecessary and the front door logs no
WARNING when upstream changes the line.

## 1. ka9q-web, `html/radio.js` (against 2c6303f)

Upstream: wa2n-code/ka9q-web.

Reason: the page builds its WebSocket address from `window.location.host`
alone, so a page served under any path prefix connects to the server's root
instead of its own path.  The patch appends the page's own directory, which
changes nothing when the page sits at `/`.

```diff
--- a/html/radio.js
+++ b/html/radio.js
@@ -2337,7 +2337,7 @@
                   if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;
                   isAttemptingConnect = true;
                   setPopupRetryingNow();
-                  ws = new WebSocket((window.location.protocol == 'https:' ? 'wss://' : 'ws://') + window.location.host);
+                  ws = new WebSocket((window.location.protocol == 'https:' ? 'wss://' : 'ws://') + window.location.host + window.location.pathname.replace(/[^/]*$/, ''));
                   ws.onmessage = on_ws_message;
                   ws.onopen = function(evt) {
                     reconnectDelayMs = 1000;
```

Check on the server side: ka9q-web's own WebSocket handler must accept the
request at the prefixed path.  Behind a reverse proxy that strips the prefix
(station-web does), it does.  Served directly at `/`, the new address equals
the old one.

## 2. gmag-webui, `js/feed.js` (against bc1328b)

Upstream: HamSCI/gmag_webui.  The Frissell group maintains it under its own
AI-governance rules, so Michael may want to ask them before opening anything.

Reason: `sameOriginFeed` returns `scheme + loc.host + "/ws"`, so a page
served under a prefix reaches the front server's `/ws` instead of its own.
The patch builds the address from the page's own directory, which gives
`/ws` again when the page sits at `/`.

```diff
--- a/js/feed.js
+++ b/js/feed.js
@@ -8,7 +8,7 @@
  */
 export function sameOriginFeed(loc) {
     const scheme = loc.protocol === "https:" ? "wss://" : "ws://";
-    return scheme + loc.host + "/ws";
+    return scheme + loc.host + (loc.pathname || "/").replace(/[^/]*$/, "") + "ws";
 }
 
 /**
```

Optional second hunk, `index.html`.  The page links `/site.webmanifest` by
an absolute path, so under a prefix the browser asks the front server for it
and gets a 404.  The effect is cosmetic: the browser finds no web-app
manifest.  A relative link fixes it.

```diff
--- a/index.html
+++ b/index.html
@@ -7,7 +7,7 @@
         <link rel="apple-touch-icon" sizes="180x180" href="images/apple-touch-icon.png">
         <link rel="icon" type="image/png" sizes="32x32" href="images/favicon-32x32.png">
         <link rel="icon" type="image/png" sizes="16x16" href="images/favicon-16x16.png">
-        <link rel="manifest" href="/site.webmanifest">
+        <link rel="manifest" href="site.webmanifest">
         <!--Third-Party Libraries-->
         <link href="vendor/fontawesome-free-7.1.0-web/css/fontawesome.css" rel="stylesheet" />
         <link href="vendor/fontawesome-free-7.1.0-web/css/brands.css" rel="stylesheet" />
```

## Where the rewrite lives today

`REWRITES` in `src/station_web/services/viewer_proxy.py` holds the same two
substitutions, pinned to these commits.

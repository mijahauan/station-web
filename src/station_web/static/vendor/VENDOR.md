# Vendored front-end libraries

| file | version | source | sha256 |
|---|---|---|---|
| plotly-2.27.0.min.js | 2.27.0 | https://cdn.plot.ly/plotly-2.27.0.min.js | 7f4930eba8f8541dbec28dca5bd5f787f8eef1cde0369ac9657b70bed230b3e0 |
| topojson/world_110m.json | plotly 2.27 geo asset | https://cdn.plot.ly/world_110m.json | d75915eaa31c870df6b972c9e5bb86910197825f33dcfef740f3b2f68cffe843 |
| marked.min.js | 18.0.11 | https://cdn.jsdelivr.net/npm/marked@18.0.11/lib/marked.umd.js | 69451c8541c9c1e7a4bf3ffc6f73c4d89633de92bfbe3e484dfe182ef8091f88 |

Fetched 2026-09-06T13:43Z. Note: marked no longer publishes a top-level
`marked.min.js` browser bundle as of this version; the UMD build at
`lib/marked.umd.js` is vendored under the historical filename `marked.min.js`
for drop-in compatibility (it exposes the same global `marked`).
Update by re-running the commands in the plan (Task 13) and refreshing this table.

`topojson/world_110m.json` fetched 2026-10-18T18:10Z: the coastlines and
countries plotly's scattergeo draws.  Without it plotly fetches the file from
cdn.plot.ly at run time; the Propagation page's path map points
`topojsonURL` at `/static/vendor/topojson/` instead.

# Vendored front-end libraries

| file | version | source | sha256 |
|---|---|---|---|
| plotly-2.27.0.min.js | 2.27.0 | https://cdn.plot.ly/plotly-2.27.0.min.js | 7f4930eba8f8541dbec28dca5bd5f787f8eef1cde0369ac9657b70bed230b3e0 |
| marked.min.js | 18.0.11 | https://cdn.jsdelivr.net/npm/marked@18.0.11/lib/marked.umd.js | 69451c8541c9c1e7a4bf3ffc6f73c4d89633de92bfbe3e484dfe182ef8091f88 |

Fetched 2026-09-06T13:43Z. Note: marked no longer publishes a top-level
`marked.min.js` browser bundle as of this version; the UMD build at
`lib/marked.umd.js` is vendored under the historical filename `marked.min.js`
for drop-in compatibility (it exposes the same global `marked`).
Update by re-running the commands in the plan (Task 13) and refreshing this table.

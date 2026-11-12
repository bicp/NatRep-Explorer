# Local patches to sql.js-httpvfs 0.8.12

`sqlite.worker.js` is the published 0.8.12 build with two small edits (search for `maxReadSpeed:e.maxReadSpeed` and
the fallback before `doXHR failed (bug)!`):

1. **`maxReadSpeed` from config.json.** The library's lazy file supports a `maxReadSpeed` option (bytes per request
   when reading ahead) but never passed it through from the database config. `scripts/build_db.py` stores each 64 KB
   page as its own file and sets `maxReadSpeed` to the page size, which turns read-ahead off – a request can't span
   several files.
2. **Fetch a missing page on its own instead of throwing.** Read-ahead may request a range that runs past the end of
   one chunk file; the server then returns less than asked and the page that was actually needed is missing, which
   threw `doXHR failed (bug)!`. The patch fetches that page with its own request.

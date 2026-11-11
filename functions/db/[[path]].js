// Serve HTTP Range requests for the database chunks in /db/.
//
// The browser reads the SQLite database a few KB at a time with "Range: bytes=a-b" requests, but Cloudflare Pages
// static assets ignore the Range header and always return the whole file. This function loads the chunk (1 MB, see
// CHUNK_BYTES in scripts/build_db.py) from the static assets and returns only the requested bytes (206 Partial Content).
// Everything else passes straight through.

export async function onRequestGet({ request, env }) {
  const asset = await env.ASSETS.fetch(request.url);
  const range = /^bytes=(\d+)-(\d*)$/.exec(request.headers.get("Range") || "");
  if (!range || !asset.ok) return withRanges(asset);

  const data = new Uint8Array(await asset.arrayBuffer());
  const size = data.length;
  const start = Number(range[1]);
  const end = Math.min(range[2] ? Number(range[2]) : size - 1, size - 1);
  if (start > end) return new Response(null, { status: 416, headers: { "Content-Range": `bytes */${size}` } });

  return new Response(data.subarray(start, end + 1), {
    status: 206,
    headers: {
      "Content-Type": asset.headers.get("Content-Type") || "application/octet-stream",
      "Content-Range": `bytes ${start}-${end}/${size}`,
      "Accept-Ranges": "bytes",
      "Cache-Control": "no-cache",
    },
  });
}

export async function onRequestHead({ request, env }) {
  return withRanges(await env.ASSETS.fetch(request.url, { method: "HEAD" }));
}

function withRanges(res) {
  const out = new Response(res.body, res);
  out.headers.set("Accept-Ranges", "bytes");
  return out;
}

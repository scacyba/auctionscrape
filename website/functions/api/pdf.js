const R2_BINDING = 'AUCTION_BUCKET';
const PDF_KEY_PATTERN = /^bit\/okayama\/pdf\/\d{4}\/\d{2}\/\d{2}\/[A-Za-z0-9._-]+\.pdf$/;

function jsonResponse(body, init = {}) {
  return new Response(JSON.stringify(body), {
    ...init,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
      ...init.headers,
    },
  });
}

function downloadName(key) {
  const parts = key.split('/');
  return parts.at(-1) || 'auction.pdf';
}

export async function onRequestGet({ request, env }) {
  const bucket = env[R2_BINDING];
  if (!bucket) {
    return jsonResponse({ error: `R2 binding ${R2_BINDING} is not configured.` }, { status: 500 });
  }

  const url = new URL(request.url);
  const key = url.searchParams.get('key') || '';
  if (!PDF_KEY_PATTERN.test(key)) {
    return jsonResponse({ error: 'Invalid PDF key.' }, { status: 400 });
  }

  const object = await bucket.get(key);
  if (!object) {
    return jsonResponse({ error: 'PDF was not found.' }, { status: 404 });
  }

  return new Response(object.body, {
    headers: {
      'content-type': object.httpMetadata?.contentType || 'application/pdf',
      'content-length': String(object.size),
      'content-disposition': `attachment; filename="${downloadName(key)}"`,
      'cache-control': 'public, max-age=86400',
      etag: object.httpEtag,
    },
  });
}

const R2_BINDING = 'AUCTION_BUCKET';
const ITEM_DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const PDF_KEY_PATTERN = /^bit\/okayama\/pdf\/\d{4}\/\d{2}\/\d{2}\/[A-Za-z0-9._-]+\.pdf$/;

function jsonResponse(body, init = {}) {
  return new Response(JSON.stringify(body), {
    ...init,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      ...init.headers,
    },
  });
}

function getBucket(env) {
  return env[R2_BINDING];
}

function itemKeyFromDate(date) {
  const [year, month, day] = date.split('-');
  return `bit/okayama/html/${year}/${month}/${day}/items.json`;
}

function downloadName(key) {
  const parts = key.split('/');
  return parts.at(-1) || 'auction.pdf';
}

async function handleItems(request, env) {
  const bucket = getBucket(env);
  if (!bucket) {
    return jsonResponse({ error: `R2 binding ${R2_BINDING} is not configured.` }, { status: 500 });
  }

  const url = new URL(request.url);
  const date = url.searchParams.get('date') || '';
  if (!ITEM_DATE_PATTERN.test(date)) {
    return jsonResponse({ error: 'date must be YYYY-MM-DD.' }, { status: 400 });
  }

  const key = itemKeyFromDate(date);
  const object = await bucket.get(key);
  if (!object) {
    return jsonResponse(
      { date, items: [], key, error: 'items.json was not found.' },
      { status: 404, headers: { 'cache-control': 'no-store' } },
    );
  }

  return new Response(object.body, {
    headers: {
      'content-type': object.httpMetadata?.contentType || 'application/json; charset=utf-8',
      'cache-control': 'public, max-age=300',
      etag: object.httpEtag,
    },
  });
}

async function handlePdf(request, env) {
  const bucket = getBucket(env);
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

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === 'GET' && url.pathname === '/api/items') {
      return handleItems(request, env);
    }

    if (request.method === 'GET' && url.pathname === '/api/pdf') {
      return handlePdf(request, env);
    }

    return env.ASSETS.fetch(request);
  },
};

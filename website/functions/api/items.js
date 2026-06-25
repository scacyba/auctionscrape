const R2_BINDING = 'AUCTION_BUCKET';
const ITEM_KEY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

function jsonResponse(body, init = {}) {
  return new Response(JSON.stringify(body), {
    ...init,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': init.status === 404 ? 'no-store' : 'public, max-age=300',
      ...init.headers,
    },
  });
}

export async function onRequestGet({ request, env }) {
  const bucket = env[R2_BINDING];
  if (!bucket) {
    return jsonResponse({ error: `R2 binding ${R2_BINDING} is not configured.` }, { status: 500 });
  }

  const url = new URL(request.url);
  const date = url.searchParams.get('date') || '';
  if (!ITEM_KEY_PATTERN.test(date)) {
    return jsonResponse({ error: 'date must be YYYY-MM-DD.' }, { status: 400 });
  }

  const [year, month, day] = date.split('-');
  const key = `bit/okayama/html/${year}/${month}/${day}/items.json`;
  const object = await bucket.get(key);
  if (!object) {
    return jsonResponse({ date, items: [], key, error: 'items.json was not found.' }, { status: 404 });
  }

  return new Response(object.body, {
    headers: {
      'content-type': object.httpMetadata?.contentType || 'application/json; charset=utf-8',
      'cache-control': 'public, max-age=300',
      etag: object.httpEtag,
    },
  });
}

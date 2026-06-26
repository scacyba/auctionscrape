const R2_BINDING = 'AUCTION_BUCKET';
const ITEM_KEY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const ITEMS_PREFIX = 'bit/okayama/html/';

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

function itemKeyFromDate(date) {
  const [year, month, day] = date.split('-');
  return `${ITEMS_PREFIX}${year}/${month}/${day}/items.json`;
}

function dateFromItemsKey(key) {
  const match = key.match(/^bit\/okayama\/html\/(\d{4})\/(\d{2})\/(\d{2})\/items\.json$/);
  return match ? `${match[1]}-${match[2]}-${match[3]}` : null;
}

async function latestItemsObject(bucket) {
  let cursor;
  let latest = null;
  do {
    const listed = await bucket.list({ prefix: ITEMS_PREFIX, cursor });
    for (const object of listed.objects || []) {
      const date = dateFromItemsKey(object.key);
      if (date && (!latest || date > latest.date)) {
        latest = { date, key: object.key };
      }
    }
    cursor = listed.truncated ? listed.cursor : undefined;
  } while (cursor);

  if (!latest) return null;
  const object = await bucket.get(latest.key);
  return object ? { ...latest, object } : null;
}

async function itemsResponse(object, date, extra = {}) {
  const payload = await object.json();
  return jsonResponse({ ...payload, date: payload.date || date, ...extra }, {
    headers: {
      'cache-control': 'public, max-age=300',
      etag: object.httpEtag,
    },
  });
}

export async function onRequestGet({ request, env }) {
  const bucket = env[R2_BINDING];
  if (!bucket) {
    return jsonResponse({ error: `R2 binding ${R2_BINDING} is not configured.` }, { status: 500 });
  }

  const url = new URL(request.url);
  const date = url.searchParams.get('date') || 'latest';
  if (date === 'latest') {
    const latest = await latestItemsObject(bucket);
    if (!latest) {
      return jsonResponse({ date, items: [], error: 'items.json was not found.' }, { status: 404 });
    }
    return itemsResponse(latest.object, latest.date, { key: latest.key, latest: true });
  }
  if (!ITEM_KEY_PATTERN.test(date)) {
    return jsonResponse({ error: 'date must be YYYY-MM-DD or latest.' }, { status: 400 });
  }

  const key = itemKeyFromDate(date);
  const object = await bucket.get(key);
  if (!object) {
    if (url.searchParams.get('fallbackLatest') === '1') {
      const latest = await latestItemsObject(bucket);
      if (latest) {
        return itemsResponse(latest.object, latest.date, {
          key: latest.key,
          requestedDate: date,
          fallbackLatest: true,
        });
      }
    }
    return jsonResponse({ date, items: [], key, error: 'items.json was not found.' }, { status: 404 });
  }

  return itemsResponse(object, date, { key });
}

const NOMINATIM_ENDPOINT = 'https://nominatim.openstreetmap.org/search';
const MAX_LOCATION_LENGTH = 200;

function jsonResponse(body, init = {}) {
  return new Response(JSON.stringify(body), {
    ...init,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      ...init.headers,
    },
  });
}

function toHalfWidth(value) {
  return String(value).replace(/[！-～]/g, (char) => String.fromCharCode(char.charCodeAt(0) - 0xfee0));
}

function geocodeQueries(location) {
  const normalized = toHalfWidth(location)
    .replace(/\s+/g, ' ')
    .replace(/[、,].*$/, '')
    .trim();
  const withoutLot = normalized
    .replace(/字[^\s]+/g, '')
    .replace(/[0-9-]+番地?[0-9-号-]*/g, '')
    .replace(/[0-9-]+地[0-9-]*/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  const municipality = normalized.match(/^(.+?[市区町村])/u)?.[1];
  return [...new Set([
    `岡山県 ${normalized}`,
    normalized,
    withoutLot ? `岡山県 ${withoutLot}` : '',
    withoutLot,
    municipality ? `岡山県 ${municipality}` : '',
  ].filter(Boolean))];
}

async function fetchNominatim(query) {
  const url = new URL(NOMINATIM_ENDPOINT);
  url.searchParams.set('format', 'jsonv2');
  url.searchParams.set('limit', '1');
  url.searchParams.set('countrycodes', 'jp');
  url.searchParams.set('accept-language', 'ja');
  url.searchParams.set('q', query);
  const response = await fetch(url, {
    headers: {
      accept: 'application/json',
      'user-agent': 'auctionscrape/1.0 (+https://github.com/) Cloudflare Worker geocoder',
    },
  });
  if (!response.ok) return null;
  const results = await response.json();
  if (!Array.isArray(results) || results.length === 0) return null;
  const [result] = results;
  const lat = Number(result.lat);
  const lng = Number(result.lon);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  return { lat, lng, query, displayName: result.display_name || '' };
}

export async function onRequestGet({ request }) {
  const url = new URL(request.url);
  const location = (url.searchParams.get('location') || '').trim();
  if (!location) {
    return jsonResponse({ error: 'location is required.' }, { status: 400 });
  }
  if (location.length > MAX_LOCATION_LENGTH) {
    return jsonResponse({ error: 'location is too long.' }, { status: 400 });
  }

  for (const query of geocodeQueries(location)) {
    const point = await fetchNominatim(query);
    if (point) {
      return jsonResponse(point, { headers: { 'cache-control': 'public, max-age=604800' } });
    }
  }

  return jsonResponse({ error: 'location could not be geocoded.' }, { status: 404, headers: { 'cache-control': 'public, max-age=86400' } });
}

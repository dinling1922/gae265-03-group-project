export type Coords = { latitude: number; longitude: number };

async function search(q: string): Promise<Coords | null> {
  const url = `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&accept-language=zh-TW&q=${encodeURIComponent(q)}`;
  const res = await fetch(url);
  if (!res.ok) return null;
  const [hit] = (await res.json()) as { lat: string; lon: string }[];
  return hit ? { latitude: Number(hit.lat), longitude: Number(hit.lon) } : null;
}

/** 地址或地名轉座標（OSM Nominatim，免金鑰）；查不到或網路失敗回 null。
 *  先照原文查，查不到再加上城市（例如只寫店名時）。 */
export async function geocode(location: string, city = ""): Promise<Coords | null> {
  if (!location) return null;
  try {
    return (await search(location)) ?? (city ? await search(`${location} ${city}`) : null);
  } catch {
    return null;
  }
}

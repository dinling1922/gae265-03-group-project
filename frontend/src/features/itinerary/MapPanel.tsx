import { Button } from "@base-ui/react/button";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useMemo, useRef } from "react";
import { MapContainer, Marker, Polyline, TileLayer, useMap } from "react-leaflet";
import type { MapScope, Stop } from "./mapView";
import { formatDay, formatHour } from "./tripRules";

const chip =
  "rounded-chip border bg-white px-3.5 py-1 t-caption-bold focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-leaf";

/** 編號 pin：選取的是黃底放大。divIcon 的內容是字串，class 由 Tailwind 掃描原始碼取得。 */
function pinIcon(n: string, selected: boolean): L.DivIcon {
  const base =
    "grid size-[30px] place-items-center rounded-full border-[3px] border-white font-bold text-[13px] shadow-[0_2px_6px_rgba(0,0,0,.3)]";
  const tone = selected ? "scale-125 bg-sun text-heading" : "bg-brand text-white";
  return L.divIcon({
    className: "",
    html: `<div data-testid="map-pin" data-n="${n}" data-selected="${selected}" class="${base} ${tone}">${n}</div>`,
    iconSize: [30, 30],
    iconAnchor: [15, 15],
  });
}

/** 容器大小改變（捲動出現捲軸、欄位換版面）後重新計算，不然會出現灰色區塊。 */
function KeepSized() {
  const map = useMap();
  useEffect(() => {
    const observer = new ResizeObserver(() => map.invalidateSize());
    observer.observe(map.getContainer());
    return () => observer.disconnect();
  }, [map]);
  return null;
}

/** 看的範圍（哪一天、單日或整趟）或站的位置變了就縮放到全部的站；只是換選取的不動。 */
function FitStops({ points, viewKey }: { points: [number, number][]; viewKey: string }) {
  const map = useMap();
  const signature = `${viewKey}|${points.map((p) => p.join(",")).join(";")}`;
  // biome-ignore lint/correctness/useExhaustiveDependencies: 只在 signature 變時重新縮放，points 每次渲染都是新陣列
  useEffect(() => {
    if (points.length > 0) map.fitBounds(points, { padding: [40, 40], maxZoom: 14 });
  }, [map, signature]);
  return null;
}

function StopMarker({
  stop,
  selected,
  onSelect,
}: {
  stop: Stop;
  selected: boolean;
  onSelect: (stop: Stop) => void;
}) {
  const ref = useRef<L.Marker>(null);
  const { activity } = stop;
  // 同一個 icon 物件：每次渲染都換新的，Leaflet 會重建 pin 的 DOM，點擊會掉
  const icon = useMemo(() => pinIcon(stop.n, selected), [stop.n, selected]);
  const { title, date, hour, location } = activity;
  // 選取的站打開資訊卡（來自清單、時間軸或 pin 的選取都一樣）。不用 react-leaflet 的 <Popup>：
  // 它在每次渲染都會把資訊卡關掉再開。內容用 textContent 組，標題與地點是使用者或 agent 給的，不當 HTML。
  useEffect(() => {
    const marker = ref.current;
    if (!selected || !marker) return;
    const content = document.createElement("div");
    const heading = document.createElement("b");
    heading.textContent = title;
    content.append(heading, document.createElement("br"));
    content.append(`${formatDay(date)} ${formatHour(hour)}${location ? `・${location}` : ""}`);
    marker.bindPopup(content, { offset: [0, -12] }).openPopup();
    return () => {
      marker.closePopup();
      marker.unbindPopup();
    };
  }, [selected, title, date, hour, location]);
  return (
    <Marker
      ref={ref}
      position={[activity.latitude as number, activity.longitude as number]}
      icon={icon}
      // pin 不進 tab 順序：鍵盤與讀屏用右邊的清單；可聚焦的 pin 按下去時瀏覽器會捲動畫面，點擊就落空
      keyboard={false}
      eventHandlers={{ click: () => onSelect(stop) }}
    />
  );
}

/**
 * 右欄日程地圖（ADR-0031）：「全部天數／第 N 天」切換、地圖、編號 pin 與單日路線、
 * 「這一天的順序」清單。選取的活動與時間軸、清單共用（由頁面持有）。
 */
export function MapPanel({
  dates,
  scope,
  day,
  stops,
  unmapped,
  selectedId,
  onShowTrip,
  onShowDay,
  onSelect,
}: {
  dates: string[];
  scope: MapScope;
  day: string;
  stops: Stop[];
  unmapped: number;
  selectedId: string | null;
  onShowTrip: () => void;
  onShowDay: (date: string) => void;
  onSelect: (stop: Stop) => void;
}) {
  const points = stops.map((s): [number, number] => [
    s.activity.latitude as number,
    s.activity.longitude as number,
  ]);
  const active = (on: boolean) =>
    on ? "border-brand bg-leaf-soft text-brand" : "border-line text-muted";

  return (
    <div data-testid="map-panel" className="flex flex-col gap-3">
      <div>
        <p className="m-0 t-label text-muted">地圖</p>
        <h2 className="m-0 t-section-title text-heading">日程地圖</h2>
        <p className="m-0 t-caption text-muted">一站一站照順序走</p>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <Button
          aria-pressed={scope === "trip"}
          onClick={onShowTrip}
          className={`${chip} ${active(scope === "trip")}`}
        >
          全部天數
        </Button>
        {dates.map((d, i) => (
          <Button
            key={d}
            aria-pressed={scope === "day" && d === day}
            onClick={() => onShowDay(d)}
            className={`${chip} ${active(scope === "day" && d === day)}`}
          >
            第 {i + 1} 天
          </Button>
        ))}
      </div>
      <MapContainer
        center={[25.04, 121.56]}
        zoom={3}
        scrollWheelZoom={false}
        // 容器不進 tab 順序（鍵盤用右邊的清單）：可聚焦的容器被按下時，瀏覽器會把它捲進可視範圍，點擊就落空
        keyboard={false}
        className="z-0 h-[280px] rounded-tile border border-line md:h-[360px]"
      >
        <TileLayer
          attribution="© OpenStreetMap contributors"
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          maxZoom={19}
        />
        <KeepSized />
        <FitStops points={points} viewKey={`${scope}-${day}`} />
        {scope === "day" && points.length > 1 && (
          <Polyline
            positions={points}
            pathOptions={{ color: "#285442", weight: 3, opacity: 0.7, dashArray: "6 6" }}
          />
        )}
        {stops.map((s) => (
          <StopMarker
            key={s.activity.id}
            stop={s}
            selected={s.activity.id === selectedId}
            onSelect={onSelect}
          />
        ))}
      </MapContainer>
      <p className="m-0 t-caption text-muted" data-testid="map-stats">
        {stops.length} 個地點
      </p>
      <h3 className="m-0 t-label text-heading">
        {scope === "day" ? "這一天的順序" : "整趟的順序"}
      </h3>
      <ol className="m-0 flex list-none flex-col gap-2 p-0" data-testid="stop-list">
        {stops.map((s) => {
          const selected = s.activity.id === selectedId;
          return (
            <li key={s.activity.id}>
              <Button
                onClick={() => onSelect(s)}
                aria-pressed={selected}
                data-testid="stop-item"
                data-selected={selected}
                className={`flex w-full items-center gap-3 rounded-btn border px-3 py-2.5 text-left hover:border-brand focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-leaf ${
                  selected ? "border-brand bg-leaf-soft" : "border-line bg-white"
                }`}
              >
                <span className="grid h-7 min-w-7 flex-none place-items-center rounded-full bg-brand px-1.5 text-[13px] font-bold text-white">
                  {s.n}
                </span>
                <span className="min-w-0">
                  <b className="block t-caption-bold text-heading">{s.activity.title}</b>
                  <span className="t-micro text-muted">
                    {scope === "trip" && `第 ${s.dayIndex + 1} 天・`}
                    {formatHour(s.activity.hour)}
                    {s.activity.location && `・${s.activity.location}`}
                  </span>
                </span>
              </Button>
            </li>
          );
        })}
      </ol>
      {unmapped > 0 && (
        <p className="m-0 t-caption text-muted" data-testid="map-unmapped">
          另有 {unmapped} 個活動沒有座標，不在地圖上
        </p>
      )}
    </div>
  );
}

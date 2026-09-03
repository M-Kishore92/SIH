import { useState, useEffect, useRef, useCallback } from 'react';
import {
  MapContainer, TileLayer, GeoJSON, Polygon, useMap,
} from 'react-leaflet';
import L from 'leaflet';
import type { GeoJsonObject, Feature, Polygon as GeoPolygon } from 'geojson';
import {
  Map, Satellite, Zap, AlertTriangle, CheckCircle,
  XCircle, Activity, Play, Pause, StopCircle,
  RefreshCw, Shield, ChevronRight, Radio,
} from 'lucide-react';
import './App.css';

// ─── Types ────────────────────────────────────────────────────────────────────
interface Parcel {
  id: string;
  survey_number: string;
  village: string;
  district: string;
  recorded_area: number;
  surveyed_area: number | null;
  owner_reference: string;
  status: string;
  risk_level: string;
  boundary_geojson: string;
}

interface CompareResult {
  existing_area: number;
  surveyed_area: number;
  area_difference: number;
  area_difference_percentage: number;
  overlap_area: number;
  anomaly_detected: boolean;
  anomaly_type: string;
  risk_level: string;
}

interface GNSSTelemetry {
  satellites: number;
  accuracy: number;
  fix_type: string;
  altitude: number;
  lat: number;
  lon: number;
}

type SurveyState = 'idle' | 'running' | 'paused' | 'complete';

// ─── Constants ────────────────────────────────────────────────────────────────
const API = 'http://localhost:8000';

const RISK_COLORS: Record<string, string> = {
  GREEN:  '#22c55e',
  YELLOW: '#eab308',
  RED:    '#ef4444',
};

const RISK_BG: Record<string, string> = {
  GREEN:  'bg-emerald-900/40 text-emerald-300 border-emerald-700',
  YELLOW: 'bg-yellow-900/40 text-yellow-300 border-yellow-700',
  RED:    'bg-red-900/40 text-red-300 border-red-700',
};

// Fix missing default Leaflet marker icons in Vite builds
delete (L.Icon.Default.prototype as Record<string, unknown>)._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
  iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
  shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
});

// ─── Helpers ──────────────────────────────────────────────────────────────────
/** Extract coordinate ring from a parcel's GeoJSON string */
function extractCoords(geojson: string): [number, number][] {
  try {
    const parsed = JSON.parse(geojson) as Feature<GeoPolygon>;
    const ring = parsed.geometry.coordinates[0] as [number, number][];
    // GeoJSON is [lon, lat]; Leaflet wants [lat, lon]
    return ring.map(([lon, lat]) => [lat, lon]);
  } catch {
    return [];
  }
}

/** Add ±noise (degrees) to a coordinate */
function jitter(val: number, amount: number) {
  return val + (Math.random() - 0.5) * 2 * amount;
}

/** Build a "surveyed" ring from the original with realistic drift (~2m ≈ 0.00002°) */
function buildSurveyedRing(original: [number, number][]): [number, number][] {
  const noiseBase = 0.00002;
  return original.map(([lat, lon], i) => {
    // One edge deliberately shifted more (simulate encroachment)
    const bias = (i === 1 || i === 2) ? 0.00015 : 0;
    return [
      jitter(lat + bias, noiseBase),
      jitter(lon, noiseBase),
    ] as [number, number];
  });
}

/** Build GeoJSON Feature string from a Leaflet [lat,lon] ring */
function ringToGeoJSON(ring: [number, number][]): string {
  const coords = ring.map(([lat, lon]) => [lon, lat]);
  return JSON.stringify({
    type: 'Feature',
    geometry: { type: 'Polygon', coordinates: [coords] },
  });
}

// ─── Sub-component: auto-flyTo selected parcel ────────────────────────────────
function FlyToParcel({ parcel }: { parcel: Parcel | null }) {
  const map = useMap();
  useEffect(() => {
    if (!parcel) return;
    const coords = extractCoords(parcel.boundary_geojson);
    if (coords.length === 0) return;
    const latlngs = coords.map(([lat, lon]) => L.latLng(lat, lon));
    const bounds = L.latLngBounds(latlngs);
    map.fitBounds(bounds, { padding: [60, 60] });
  }, [parcel, map]);
  return null;
}

// ─── Main App ─────────────────────────────────────────────────────────────────
export default function App() {
  const [parcels, setParcels]           = useState<Parcel[]>([]);
  const [selected, setSelected]         = useState<Parcel | null>(null);
  const [surveyState, setSurveyState]   = useState<SurveyState>('idle');
  const [surveyPoints, setSurveyPoints] = useState<[number, number][]>([]);
  const [fullSurveyRing, setFullSurveyRing] = useState<[number, number][]>([]);
  const [stepIdx, setStepIdx]           = useState(0);
  const [telemetry, setTelemetry]       = useState<GNSSTelemetry | null>(null);
  const [compareResult, setCompareResult] = useState<CompareResult | null>(null);
  const [rtkDone, setRtkDone]           = useState(false);
  const [loading, setLoading]           = useState(true);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── Fetch parcels ──────────────────────────────────────────────────────────
  const fetchParcels = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/parcels`);
      const data: Parcel[] = await res.json();
      setParcels(data);
    } catch {
      console.error('Failed to fetch parcels');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchParcels(); }, [fetchParcels]);

  // ── Metrics ────────────────────────────────────────────────────────────────
  const total    = parcels.length;
  const verified = parcels.filter(p => p.risk_level === 'GREEN').length;
  const yellow   = parcels.filter(p => p.risk_level === 'YELLOW').length;
  const red      = parcels.filter(p => p.risk_level === 'RED').length;

  // ── Select parcel ──────────────────────────────────────────────────────────
  function handleSelect(p: Parcel) {
    setSelected(p);
    setSurveyState('idle');
    setSurveyPoints([]);
    setFullSurveyRing([]);
    setStepIdx(0);
    setTelemetry(null);
    setCompareResult(null);
    setRtkDone(false);
  }

  // ── Start survey simulation ────────────────────────────────────────────────
  function startSurvey() {
    if (!selected) return;
    const original = extractCoords(selected.boundary_geojson);
    if (original.length === 0) return;
    const ring = buildSurveyedRing(original);
    setFullSurveyRing(ring);
    setSurveyPoints([ring[0]]);
    setStepIdx(1);
    setSurveyState('running');
    setCompareResult(null);
    setRtkDone(false);
  }

  // ── Interval: advance survey walk ─────────────────────────────────────────
  useEffect(() => {
    if (surveyState !== 'running') {
      if (intervalRef.current) clearInterval(intervalRef.current);
      return;
    }
    intervalRef.current = setInterval(() => {
      setStepIdx(prev => {
        const next = prev + 1;
        if (next >= fullSurveyRing.length) {
          setSurveyState('paused');
          return prev;
        }
        setSurveyPoints(pts => [...pts, fullSurveyRing[next - 1]]);
        // Simulate live telemetry
        setTelemetry({
          satellites: 16 + Math.floor(Math.random() * 6),
          accuracy: parseFloat((1.1 + Math.random() * 0.6).toFixed(2)),
          fix_type: '3D-GNSS',
          altitude: parseFloat((13.5 + Math.random() * 2).toFixed(1)),
          lat: fullSurveyRing[prev][0],
          lon: fullSurveyRing[prev][1],
        });
        return next;
      });
    }, 600);
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [surveyState, fullSurveyRing]);

  // ── Complete survey ────────────────────────────────────────────────────────
  async function completeSurvey() {
    if (!selected) return;
    setSurveyState('complete');
    if (intervalRef.current) clearInterval(intervalRef.current);

    // Use full ring as the surveyed polygon
    const surveyedGeoJSON = ringToGeoJSON(fullSurveyRing);

    try {
      // 1. Create survey record
      await fetch(`${API}/api/surveys`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          parcel_id: selected.id,
          surveyor: 'ESP32-GNSS-SIM [SIMULATED]',
          survey_method: 'GNSS',
        }),
      });

      // 2. Run Shapely comparison
      const cmpRes = await fetch(`${API}/api/parcels/${selected.id}/compare`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ surveyed_geojson: surveyedGeoJSON }),
      });
      const cmpData: CompareResult = await cmpRes.json();
      setCompareResult(cmpData);

      // Refresh parcel list so map colors update
      await fetchParcels();
    } catch (err) {
      console.error('Survey completion error:', err);
    }
  }

  // ── RTK Verify ────────────────────────────────────────────────────────────
  async function deployRTK() {
    if (!selected) return;
    try {
      await fetch(`${API}/api/parcels/${selected.id}/verify`, { method: 'PATCH' });
      setRtkDone(true);
      await fetchParcels();
      // Update compareResult risk display
      setCompareResult(prev => prev ? { ...prev, risk_level: 'GREEN', anomaly_detected: false } : prev);
    } catch (err) {
      console.error('RTK verify error:', err);
    }
  }

  // ── GeoJSON style per parcel ───────────────────────────────────────────────
  function styleForParcel(feature?: GeoJsonObject) {
    if (!feature) return {};
    const feat = feature as Feature & { parcelId?: string };
    const parcel = parcels.find(p => p.id === feat.parcelId);
    const color = parcel ? (RISK_COLORS[parcel.risk_level] ?? '#60a5fa') : '#60a5fa';
    const isSelected = selected?.id === parcel?.id;
    return {
      color,
      weight: isSelected ? 3 : 1.5,
      opacity: 0.9,
      fillColor: color,
      fillOpacity: isSelected ? 0.35 : 0.15,
    };
  }

  // Build merged GeoJSON FeatureCollection for all parcels
  const featureCollection: GeoJsonObject = {
    type: 'FeatureCollection',
    // @ts-expect-error: dynamic features
    features: parcels.map(p => {
      try {
        const feat = JSON.parse(p.boundary_geojson) as Feature;
        return { ...feat, parcelId: p.id };
      } catch { return null; }
    }).filter(Boolean),
  };

  // ─── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="flex flex-col h-screen bg-slate-900 text-slate-200 overflow-hidden">

      {/* ── Header ── */}
      <header className="flex items-center justify-between px-5 py-3 bg-slate-950 border-b border-slate-800 shrink-0 z-50">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-blue-600 rounded-lg">
            <Map size={18} className="text-white" />
          </div>
          <div>
            <h1 className="text-base font-bold text-white leading-tight tracking-tight">
              Smart Land Survey &amp; Digital Resurvey Platform
            </h1>
            <p className="text-xs text-slate-400">India Cadastral Intelligence System</p>
          </div>
        </div>
        <div className="flex items-center gap-2 bg-emerald-950 border border-emerald-800 px-3 py-1.5 rounded-full">
          <span className="w-2 h-2 rounded-full bg-emerald-400 pulse-dot" />
          <span className="text-xs font-semibold text-emerald-400 uppercase tracking-widest">
            Simulated Hardware Mode Active
          </span>
        </div>
      </header>

      {/* ── Metrics Row ── */}
      <div className="grid grid-cols-4 gap-3 px-4 py-3 bg-slate-900 border-b border-slate-800 shrink-0">
        {[
          { label: 'Total Parcels',        value: total,    icon: <Map size={16} />,           color: 'text-blue-400',    bg: 'bg-blue-900/30' },
          { label: 'Verified (GREEN)',      value: verified, icon: <CheckCircle size={16} />,   color: 'text-emerald-400', bg: 'bg-emerald-900/30' },
          { label: 'Under Review (YELLOW)', value: yellow,   icon: <AlertTriangle size={16} />, color: 'text-yellow-400',  bg: 'bg-yellow-900/30' },
          { label: 'Flagged (RED)',         value: red,      icon: <XCircle size={16} />,       color: 'text-red-400',     bg: 'bg-red-900/30' },
        ].map(m => (
          <div key={m.label} className={`flex items-center gap-3 ${m.bg} border border-slate-700 rounded-xl px-4 py-3`}>
            <span className={m.color}>{m.icon}</span>
            <div>
              <div className={`text-2xl font-bold ${m.color}`}>{loading ? '…' : m.value}</div>
              <div className="text-xs text-slate-400">{m.label}</div>
            </div>
          </div>
        ))}
      </div>

      {/* ── Main Layout ── */}
      <div className="flex flex-1 overflow-hidden">

        {/* ── Parcel List ── */}
        <aside className="w-64 bg-slate-950 border-r border-slate-800 flex flex-col overflow-hidden shrink-0">
          <div className="px-3 py-2 border-b border-slate-800">
            <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Land Parcels</p>
          </div>
          <div className="flex-1 overflow-y-auto">
            {loading ? (
              <div className="p-4 text-slate-500 text-sm">Loading…</div>
            ) : parcels.map(p => (
              <button
                key={p.id}
                onClick={() => handleSelect(p)}
                className={`w-full text-left px-3 py-2.5 border-b border-slate-800 transition-colors hover:bg-slate-800 ${selected?.id === p.id ? 'bg-slate-800 border-l-2' : ''}`}
                style={selected?.id === p.id ? { borderLeftColor: RISK_COLORS[p.risk_level] ?? '#60a5fa' } : {}}
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-mono text-slate-300">{p.id}</span>
                  <span
                    className="text-xs font-bold px-1.5 py-0.5 rounded"
                    style={{ color: RISK_COLORS[p.risk_level], backgroundColor: RISK_COLORS[p.risk_level] + '22' }}
                  >
                    {p.risk_level}
                  </span>
                </div>
                <div className="text-xs text-slate-500 mt-0.5">Survey #{p.survey_number}</div>
                <div className="text-xs text-slate-400">{p.village}, {p.district}</div>
              </button>
            ))}
          </div>
        </aside>

        {/* ── Map ── */}
        <div className="flex-1 relative">
          <MapContainer
            center={[13.0827, 80.2707]}
            zoom={14}
            style={{ height: '100%', width: '100%' }}
            zoomControl={false}
          >
            <TileLayer
              url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
              attribution='&copy; <a href="https://carto.com/">CARTO</a>'
              maxZoom={20}
            />

            {/* Parcel boundaries */}
            {parcels.length > 0 && (
              <GeoJSON
                key={JSON.stringify(parcels.map(p => p.risk_level + p.id))}
                data={featureCollection}
                style={(f) => styleForParcel(f as GeoJsonObject)}
                onEachFeature={(feature, layer) => {
                  const feat = feature as Feature & { parcelId?: string };
                  const parcel = parcels.find(p => p.id === feat.parcelId);
                  if (parcel) {
                    layer.bindTooltip(`<b>${parcel.id}</b><br/>${parcel.owner_reference}<br/>${parcel.recorded_area} acres`, { className: 'leaflet-tooltip' });
                    layer.on('click', () => handleSelect(parcel));
                  }
                }}
              />
            )}

            {/* Live survey overlay (dashed orange) */}
            {surveyPoints.length >= 2 && (
              <Polygon
                positions={surveyPoints}
                pathOptions={{
                  color: '#f97316',
                  weight: 2,
                  dashArray: '8 6',
                  fillColor: '#f97316',
                  fillOpacity: 0.08,
                }}
              />
            )}

            <FlyToParcel parcel={selected} />
          </MapContainer>
        </div>

        {/* ── Right Panel ── */}
        {selected && (
          <aside className="w-80 bg-slate-950 border-l border-slate-800 flex flex-col overflow-y-auto shrink-0">

            {/* Parcel Info */}
            <div className="px-4 py-3 border-b border-slate-800">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Selected Parcel</span>
                <span
                  className={`text-xs font-bold px-2 py-0.5 rounded border ${RISK_BG[compareResult?.risk_level ?? selected.risk_level]}`}
                >
                  {compareResult?.risk_level ?? selected.risk_level}
                </span>
              </div>
              <div className="space-y-1.5">
                {[
                  ['Parcel ID',    selected.id],
                  ['Survey No.',   selected.survey_number],
                  ['Owner',        selected.owner_reference],
                  ['Village',      `${selected.village}, ${selected.district}`],
                  ['Recorded Area', `${selected.recorded_area.toFixed(2)} acres`],
                  ['Status',       selected.status],
                ].map(([label, val]) => (
                  <div key={label} className="flex justify-between text-xs">
                    <span className="text-slate-500">{label}</span>
                    <span className="text-slate-200 font-medium text-right max-w-[60%] truncate">{val}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Survey Controls */}
            <div className="px-4 py-3 border-b border-slate-800">
              <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">GNSS Survey Control</p>

              {surveyState === 'idle' && (
                <button
                  onClick={startSurvey}
                  className="w-full flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold py-2.5 rounded-lg transition-colors"
                >
                  <Play size={15} /> Start GNSS Live Survey
                </button>
              )}

              {(surveyState === 'running' || surveyState === 'paused') && (
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <div className={`w-2 h-2 rounded-full ${surveyState === 'running' ? 'bg-orange-400 pulse-dot' : 'bg-slate-500'}`} />
                    <span className="text-xs text-slate-300 font-medium uppercase tracking-wide">
                      {surveyState === 'running' ? 'Walking Perimeter…' : 'Paused'}
                    </span>
                  </div>
                  {/* Progress bar */}
                  <div className="w-full bg-slate-800 rounded-full h-1.5">
                    <div
                      className="bg-orange-500 h-1.5 rounded-full transition-all"
                      style={{ width: `${Math.min(100, (stepIdx / Math.max(fullSurveyRing.length, 1)) * 100)}%` }}
                    />
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => setSurveyState(s => s === 'running' ? 'paused' : 'running')}
                      className="flex-1 flex items-center justify-center gap-1.5 bg-slate-700 hover:bg-slate-600 text-slate-200 text-xs font-semibold py-2 rounded-lg transition-colors"
                    >
                      {surveyState === 'running' ? <><Pause size={13} /> Pause</> : <><Play size={13} /> Resume</>}
                    </button>
                    <button
                      onClick={completeSurvey}
                      className="flex-1 flex items-center justify-center gap-1.5 bg-emerald-700 hover:bg-emerald-600 text-white text-xs font-semibold py-2 rounded-lg transition-colors"
                    >
                      <StopCircle size={13} /> Complete
                    </button>
                  </div>
                </div>
              )}

              {surveyState === 'complete' && !compareResult && (
                <div className="flex items-center gap-2 text-slate-400 text-xs">
                  <RefreshCw size={14} className="animate-spin" /> Processing comparison…
                </div>
              )}

              {surveyState === 'idle' || surveyState === 'complete' ? null : null}
            </div>

            {/* Live Telemetry */}
            {telemetry && surveyState !== 'idle' && (
              <div className="px-4 py-3 border-b border-slate-800">
                <div className="flex items-center gap-2 mb-2">
                  <Radio size={13} className="text-orange-400" />
                  <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Live Telemetry</p>
                  <span className="text-xs text-orange-400 font-mono">[SIMULATED DATA]</span>
                </div>
                <div className="bg-slate-900 border border-slate-700 rounded-lg p-3 space-y-1.5 font-mono text-xs">
                  {[
                    ['Device',     'ESP32-GNSS-SIM'],
                    ['Satellites', `${telemetry.satellites} SVs`],
                    ['Accuracy',   `±${telemetry.accuracy}m`],
                    ['Fix Type',   telemetry.fix_type],
                    ['Altitude',   `${telemetry.altitude}m`],
                    ['Lat',        telemetry.lat.toFixed(6)],
                    ['Lon',        telemetry.lon.toFixed(6)],
                  ].map(([k, v]) => (
                    <div key={k} className="flex justify-between">
                      <span className="text-slate-500">{k}</span>
                      <span className="text-emerald-400">{v}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Comparison Results */}
            {compareResult && (
              <div className="px-4 py-3 border-b border-slate-800 slide-up">
                <div className="flex items-center gap-2 mb-3">
                  <Activity size={13} className="text-blue-400" />
                  <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Shapely Analysis Results</p>
                </div>

                {/* Diagnosis banner */}
                {compareResult.anomaly_detected && !rtkDone ? (
                  <div className="flex items-center gap-2 bg-red-950 border border-red-800 rounded-lg px-3 py-2 mb-3">
                    <XCircle size={14} className="text-red-400 shrink-0" />
                    <span className="text-xs text-red-300 font-semibold">🔴 HIGH DISCREPANCY DETECTED</span>
                  </div>
                ) : (
                  <div className="flex items-center gap-2 bg-emerald-950 border border-emerald-800 rounded-lg px-3 py-2 mb-3">
                    <CheckCircle size={14} className="text-emerald-400 shrink-0" />
                    <span className="text-xs text-emerald-300 font-semibold">🟢 BOUNDARIES MATCH</span>
                  </div>
                )}

                <div className="bg-slate-900 border border-slate-700 rounded-lg p-3 space-y-1.5 text-xs">
                  {[
                    ['Recorded Area',    `${compareResult.existing_area.toFixed(2)} acres`],
                    ['Surveyed Area',    `${compareResult.surveyed_area.toFixed(2)} acres`],
                    ['Difference',       `${compareResult.area_difference.toFixed(2)} acres`],
                    ['Variance',         `${compareResult.area_difference_percentage.toFixed(2)}%`],
                    ['Overlap',          `${compareResult.overlap_area.toFixed(2)} acres`],
                    ['Anomaly Type',     compareResult.anomaly_type],
                    ['Risk Level',       rtkDone ? 'GREEN (RTK Override)' : compareResult.risk_level],
                  ].map(([k, v]) => (
                    <div key={k} className="flex justify-between">
                      <span className="text-slate-500">{k}</span>
                      <span className={k === 'Risk Level' ? (rtkDone || !compareResult.anomaly_detected ? 'text-emerald-400' : 'text-red-400') : 'text-slate-200'}>
                        {v}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* RTK Override */}
            {compareResult?.anomaly_detected && !rtkDone && (
              <div className="px-4 py-3 slide-up">
                <div className="bg-yellow-950 border border-yellow-800 rounded-lg p-3 mb-3">
                  <div className="flex items-center gap-2 mb-1">
                    <AlertTriangle size={13} className="text-yellow-400" />
                    <span className="text-xs font-semibold text-yellow-300">Tolerance Threshold Exceeded</span>
                  </div>
                  <p className="text-xs text-yellow-500">
                    Discrepancy {compareResult.area_difference_percentage.toFixed(2)}% exceeds acceptable
                    threshold. Deploy RTK for precision verification.
                  </p>
                </div>
                <button
                  onClick={deployRTK}
                  className="w-full flex items-center justify-center gap-2 bg-violet-700 hover:bg-violet-600 text-white text-sm font-semibold py-2.5 rounded-lg transition-colors"
                >
                  <Shield size={15} /> Deploy RTK Precision Check
                </button>
              </div>
            )}

            {/* RTK Success */}
            {rtkDone && (
              <div className="px-4 py-3 slide-up">
                <div className="flex items-center gap-2 bg-emerald-950 border border-emerald-700 rounded-lg px-3 py-2">
                  <CheckCircle size={14} className="text-emerald-400" />
                  <span className="text-xs text-emerald-300 font-semibold">
                    RTK Override — Parcel Verified (±0.03m accuracy)
                  </span>
                </div>
              </div>
            )}

            {/* Start new survey after completion */}
            {surveyState === 'complete' && compareResult && (
              <div className="px-4 py-3">
                <button
                  onClick={() => {
                    setSurveyState('idle');
                    setSurveyPoints([]);
                    setFullSurveyRing([]);
                    setStepIdx(0);
                    setTelemetry(null);
                    setCompareResult(null);
                    setRtkDone(false);
                  }}
                  className="w-full flex items-center justify-center gap-2 bg-slate-700 hover:bg-slate-600 text-slate-200 text-xs font-semibold py-2 rounded-lg transition-colors"
                >
                  <RefreshCw size={13} /> New Survey
                </button>
              </div>
            )}

            {/* Hint when nothing selected */}
            {!selected && (
              <div className="flex-1 flex items-center justify-center text-slate-600 text-xs p-4 text-center">
                <div>
                  <ChevronRight size={24} className="mx-auto mb-2 opacity-40" />
                  Click a parcel on the list or map to begin
                </div>
              </div>
            )}
          </aside>
        )}
      </div>
    </div>
  );
}

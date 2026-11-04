import { useCallback, useEffect, useMemo, useRef, useState, Fragment } from 'react'
import { MapContainer, Marker, Polygon, Polyline, TileLayer, Tooltip, useMap } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { get } from '../lib/api.js'
import { storeActions, useStore, toast } from '../lib/store.js'
import { Panel, PanelHeader, StateChip, cameraId, fmtTime } from './ui.jsx'

// Defensive Leaflet bounds update monkey patch to prevent crashes on uninitialized geometry
if (typeof window !== 'undefined') {
  if (L?.Polyline?.prototype) {
    const origPolyUpdateBounds = L.Polyline.prototype._updateBounds
    L.Polyline.prototype._updateBounds = function () {
      if (!this._rawPxBounds || !this._rawPxBounds.min || !this._rawPxBounds.max) {
        return
      }
      return origPolyUpdateBounds.call(this)
    }
  }
  if (L?.CircleMarker?.prototype) {
    const origCircleUpdateBounds = L.CircleMarker.prototype._updateBounds
    L.CircleMarker.prototype._updateBounds = function () {
      if (!this._point) return
      return origCircleUpdateBounds.call(this)
    }
  }
}

const STATE_FILL = {
  normal: '#10B981',
  watch: '#F59E0B',
  escalating: '#F97316',
  verifying: '#F97316',
  critical: '#EF4444',
  signal_lost: '#5A6B80',
}

const RANK = { critical: 4, escalating: 3, verifying: 3, watch: 2, normal: 1, signal_lost: 0 }

// Flood Prone Uttarakhand — Upper Ganga & Alaknanda Basin Base Maps (OpenStreetMap Default)
const MAP_THEMES = {
  osm: {
    id: 'osm',
    label: 'OpenStreetMap',
    desc: 'OpenStreetMap Standard (Official High-Detail Map)',
    url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    subdomains: '',
    attr: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
    cssClass: 'at-tiles-osm',
  },
  hot: {
    id: 'hot',
    label: 'OSM Humanitarian',
    desc: 'Humanitarian OSM (Disaster Relief & High-Contrast Flood View)',
    url: 'https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png',
    subdomains: 'abc',
    attr: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, Tiles by <a href="https://www.hotosm.org/">Humanitarian OSM</a>',
    maxZoom: 19,
    cssClass: 'at-tiles-hot',
  },
  topo: {
    id: 'topo',
    label: 'Himalayan Topo',
    desc: 'OpenTopoMap High-Resolution Himalayan Elevation Contours',
    url: 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
    subdomains: 'abc',
    attr: '&copy; OpenTopoMap (CC-BY-SA)',
    maxZoom: 17,
    cssClass: 'at-tiles-topo',
  },
  satellite: {
    id: 'satellite',
    label: 'Satellite',
    desc: 'ESRI World Imagery (Garhwal Himalayan River Gorge Aerial)',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    subdomains: '',
    attr: 'Tiles &copy; Esri &mdash; Maxar, Earthstar Geographics, USDA, USGS',
    maxZoom: 19,
    cssClass: 'at-tiles-satellite',
  },
}

// High Ground Disaster Shelters & Relief Sanctuaries in Uttarakhand Upper Ganga Basin
const EVACUATION_SHELTERS = [
  {
    id: 'shelter-aiims',
    name: 'AIIMS Rishikesh & IDPL Primary Relief Hub',
    shortName: 'AIIMS Relief Hub',
    pos: [30.0780, 78.2810],
    elev: '395m AMSL (+45m above river)',
    elevGain: '+45m Safe Elevation',
    cap: '5,000 Capacity · Level-1 Trauma · Helipad',
    type: 'Primary Medical & Mass Relief Sanctuary',
    color: '#10B981', // Emerald
    camera_id: 'cam-02',
    originName: 'Triveni Ghat Riverfront (cam-02)',
    originPos: [30.1030, 78.2970],
    distance: '3.4 km',
    walkTime: '24 min walk',
    vehicleTime: '8 min emergency 4x4',
    safetyStatus: '100% FLOOD SAFE · ELEVATED ALL-WEATHER HIGHWAYS',
    contact: 'VHF 156.800 MHz · SDRF Sector 4 Command',
    facilities: ['Level-1 Trauma ICU', '2x Helicopter Helipads', 'Standby Diesel Gensets', 'Bulk Potable Water', 'NDRF Medical Desk'],
    positions: [
      [30.1030, 78.2970], // Triveni Ghat Flood Gauge
      [30.1020, 78.2950], // Mayakund Floodwall Ramp
      [30.1000, 78.2925], // Haridwar Road Junction
      [30.0960, 78.2900], // NH-34 Haridwar Bypass
      [30.0915, 78.2870], // Kailash Gate Intersection
      [30.0875, 78.2845], // Dehradun Road Flyover Incline
      [30.0835, 78.2825], // Virbhadra Marg Elevated Corridor
      [30.0805, 78.2815], // AIIMS Main Boulevard
      [30.0780, 78.2810], // AIIMS Safe Gate 2
    ],
    waypoints: [
      { pos: [30.1030, 78.2970], label: 'START: Triveni Ghat Riverbank (cam-02)', desc: 'Evacuate rising floodwater immediately', type: 'start' },
      { pos: [30.1000, 78.2925], label: 'TURN: Haridwar Road (NH-34)', desc: 'Southbound highway away from river', type: 'turn' },
      { pos: [30.0875, 78.2845], label: 'ASCENT: Dehradun Road Flyover', desc: 'Elevated bypass over low-lying culverts', type: 'turn' },
      { pos: [30.0835, 78.2825], label: 'TURN: Virbhadra Marg Corridor', desc: 'Climbing to AIIMS elevated terrace', type: 'turn' },
      { pos: [30.0780, 78.2810], label: 'DESTINATION: AIIMS Rishikesh Gate 2', desc: '395m AMSL Safe Sanctuary & Trauma Center', type: 'end' },
    ],
    steps: [
      { num: 1, action: 'Immediate Evacuation', road: 'Mayakund Floodwall Ramp', dist: '180 m', icon: '⚠️', instruction: 'Ascend immediately from Triveni Ghat floodline via Mayakund concrete floodwall ramp.' },
      { num: 2, action: 'Turn Right', road: 'Haridwar Road (NH-34)', dist: '1.2 km', icon: '↱', instruction: 'Turn right onto Haridwar Road (NH-34) heading south, moving directly away from the river.' },
      { num: 3, action: 'Ascend Flyover', road: 'Dehradun Road Elevated Bypass', dist: '850 m', icon: '⇡', instruction: 'Continue onto Dehradun Road elevated flyover, bypassing storm runoff choke points.' },
      { num: 4, action: 'Turn Left', road: 'Virbhadra Marg High Road', dist: '700 m', icon: '↰', instruction: 'Turn left onto Virbhadra Marg, climbing the elevated western river terrace toward AIIMS.' },
      { num: 5, action: 'Turn Right', road: 'AIIMS Main Boulevard', dist: '470 m', icon: '↱', instruction: 'Enter AIIMS Campus Gate 2, following emergency evacuation signage to the Triage Area.' },
      { num: 6, action: 'Sanctuary Check-in', road: 'NDRF Command Desk', dist: 'ARRIVED', icon: '🛡️', instruction: 'Safe elevation: 395m AMSL (+45m clearance). Report to NDRF / SDRF medical command desk.' },
    ],
  },
  {
    id: 'shelter-narendra',
    name: 'Narendra Nagar Foothill Staging Area',
    shortName: 'Narendra Nagar Ridge',
    pos: [30.1380, 78.3370],
    elev: '485m AMSL (+110m above gorge)',
    elevGain: '+110m Mountain Ascent',
    cap: '2,500 Capacity · NDRF High Base Camp',
    type: 'High-Ground Mountain Sanctuary',
    color: '#06B6D4', // Cyan
    camera_id: 'cam-01',
    originName: 'Tapovan Ganga Gorge (cam-01)',
    originPos: [30.1250, 78.3180],
    distance: '2.8 km',
    walkTime: '18 min walk',
    vehicleTime: '7 min 4x4',
    safetyStatus: 'ZERO FLOOD RISK · HIGH HIMALAYAN FOOTHILLS',
    contact: 'VHF 154.250 MHz · NDRF Mountain Battalion',
    facilities: ['NDRF Mountain Rescue Command', 'Helicopter Air-Drop Zone', 'Satellite Comms Link (VSAT)', 'Heated Emergency Tents', 'High-Angle Rope Rescue Depot'],
    positions: [
      [30.1250, 78.3180], // Tapovan Riverfront Gorge
      [30.1268, 78.3205], // Tapovan Chowk
      [30.1285, 78.3240], // NH-7 Highway Junction
      [30.1310, 78.3280], // NH-7 Mountain Hairpin 1
      [30.1338, 78.3315], // Tehri Ridge Ascent
      [30.1360, 78.3345], // Narendra Nagar Foothill Spur
      [30.1380, 78.3370], // Narendra Nagar Base Camp
    ],
    waypoints: [
      { pos: [30.1250, 78.3180], label: 'START: Tapovan Riverfront Gorge (cam-01)', desc: 'Evacuate riverbank gorge immediately', type: 'start' },
      { pos: [30.1285, 78.3240], label: 'TURN: NH-7 Badrinath Highway', desc: 'Begin rapid mountain ascent eastwards', type: 'turn' },
      { pos: [30.1338, 78.3315], label: 'ASCENT: Tehri Mountain Ridge Hairpin', desc: 'Engineered highway above 500-yr flood level', type: 'turn' },
      { pos: [30.1380, 78.3370], label: 'DESTINATION: Narendra Nagar Base Camp', desc: '485m AMSL High Ridge Safe Sanctuary', type: 'end' },
    ],
    steps: [
      { num: 1, action: 'Immediate Ascent', road: 'Tapovan Retaining Steps', dist: '180 m', icon: '⚠️', instruction: 'Ascend immediately from Tapovan riverbanks via concrete retaining steps away from gorge.' },
      { num: 2, action: 'Turn Right', road: 'Tapovan High Street', dist: '350 m', icon: '↱', instruction: 'Follow Tapovan High Street eastward, passing Lakshman Jhula turnoff to reach highway junction.' },
      { num: 3, action: 'Merge onto NH-7', road: 'Badrinath National Highway', dist: '800 m', icon: '↰', instruction: 'Merge onto NH-7 (Badrinath Highway), ascending steadily up the rocky mountain spine.' },
      { num: 4, action: 'Navigate Ridge', road: 'NH-7 Hairpin Curves', dist: '900 m', icon: '⇡', instruction: 'Follow engineered highway curves well above the maximum historical surge crest.' },
      { num: 5, action: 'Turn Right', road: 'Tehri Foothill Bypass', dist: '570 m', icon: '↱', instruction: 'Turn right onto Narendra Nagar Foothill access road to enter the disaster staging compound.' },
      { num: 6, action: 'Sanctuary Check-in', road: 'NDRF Mountain Desk', dist: 'ARRIVED', icon: '🛡️', instruction: 'Safe elevation: 485m AMSL (+110m clearance). Report to NDRF Mountain Battalion registrar.' },
    ],
  },
  {
    id: 'shelter-railway',
    name: 'Yog Nagari Elevated Rail Assembly Center',
    shortName: 'Yog Nagari Elevated Rail',
    pos: [30.0840, 78.2770],
    elev: '375m AMSL (+37m above barrage)',
    elevGain: '+37m High Platform',
    cap: '4,000 Capacity · Standby Evacuation Trains',
    type: 'Mass Transit & Logistics Sanctuary',
    color: '#F59E0B', // Amber
    camera_id: 'cam-03',
    originName: 'Veerbhadra Barrage Spillway (cam-03)',
    originPos: [30.0760, 78.2880],
    distance: '1.9 km',
    walkTime: '14 min walk',
    vehicleTime: '4 min vehicle',
    safetyStatus: 'REINFORCED ELEVATED INFRASTRUCTURE · FLOOD SECURE',
    contact: 'VHF 161.150 MHz · Railway Emergency Operations',
    facilities: ['High-Level Covered Platforms', 'Stationed Relief Evacuation Trains', 'Standby Diesel Generators', 'Community Kitchens', 'Northern Railway Medical Wing'],
    positions: [
      [30.0760, 78.2880], // Veerbhadra Barrage
      [30.0775, 78.2855], // Spillway Crest Road
      [30.0795, 78.2825], // West Embankment Bund
      [30.0815, 78.2795], // Railway Link Road
      [30.0835, 78.2778], // Elevated Terminal Ramp
      [30.0840, 78.2770], // Yog Nagari Rail Sanctuary
    ],
    waypoints: [
      { pos: [30.0760, 78.2880], label: 'START: Veerbhadra Barrage Sluice (cam-03)', desc: 'Clear sluice catwalks and spillway', type: 'start' },
      { pos: [30.0795, 78.2825], label: 'TURN: West Embankment Bund', desc: 'Elevated stone levee above backwater', type: 'turn' },
      { pos: [30.0815, 78.2795], label: 'ASCENT: Railway Link Boulevard', desc: 'Direct paved road to terminal', type: 'turn' },
      { pos: [30.0840, 78.2770], label: 'DESTINATION: Yog Nagari Elevated Terminal', desc: '375m AMSL Multi-Modal Rail Sanctuary', type: 'end' },
    ],
    steps: [
      { num: 1, action: 'Barrage Evacuation', road: 'Spillway Access Crest', dist: '200 m', icon: '⚠️', instruction: 'Disengage from Veerbhadra Barrage sluice catwalks via Spillway Crest Road.' },
      { num: 2, action: 'Turn Left', road: 'West Embankment Bund', dist: '600 m', icon: '↰', instruction: 'Follow reinforced western stone embankment above the backwater storage zone.' },
      { num: 3, action: 'Turn Right', road: 'IDPL Outer Link Road', dist: '450 m', icon: '↱', instruction: 'Turn right onto IDPL Outer Link Road heading northwest toward railway complex.' },
      { num: 4, action: 'Turn Left', road: 'Railway Link Boulevard', dist: '350 m', icon: '↰', instruction: 'Turn left onto Railway Link Boulevard approaching station terminal.' },
      { num: 5, action: 'Ascend Ramp', road: 'Elevated Concourse Incline', dist: '300 m', icon: '⇡', instruction: 'Ascend elevated multi-modal terminal ramp to Platform Level 2.' },
      { num: 6, action: 'Sanctuary Boarding', road: 'Evacuation Rail Desk', dist: 'ARRIVED', icon: '🛡️', instruction: 'Safe elevation: 375m AMSL (+37m clearance). Board standby evacuation trains or shelter on Concourse.' },
    ],
  },
  {
    id: 'shelter-rajaji',
    name: 'Rajaji Foothill Emergency Post',
    shortName: 'Rajaji Hill Post',
    pos: [30.0630, 78.2940],
    elev: '420m AMSL (+85m above canal)',
    elevGain: '+85m East Bank Ridge',
    cap: '1,500 Capacity · Emergency Comms Post',
    type: 'East Bank High-Ground Sanctuary',
    color: '#38BDF8', // Sky
    camera_id: 'cam-04',
    originName: 'Chilla Power Canal Intake (cam-04)',
    originPos: [30.0520, 78.2750],
    distance: '2.3 km',
    walkTime: '16 min walk',
    vehicleTime: '6 min 4x4',
    safetyStatus: 'EAST BANK PROTECTED CREST · NON-SUBMERGIBLE',
    contact: 'HF 7.050 MHz / VHF 146.520 MHz · Forest Emergency Net',
    facilities: ['Solar Microgrid Battery Storage', 'Forest Ranger High-Ground Outpost', 'Satellite Emergency Telephone', 'Off-Road 4x4 Fleet', 'Emergency First-Aid Stock'],
    positions: [
      [30.0520, 78.2750], // Chilla Intake
      [30.0545, 78.2785], // East Bund Road
      [30.0575, 78.2830], // East Perimeter Crest
      [30.0600, 78.2880], // Rajaji Foothill Incline
      [30.0620, 78.2915], // Wireless Outpost Spur
      [30.0630, 78.2940], // Rajaji Post Sanctuary
    ],
    waypoints: [
      { pos: [30.0520, 78.2750], label: 'START: Chilla Canal Intake (cam-04)', desc: 'Clear sluice gates and headworks', type: 'start' },
      { pos: [30.0545, 78.2785], label: 'ASCENT: East Bank Bund Crown', desc: 'Levee crown above canal spillway', type: 'turn' },
      { pos: [30.0600, 78.2880], label: 'TURN: Rajaji Ridge Road', desc: 'Foothill road toward wildlife reserve', type: 'turn' },
      { pos: [30.0630, 78.2940], label: 'DESTINATION: Rajaji Emergency Post', desc: '420m AMSL High Ridge Sanctuary', type: 'end' },
    ],
    steps: [
      { num: 1, action: 'Clear Canal Sluice', road: 'Canal Headworks Barrier', dist: '250 m', icon: '⚠️', instruction: 'Evacuate Chilla Canal intake sluice immediately along the concrete flood barrier bund.' },
      { num: 2, action: 'Turn Left', road: 'East Bank Perimeter Bund', dist: '650 m', icon: '↰', instruction: 'Follow East Bank perimeter bund, keeping to the elevated top crown of the levee.' },
      { num: 3, action: 'Turn Right', road: 'Rajaji Foothill Road', dist: '750 m', icon: '↱', instruction: 'Turn right onto Rajaji Foothill Road climbing up towards the protected reserve boundary.' },
      { num: 4, action: 'Bear Left', road: 'Wireless Station Spur', dist: '450 m', icon: '↰', instruction: 'Bear left onto all-weather crushed gravel spur road leading to wireless tower.' },
      { num: 5, action: 'Enter Compound', road: 'Forest Outpost Gate', dist: '200 m', icon: '⇡', instruction: 'Enter through the gate of Rajaji Foothill Emergency Post.' },
      { num: 6, action: 'Sanctuary Check-in', road: 'Rajaji Ranger Station', dist: 'ARRIVED', icon: '🛡️', instruction: 'Safe elevation: 420m AMSL (+85m clearance). Shelter in reinforced stone buildings.' },
    ],
  },
]

// Emergency Evacuation Corridors mapped from sanctuaries
const EVACUATION_ROUTES = EVACUATION_SHELTERS.map((s) => ({
  id: `evac-${s.id}`,
  shelter_id: s.id,
  camera_id: s.camera_id,
  name: `${s.originName.split(' ')[0]} → ${s.shortName}`,
  dest: s.name,
  elev_gain: `${s.elevGain} (${s.elev})`,
  distance: `${s.distance} · ${s.walkTime} / ${s.vehicleTime}`,
  status: s.safetyStatus,
  color: s.color,
  positions: s.positions,
}))

function stateOf(c) {
  return c.signal === 'signal_lost' ? 'signal_lost' : c.state || 'normal'
}

function destination(lat, lng, bearingRad, distM) {
  const R = 6371000
  const d = distM / R
  const p1 = (lat * Math.PI) / 180
  const l1 = (lng * Math.PI) / 180
  const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(bearingRad))
  const l2 = l1 + Math.atan2(
    Math.sin(bearingRad) * Math.sin(d) * Math.cos(p1),
    Math.cos(d) - Math.sin(p1) * Math.sin(p2),
  )
  return [(p2 * 180) / Math.PI, (l2 * 180) / Math.PI]
}

function fovPoints(c) {
  if (!Number.isFinite(c.lat) || !Number.isFinite(c.lng)) return []
  const bearing = ((c.bearing_deg ?? 140) * Math.PI) / 180
  const half = (((c.fov_deg ?? 70) / 2) * Math.PI) / 180
  const range = c.range_m ?? 140
  const N = 18
  const pts = []
  for (let i = 0; i <= N; i += 1) {
    pts.push(destination(c.lat, c.lng, bearing - half + (2 * half * i) / N, range))
  }
  pts.push([c.lat, c.lng])
  return pts
}

function extractPolylineCoords(feat) {
  if (Array.isArray(feat?.latlngs) && feat.latlngs.length >= 2) {
    return feat.latlngs.filter(
      (p) => Array.isArray(p) && p.length >= 2 && Number.isFinite(p[0]) && Number.isFinite(p[1]),
    )
  }
  const geom = feat?.geometry
  if (geom?.type === 'LineString' && Array.isArray(geom.coordinates)) {
    return geom.coordinates
      .filter((pt) => Array.isArray(pt) && pt.length >= 2 && Number.isFinite(pt[0]) && Number.isFinite(pt[1]))
      .map(([lng, lat]) => [lat, lng])
  }
  return null
}

function extractPolygonCoords(feat) {
  if (Array.isArray(feat?.latlngs) && feat.latlngs.length >= 3) {
    return feat.latlngs.filter(
      (p) => Array.isArray(p) && p.length >= 2 && Number.isFinite(p[0]) && Number.isFinite(p[1]),
    )
  }
  const geom = feat?.geometry
  if (geom?.type === 'Polygon' && Array.isArray(geom.coordinates) && Array.isArray(geom.coordinates[0])) {
    return geom.coordinates[0]
      .filter((pt) => Array.isArray(pt) && pt.length >= 2 && Number.isFinite(pt[0]) && Number.isFinite(pt[1]))
      .map(([lng, lat]) => [lat, lng])
  }
  return null
}

// ------------------------------------------------------------------ camera pin

function CameraPin({ c, isSel, onSelect }) {
  const cid = cameraId(c)
  const st = stateOf(c)
  const color = STATE_FILL[st] || STATE_FILL.normal
  const alerting = st !== 'normal' && st !== 'signal_lost'

  const icon = useMemo(() => {
    return L.divIcon({
      className: 'at-cam-pin-wrap',
      html: `
        <div style="position:relative; width:40px; height:40px; display:flex; align-items:center; justify-content:center; cursor:pointer;">
          ${alerting ? `<span style="position:absolute; width:40px; height:40px; border-radius:50%; background:${color}44; animation:ping 1.4s cubic-bezier(0,0,0.2,1) infinite;"></span>` : ''}
          <div style="width:24px; height:24px; border-radius:50%; background:#0a0f16; border:2.5px solid ${isSel ? '#22D3EE' : color}; display:flex; align-items:center; justify-content:center; box-shadow:0 0 12px ${color}bb;">
            <div style="width:8px; height:8px; border-radius:50%; background:${color};"></div>
          </div>
          ${isSel ? '<span style="position:absolute; width:34px; height:34px; border-radius:50%; border:2px dashed #22D3EE;"></span>' : ''}
        </div>
      `,
      iconSize: [40, 40],
      iconAnchor: [20, 20],
    })
  }, [color, isSel, alerting])

  return (
    <Marker position={[c.lat, c.lng]} icon={icon} eventHandlers={{ click: onSelect }}>
      <Tooltip direction="right" offset={[18, -4]} opacity={1} className="at-tip">
        <div className="at-tip-title font-semibold text-white">{c.name || cid}</div>
        <div className="at-tip-state font-mono text-[10px]" style={{ color }}>
          {st.replace('_', ' ').toUpperCase()} · {cid}
        </div>
        <div className="text-[9px] text-faint mt-0.5">
          {c.location || 'Upper Ganga Flood Telemetry Network, Uttarakhand'}
        </div>
      </Tooltip>
    </Marker>
  )
}

// ----------------------------------------------------------- evacuation shelter & waypoint pins

function ShelterPin({ s, isSelected, onSelect }) {
  const icon = useMemo(() => {
    const sz = isSelected ? 44 : 36
    const anchor = sz / 2
    return L.divIcon({
      className: 'at-shelter-pin-wrap',
      html: `
        <div style="position:relative; width:${sz}px; height:${sz}px; display:flex; align-items:center; justify-content:center; cursor:pointer;" title="Click for Safe Turn-by-Turn Route Directions">
          <span style="position:absolute; width:${sz}px; height:${sz}px; border-radius:50%; background:${s.color}${isSelected ? '55' : '26'}; animation:ping ${isSelected ? '1.2s' : '2.2s'} cubic-bezier(0,0,0.2,1) infinite;"></span>
          <div style="width:${isSelected ? '30px' : '25px'}; height:${isSelected ? '30px' : '25px'}; border-radius:7px; background:#0a0f16; border:${isSelected ? '2.5px solid #22D3EE' : `2px solid ${s.color}`}; display:flex; align-items:center; justify-content:center; box-shadow:0 0 ${isSelected ? '18px #22D3EE' : `10px ${s.color}aa`}; transition:all 0.2s;">
            <svg width="${isSelected ? '16' : '13'}" height="${isSelected ? '16' : '13'}" viewBox="0 0 24 24" fill="none" stroke="${isSelected ? '#22D3EE' : s.color}" stroke-width="2.5">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
            </svg>
          </div>
          ${isSelected ? `<span style="position:absolute; bottom:-16px; white-space:nowrap; background:#0a0f16f5; color:#22D3EE; border:1px solid #22D3EE88; font-size:8.5px; font-weight:700; padding:1px 5px; border-radius:3px; letter-spacing:0.04em; box-shadow:0 2px 6px rgba(0,0,0,0.6);">SAFE DESTINATION</span>` : ''}
        </div>
      `,
      iconSize: [sz, sz],
      iconAnchor: [anchor, anchor],
    })
  }, [s, isSelected])

  return (
    <Marker position={s.pos} icon={icon} eventHandlers={{ click: () => onSelect(s.id) }}>
      <Tooltip direction="top" offset={[0, -16]} opacity={1} className="at-tip">
        <div className="at-tip-title font-semibold text-emerald-400">🛡️ {s.name}</div>
        <div className="text-[10px] text-white mt-0.5">{s.type}</div>
        <div className="num text-[9px] text-cyan-300 font-semibold mt-0.5">SAFE ELEVATION: {s.elev}</div>
        <div className="text-[9px] text-live font-semibold mt-0.5">➔ CLICK FOR SAFE ROUTE DIRECTIONS</div>
      </Tooltip>
    </Marker>
  )
}

function WaypointPin({ wp, isStart, isEnd, index }) {
  const icon = useMemo(() => {
    let border = '#06B6D4'
    let content = `${index}`
    let sz = 24
    let anchor = 12

    if (isStart) {
      border = '#EF4444'
      content = '🚩'
      sz = 28
      anchor = 14
    } else if (isEnd) {
      border = '#10B981'
      content = '🏁'
      sz = 28
      anchor = 14
    }

    return L.divIcon({
      className: 'at-waypoint-pin',
      html: `
        <div style="position:relative; width:${sz}px; height:${sz}px; display:flex; align-items:center; justify-content:center;">
          <div style="width:${sz}px; height:${sz}px; border-radius:50%; background:#0a0f16; border:2px solid ${border}; display:flex; align-items:center; justify-content:center; box-shadow:0 0 10px ${border}99; color:#fff; font-size:${isStart || isEnd ? '11px' : '10px'}; font-weight:700;">
            ${content}
          </div>
        </div>
      `,
      iconSize: [sz, sz],
      iconAnchor: [anchor, anchor],
    })
  }, [wp, isStart, isEnd, index])

  return (
    <Marker position={wp.pos} icon={icon}>
      <Tooltip direction="top" offset={[0, -10]} opacity={0.95} className="at-tip">
        <div className="num text-[10px] font-bold text-cyan-300">{wp.label}</div>
        {wp.desc ? <div className="text-[9px] text-faint mt-0.5">{wp.desc}</div> : null}
      </Tooltip>
    </Marker>
  )
}

function EvacuationDirectionsDrawer({
  shelter,
  allShelters,
  onClose,
  onSelectShelter,
  onZoomRoute,
  onFocusWaypoint,
}) {
  const [copied, setCopied] = useState(false)

  if (!shelter) return null

  const handleCopy = () => {
    const text = [
      `🚨 AQUATRACE SAFE EVACUATION ROUTE DISPATCH BRIEFING`,
      `DESTINATION SANCTUARY: ${shelter.name}`,
      `TYPE: ${shelter.type} | SAFE ELEVATION: ${shelter.elev}`,
      `CAPACITY: ${shelter.cap}`,
      `ORIGIN: ${shelter.originName}`,
      `METRICS: ${shelter.distance} | ~${shelter.vehicleTime} | ~${shelter.walkTime}`,
      `SAFETY CLEARANCE: ${shelter.safetyStatus}`,
      `EMERGENCY COMMS: ${shelter.contact}`,
      ``,
      `TURN-BY-TURN ROAD DIRECTIONS:`,
      ...shelter.steps.map((st) => `${st.num}. [${st.dist}] ${st.action} on ${st.road} — ${st.instruction}`),
      ``,
      `HIMALAYAN FLOOD RAPID RESPONSE DIRECTIVE · UTTARAKHAND SDRF / NDRF`,
    ].join('\n')

    navigator.clipboard?.writeText?.(text).then(() => {
      setCopied(true)
      toast({
        title: 'Dispatch Route Copied',
        body: `Turn-by-turn directions to ${shelter.shortName} copied to clipboard.`,
      })
      setTimeout(() => setCopied(false), 2500)
    }).catch(() => {})
  }

  return (
    <div className="absolute right-3 top-3 bottom-3 z-[1000] flex w-[370px] max-w-[calc(100%-24px)] flex-col rounded-xl border border-emerald-500/40 bg-[#0a0f16]/96 p-3 backdrop-blur-xl shadow-2xl overflow-hidden text-ink animate-in fade-in slide-in-from-right-4 duration-200">
      {/* Top Header - Fixed */}
      <div className="shrink-0 flex items-center justify-between border-b border-white/10 pb-2">
        <div className="flex items-center gap-2">
          <span className="relative flex h-2.5 w-2.5">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
            <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-500"></span>
          </span>
          <div className="flex flex-col">
            <span className="text-[11px] font-bold uppercase tracking-wider text-emerald-300">
              Safe Evacuation Directions
            </span>
            <span className="num text-[8.5px] text-faint">
              Turn-by-turn navigation · High Ground Sanctuary
            </span>
          </div>
        </div>
        <button
          onClick={onClose}
          className="rounded-full p-1 text-faint hover:bg-white/10 hover:text-white transition cursor-pointer"
          aria-label="Close directions"
        >
          ✕
        </button>
      </div>

      {/* Shelter Switcher Tabs - Fixed */}
      <div className="shrink-0 mt-2 flex items-center gap-1 overflow-x-auto pb-1.5 border-b border-white/6 at-scroll">
        {allShelters.map((s) => {
          const isAct = s.id === shelter.id
          return (
            <button
              key={s.id}
              onClick={() => onSelectShelter(s.id)}
              className="shrink-0 rounded-md border px-2 py-1 text-[9px] font-medium uppercase tracking-wider transition cursor-pointer"
              style={{
                borderColor: isAct ? s.color : 'rgba(255,255,255,0.12)',
                background: isAct ? `${s.color}25` : 'rgba(255,255,255,0.03)',
                color: isAct ? '#fff' : '#8B98A9',
                boxShadow: isAct ? `0 0 8px ${s.color}44` : 'none',
              }}
            >
              🛡️ {s.shortName}
            </button>
          )
        })}
      </div>

      {/* Scrollable Body Content with visible glowing scrollbar */}
      <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain pr-1.5 py-2 space-y-2.5 at-scroll">
        {/* Sanctuary Hero Card */}
        <div
          className="rounded-lg border p-2 bg-white/3"
          style={{ borderColor: `${shelter.color}55`, borderLeftWidth: 3.5, borderLeftColor: shelter.color }}
        >
          <div className="flex items-start justify-between gap-1.5">
            <div className="min-w-0">
              <div className="text-[12px] font-bold text-white leading-snug">{shelter.name}</div>
              <div className="num text-[9.5px] text-cyan-300 font-semibold mt-0.5">{shelter.type}</div>
            </div>
            <span className="shrink-0 rounded bg-emerald-500/20 border border-emerald-500/40 px-1.5 py-0.5 text-[8.5px] font-bold text-emerald-300 uppercase">
              {shelter.elevGain}
            </span>
          </div>

          <div className="mt-1.5 flex items-center justify-between text-[8.5px] text-faint border-t border-white/6 pt-1">
            <span>Safe Elev: <strong className="text-white">{shelter.elev}</strong></span>
            <span>Cap: <strong className="text-white">{shelter.cap.split('·')[0]}</strong></span>
          </div>

          <div className="mt-1 flex flex-wrap gap-1 text-[7.5px]">
            {shelter.facilities.map((fac, idx) => (
              <span key={idx} className="rounded bg-white/5 border border-white/10 px-1 py-0.5 text-muted">
                ✓ {fac}
              </span>
            ))}
          </div>
        </div>

        {/* Transit Metrics Strip */}
        <div className="grid grid-cols-3 gap-1 rounded-lg border border-white/10 bg-white/2 p-1.5 text-center">
          <div>
            <div className="num text-[12px] font-bold text-white">{shelter.distance}</div>
            <div className="text-[7.5px] uppercase tracking-wider text-faint">Distance</div>
          </div>
          <div>
            <div className="num text-[12px] font-bold text-emerald-400">{shelter.vehicleTime.split(' ')[0]} {shelter.vehicleTime.split(' ')[1]}</div>
            <div className="text-[7.5px] uppercase tracking-wider text-faint">4x4 / Vehicle</div>
          </div>
          <div>
            <div className="num text-[12px] font-bold text-cyan-400">{shelter.walkTime.split(' ')[0]} {shelter.walkTime.split(' ')[1]}</div>
            <div className="text-[7.5px] uppercase tracking-wider text-faint">Foot Transit</div>
          </div>
        </div>

        {/* Route Origin Subtitle */}
        <div className="flex items-center justify-between text-[8.5px] px-1">
          <span className="text-faint flex items-center gap-1 truncate">
            <span className="text-red-400">🚩</span> Origin: <strong className="text-white truncate">{shelter.originName}</strong>
          </span>
          <span className="num shrink-0 text-[8px] text-emerald-400 font-semibold">SAFE CORRIDOR</span>
        </div>

        {/* Step-by-Step Directions Header */}
        <div className="flex items-center justify-between text-[9px] px-1 pt-1 border-t border-white/8">
          <span className="font-bold uppercase tracking-wider text-cyan-300">
            Road Navigation ({shelter.steps.length} Steps)
          </span>
          <span className="text-[8px] text-faint">Scroll for all turns ↓</span>
        </div>

        {/* All Steps in Scroll Flow */}
        <div className="space-y-1.5">
          {shelter.steps.map((st) => (
            <div
              key={st.num}
              onClick={() => onFocusWaypoint && onFocusWaypoint(st.num)}
              className="group rounded-md border border-white/8 bg-white/2 p-2 hover:bg-white/6 hover:border-cyan-400/40 transition cursor-pointer"
            >
              <div className="flex items-center justify-between text-[9px]">
                <span className="flex items-center gap-1.5 font-semibold text-white">
                  <span className="text-xs">{st.icon}</span>
                  <span>{st.action}</span>
                  <span className="text-faint font-normal">({st.road})</span>
                </span>
                <span className="num rounded bg-white/6 px-1.5 py-0.5 text-[8.5px] font-bold text-cyan-300">
                  {st.dist}
                </span>
              </div>
              <div className="mt-1 text-[9px] text-muted leading-relaxed pl-5 group-hover:text-white transition">
                {st.instruction}
              </div>
            </div>
          ))}
        </div>

        {/* Comms & Radio Channel */}
        <div className="rounded border border-white/8 bg-white/2 p-2 text-[8px] text-faint space-y-1">
          <div className="flex items-center justify-between">
            <span>EMERGENCY COMMS:</span>
            <strong className="text-cyan-300 font-mono">{shelter.contact}</strong>
          </div>
          <div className="flex items-center justify-between">
            <span>SURGE CLEARANCE:</span>
            <span className="num text-emerald-400 font-semibold">{shelter.safetyStatus}</span>
          </div>
        </div>
      </div>

      {/* Fixed Action Buttons Footer */}
      <div className="shrink-0 pt-2 border-t border-white/10 grid grid-cols-2 gap-1.5 bg-[#0a0f16]">
        <button
          onClick={onZoomRoute}
          className="flex items-center justify-center gap-1.5 rounded-lg border border-cyan-400/40 bg-cyan-500/15 py-1.5 text-[10px] font-semibold text-cyan-300 hover:bg-cyan-500/25 hover:border-cyan-400/70 hover:text-white transition cursor-pointer"
          title="Fit map view to entire road route"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
          <span>Zoom to Route</span>
        </button>

        <button
          onClick={handleCopy}
          className="flex items-center justify-center gap-1.5 rounded-lg border border-emerald-500/40 bg-emerald-500/15 py-1.5 text-[10px] font-semibold text-emerald-300 hover:bg-emerald-500/25 hover:border-emerald-400/70 hover:text-white transition cursor-pointer"
          title="Copy turn-by-turn route dispatch order"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
            <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
          </svg>
          <span>{copied ? 'Copied ✓' : 'Copy Briefing'}</span>
        </button>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ drawer

function Sparkline({ cid }) {
  const [points, setPoints] = useState([])

  useEffect(() => {
    let alive = true
    const load = () => {
      get(`/telemetry/${cid}?window=5m`)
        .then((d) => { if (alive) setPoints(d?.points || []) })
        .catch(() => {})
    }
    load()
    const id = setInterval(load, 5000)
    return () => { alive = false; clearInterval(id) }
  }, [cid])

  const ratios = points.map((p) => p.water_area_ratio).filter(Number.isFinite)
  if (ratios.length < 2) {
    return <div className="num py-3 text-center text-[9px] text-faint">awaiting telemetry…</div>
  }
  const min = Math.min(...ratios)
  const max = Math.max(...ratios)
  const span = max - min || 1
  const d = ratios
    .map((v, i) => `${i === 0 ? 'M' : 'L'}${((i / (ratios.length - 1)) * 100).toFixed(2)},${(30 - ((v - min) / span) * 28 - 1).toFixed(2)}`)
    .join(' ')
  return (
    <div>
      <div className="flex items-center justify-between text-[9px] text-faint">
        <span>5-MIN WATER AREA RATIO</span>
        <span className="num font-semibold text-ink">{(ratios[ratios.length - 1] * 100).toFixed(1)}%</span>
      </div>
      <svg viewBox="0 0 100 32" className="mt-1 h-8 w-full overflow-visible" preserveAspectRatio="none">
        <path d={d} fill="none" stroke="#22D3EE" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>
  )
}

function Drawer({ camera, forecast, alerts, onClose, onSelect }) {
  const cid = cameraId(camera)
  const st = stateOf(camera)
  const color = STATE_FILL[st] || STATE_FILL.normal
  const camAlerts = (alerts || []).filter((a) => a.camera_id === cid).slice(0, 3)
  const zoneForecast = forecast?.zones?.find((z) => z.zone === camera.zone)

  return (
    <div className="absolute right-0 top-0 z-[1000] flex h-full w-[310px] flex-col border-l border-white/10 bg-[#0a0f16]/96 p-4 backdrop-blur shadow-2xl overflow-y-auto">
      <div className="flex items-center justify-between border-b border-white/8 pb-3">
        <div className="min-w-0">
          <div className="truncate text-[12px] font-semibold text-ink">{camera.name || cid}</div>
          <div className="num mt-0.5 text-[10px] text-faint">{camera.zone || 'Upper Ganga Reach'} · {cid}</div>
        </div>
        <button
          onClick={onClose}
          className="rounded p-1 text-faint transition hover:bg-white/6 hover:text-ink cursor-pointer"
          aria-label="Close details"
        >
          ✕
        </button>
      </div>

      <div className="mt-3 flex items-center justify-between">
        <StateChip state={st} />
        <span className="num text-[10px] text-faint">{camera.lat?.toFixed(4)}°N {camera.lng?.toFixed(4)}°E</span>
      </div>

      <div className="mt-4 rounded border border-white/8 bg-white/2 p-2.5">
        <Sparkline cid={cid} />
      </div>

      {zoneForecast ? (
        <div className="mt-3 rounded border border-white/8 bg-white/2 p-2.5">
          <div className="flex items-center justify-between text-[9px] uppercase tracking-[0.14em] text-faint">
            <span>Zone Rain Outlook</span>
            <span className="num text-live">{zoneForecast.status === 'ok' ? 'Open-Meteo' : '—'}</span>
          </div>
          <div className="mt-1 flex items-baseline justify-between">
            <span className="num text-[14px] font-medium text-ink">
              {zoneForecast.prob_max_6h != null ? `${zoneForecast.prob_max_6h}%` : '—'}
            </span>
            <span className="num text-[10px] text-muted">
              {zoneForecast.mm_6h != null ? `${zoneForecast.mm_6h} mm / 6h` : '—'}
            </span>
          </div>
          {zoneForecast.advisory ? (
            <div className="mt-1 text-[9px] leading-relaxed text-faint">{zoneForecast.advisory}</div>
          ) : null}
        </div>
      ) : null}

      <div className="mt-3">
        <div className="text-[9px] uppercase tracking-[0.14em] text-faint">Adjacent Sensor Nodes</div>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {(camera.adjacent || []).map((adj) => (
            <button
              key={adj}
              onClick={() => onSelect(adj)}
              className="num rounded border border-white/10 bg-white/3 px-2 py-0.5 text-[10px] text-muted transition hover:border-live/40 hover:text-live cursor-pointer"
            >
              {adj}
            </button>
          ))}
        </div>
      </div>

      {camAlerts.length ? (
        <div className="mt-4">
          <div className="text-[9px] uppercase tracking-[0.14em] text-faint">Recent Incidents</div>
          <div className="mt-1.5 flex flex-col gap-1.5">
            {camAlerts.map((a) => (
              <div key={a.alert_id} className="rounded border border-white/8 bg-white/2 p-2 text-[10px]">
                <div className="flex items-center justify-between">
                  <span className="num font-semibold text-ink">#{a.alert_id}</span>
                  <span className="num text-faint">{fmtTime(a.triggered_at)}</span>
                </div>
                <div className="mt-0.5 text-faint">{a.reasoning_summary || a.state}</div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}

// ------------------------------------------------------------- map lifecycle

function MapLifecycle({ bounds, onMapInstance }) {
  const map = useMap()
  const fittedRef = useRef(false)

  useEffect(() => {
    if (onMapInstance) onMapInstance(map)
    map.invalidateSize()
    const timer = setTimeout(() => {
      map.invalidateSize()
      if (bounds && bounds.length > 0 && !fittedRef.current) {
        try {
          map.fitBounds(bounds, { padding: [55, 55], maxZoom: 15 })
          fittedRef.current = true
        } catch {
          /* ignore */
        }
      }
    }, 200)
    return () => clearTimeout(timer)
  }, [map, bounds, onMapInstance])

  return null
}

// ---------------------------------------------------------------- panel

export default function MapPanel({ isExpanded = false }) {
  const cameras = useStore((s) => s.cameras)
  const selected = useStore((s) => s.selected)
  const site = useStore((s) => s.site)
  const alerts = useStore((s) => s.alerts)
  const forecast = useStore((s) => s.forecast)

  // Expand state & Evacuation state from store
  const storeExpanded = useStore((s) => s.mapExpanded)
  const showEvacuationRoutes = useStore((s) => s.showEvacuationRoutes)
  const evacFocusAlert = useStore((s) => s.evacFocusAlert)
  const selectedShelterId = useStore((s) => s.selectedShelterId)
  const expanded = isExpanded || storeExpanded

  const selectedShelter = useMemo(
    () => EVACUATION_SHELTERS.find((s) => s.id === selectedShelterId) || null,
    [selectedShelterId],
  )

  // Default to OpenStreetMap Standard
  const [theme, setTheme] = useState('osm') // 'osm' | 'hot' | 'topo' | 'satellite'
  const [drawer, setDrawer] = useState(false)
  const [mapReady, setMapReady] = useState(false)
  const [showForecastMenu, setShowForecastMenu] = useState(false)
  const mapInstanceRef = useRef(null)

  const handleShelterSelect = useCallback((id) => {
    setDrawer(false) // Close camera sensor drawer to give full view to route directions
    storeActions.selectShelter(id)
    const shelter = EVACUATION_SHELTERS.find((s) => s.id === id)
    if (shelter && mapInstanceRef.current && shelter.positions) {
      try {
        mapInstanceRef.current.fitBounds(shelter.positions, { padding: [65, 65], maxZoom: 16 })
      } catch {
        /* ignore */
      }
    }
  }, [])

  const [floodHistoryLayer, setFloodHistoryLayer] = useState(null)
  const [showFloodHistory, setShowFloodHistory] = useState(true)

  // Fetch GEE flood history & DEM export
  useEffect(() => {
    let alive = true
    const checkExport = async () => {
      try {
        const res = await get('/api/layers/flood-history')
        if (res?.ready && res?.layer && alive) {
          setFloodHistoryLayer(res.layer)
          return
        }
      } catch {
        /* fallback to public static */
      }
      try {
        const r = await fetch('/flood_history_export.json')
        if (r.ok && alive) {
          const data = await r.json()
          if (data?.features?.length && alive) {
            setFloodHistoryLayer(data)
          }
        }
      } catch {
        /* export file not present */
      }
    }
    checkExport()
  }, [])

  const geo = useMemo(
    () => cameras.filter((c) => Number.isFinite(c.lat) && Number.isFinite(c.lng)),
    [cameras],
  )

  const defaultCenter = useMemo(() => {
    const c = site?.center
    if (Array.isArray(c) && c.length >= 2 && Number.isFinite(c[0]) && Number.isFinite(c[1])) {
      return [c[0], c[1]]
    }
    return [30.1030, 78.2970]
  }, [site])

  const centerLabel = useMemo(() => {
    return `${defaultCenter[0].toFixed(2)}°N ${defaultCenter[1].toFixed(2)}°E · Uttarakhand`
  }, [defaultCenter])

  const bounds = useMemo(() => (geo.length ? geo.map((c) => [c.lat, c.lng]) : null), [geo])

  const select = (cid) => {
    storeActions.select(cid)
    setDrawer(true)
  }

  const selectedCam = geo.find((c) => cameraId(c) === selected)

  const links = useMemo(() => {
    const byId = new Map(geo.map((p) => [cameraId(p), p]))
    const out = []
    for (const p of geo) {
      const pid = cameraId(p)
      for (const adj of p.adjacent || []) {
        const q = byId.get(adj)
        if (!q || pid >= cameraId(q)) continue
        const ps = stateOf(p)
        const qs = stateOf(q)
        const worse = (RANK[ps] ?? 0) >= (RANK[qs] ?? 0) ? ps : qs
        out.push({
          id: `${pid}-${cameraId(q)}`,
          a: p,
          b: q,
          alerting: worse !== 'normal' && worse !== 'signal_lost',
          color: STATE_FILL[worse] || '#8B98A9',
        })
      }
    }
    return out
  }, [geo])

  const activeTheme = MAP_THEMES[theme] || MAP_THEMES.osm

  const recenter = useCallback(() => {
    if (mapInstanceRef.current) {
      try {
        if (bounds && bounds.length > 0) {
          mapInstanceRef.current.fitBounds(bounds, { padding: [60, 60], maxZoom: 15 })
        } else {
          mapInstanceRef.current.setView(defaultCenter, 14)
        }
      } catch {
        /* ignore */
      }
    }
  }, [bounds, defaultCenter])

  // Resize leaflet map on expand toggle
  useEffect(() => {
    if (mapInstanceRef.current) {
      const timer = setTimeout(() => {
        mapInstanceRef.current.invalidateSize()
      }, 100)
      return () => clearTimeout(timer)
    }
  }, [expanded])

  const legend = (
    <div className="flex items-center gap-2">
      {/* Base map theme dropdown selector */}
      <div className="relative flex items-center">
        <select
          value={theme}
          onChange={(e) => setTheme(e.target.value)}
          className="appearance-none rounded border border-white/14 bg-[#0a0f16]/90 px-2 py-0.5 pr-5 text-[9px] font-semibold uppercase tracking-wider text-cyan-300 hover:border-cyan-400/50 hover:bg-white/8 transition cursor-pointer shadow-sm focus:outline-none"
          title="Select base map style"
        >
          {Object.entries(MAP_THEMES).map(([key, t]) => (
            <option key={key} value={key} className="bg-[#0a0f16] text-white">
              🗺️ {t.label}
            </option>
          ))}
        </select>
        <span className="pointer-events-none absolute right-1.5 top-1/2 -translate-y-1/2 text-[7px] text-cyan-300">
          ▼
        </span>
      </div>

      {/* Rain Outlook Dropdown (replaces horizontal strip) */}
      <div className="relative">
        <button
          onClick={() => setShowForecastMenu((v) => !v)}
          className="flex items-center gap-1 rounded border border-white/14 bg-[#0a0f16]/90 px-2 py-0.5 text-[9px] font-medium text-amber-300 hover:border-amber-400/50 hover:bg-amber-500/10 transition cursor-pointer shadow-sm"
          title="Click to view per-zone precipitation forecast"
        >
          <span className="text-[10px]">🌧️</span>
          <span className="num font-semibold">
            {forecast?.zones?.[0]?.prob_max_6h != null ? `RAIN ${forecast.zones[0].prob_max_6h}%` : 'RAIN'}
          </span>
          <span className="text-[7px] text-muted">▼</span>
        </button>

        {showForecastMenu && (
          <div className="absolute right-0 top-full mt-1.5 z-[1200] w-64 rounded-lg border border-amber-500/35 bg-[#0a0f16]/98 p-2.5 shadow-2xl backdrop-blur-md text-[9px] animate-in fade-in duration-150">
            <div className="flex items-center justify-between border-b border-white/10 pb-1 font-semibold text-amber-300 uppercase tracking-wider">
              <span>Rain Outlook</span>
              <span className="num text-[8px] text-faint">{forecast?.source || 'Open-Meteo'}</span>
            </div>
            <div className="mt-1.5 space-y-1">
              {forecast?.zones?.length ? (
                forecast.zones.map((z) => (
                  <div key={z.zone} className="flex items-center justify-between text-muted py-0.5">
                    <span className="text-white truncate">{z.zone}</span>
                    <span className="num text-cyan-300 font-semibold shrink-0 ml-2">
                      {z.prob_max_6h ?? '—'}% · {z.mm_6h ?? '—'} mm/6h
                    </span>
                  </div>
                ))
              ) : (
                <div className="text-faint py-1">Forecast loading…</div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* GEE Flood History & DEM layer toggle */}
      {floodHistoryLayer ? (
        <button
          onClick={() => setShowFloodHistory((v) => !v)}
          className="flex items-center gap-1.5 rounded border px-2 py-0.5 text-[9px] uppercase tracking-[0.1em] font-medium transition cursor-pointer"
          style={{
            borderColor: showFloodHistory ? '#06B6D4' : 'rgba(255,255,255,0.14)',
            background: showFloodHistory ? 'rgba(6,182,212,0.22)' : 'transparent',
            color: showFloodHistory ? '#22D3EE' : '#8B98A9',
          }}
          title="Toggle Google Earth Engine DEM elevation contours, HydroSHEDS river channels & historical flood inundation footprints"
        >
          <span
            className="h-1.5 w-1.5 rounded-full"
            style={{ background: showFloodHistory ? '#22D3EE' : '#5A6B80' }}
          />
          FLOOD HISTORY & DEM
        </button>
      ) : null}

      {/* Evacuation Routes & Shelters Toggle Button */}
      <button
        onClick={() => {
          const next = !showEvacuationRoutes
          storeActions.setShowEvacuationRoutes(next)
          if (!next) {
            storeActions.clearShelter()
          } else if (!selectedShelterId) {
            handleShelterSelect('shelter-aiims')
          }
        }}
        className="flex items-center gap-1.5 rounded border px-2 py-0.5 text-[9px] uppercase tracking-[0.1em] font-medium transition cursor-pointer"
        style={{
          borderColor: showEvacuationRoutes ? '#10B981' : 'rgba(255,255,255,0.14)',
          background: showEvacuationRoutes ? 'rgba(16,185,129,0.22)' : 'transparent',
          color: showEvacuationRoutes ? '#34D399' : '#8B98A9',
        }}
        title="Toggle Himalayan flash flood emergency evacuation corridors & safe shelters"
      >
        <span
          className="h-1.5 w-1.5 rounded-full"
          style={{ background: showEvacuationRoutes ? '#34D399' : '#5A6B80' }}
        />
        EVACUATION ROUTES & CENTERS
      </button>

      {/* Small Expand/Collapse Symbol Button (Show only the map) */}
      <button
        onClick={() => storeActions.toggleMapExpand()}
        className="flex items-center justify-center h-6 w-6 rounded border border-white/14 bg-white/4 text-muted hover:text-cyan-300 hover:border-cyan-400/60 hover:bg-cyan-500/15 transition cursor-pointer ml-1"
        title={expanded ? "Collapse to multi-panel console view" : "Show only the map (fullscreen view)"}
        aria-label={expanded ? "Collapse map" : "Expand map"}
      >
        {expanded ? (
          // Inward arrows (minimize/collapse)
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 14h6v6M20 10h-6V4M14 10l7-7M10 14l-7 7" />
          </svg>
        ) : (
          // Outward arrows (expand/maximize)
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
            <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
          </svg>
        )}
      </button>
    </div>
  )

  return (
    <Panel className="relative flex min-h-[300px] flex-1 flex-col overflow-hidden">
      <PanelHeader
        title="uttarakhand upper ganga basin live map"
        sub={`${centerLabel} · rishikesh — haridwar floodplain corridor`}
        right={legend}
      />

      <div className="relative min-h-0 flex-1">
        <MapContainer
          center={defaultCenter}
          zoom={14}
          zoomControl
          attributionControl
          scrollWheelZoom
          className="absolute inset-0 h-full w-full"
          style={{ background: '#0a0f16' }}
          whenReady={() => setMapReady(true)}
        >
          <MapLifecycle
            bounds={bounds}
            onMapInstance={(inst) => { mapInstanceRef.current = inst }}
          />
          
          <TileLayer
            key={activeTheme.id}
            url={activeTheme.url}
            subdomains={activeTheme.subdomains}
            attribution={activeTheme.attr}
            maxZoom={activeTheme.maxZoom}
            className={activeTheme.cssClass || 'at-tiles'}
          />

          {/* Render Vector Layers safely once map has initialized dimensions */}
          {mapReady ? (
            <>
              {/* --- GEE DEM elevation contours --- */}
              {showFloodHistory && floodHistoryLayer?.features
                ?.filter((f) => f.properties?.layer === 'dem_contour')
                .map((c, i) => {
                  const pts = extractPolylineCoords(c)
                  if (!pts || pts.length < 2) return null
                  return (
                    <Polyline
                      key={`dem-${i}-${c.properties?.elevation_m ?? i}`}
                      positions={pts}
                      pathOptions={{
                        color: c.properties?.color || '#38BDF8',
                        weight: 1.5,
                        dashArray: '4 4',
                        opacity: 0.85,
                      }}
                    >
                      <Tooltip direction="top" opacity={0.95} className="at-tip">
                        <div className="text-[9px] text-faint uppercase tracking-wider">COPERNICUS GLO-30 DEM</div>
                        <div className="num text-[11px] text-cyan-300 font-semibold">{c.properties?.label}</div>
                      </Tooltip>
                    </Polyline>
                  )
                })}

              {/* --- GEE WWF/HydroSHEDS river channels --- */}
              {showFloodHistory && floodHistoryLayer?.features
                ?.filter((f) => f.properties?.layer === 'hydrosheds_river')
                .map((r, i) => {
                  const pts = extractPolylineCoords(r)
                  if (!pts || pts.length < 2) return null
                  return (
                    <Polyline
                      key={`river-${i}-${r.properties?.name ?? i}`}
                      positions={pts}
                      pathOptions={{
                        color: r.properties?.color || '#06B6D4',
                        weight: (r.properties?.stream_order ?? 1) >= 4 ? 4 : 2.5,
                        opacity: 0.95,
                      }}
                    >
                      <Tooltip direction="right" opacity={1} className="at-tip">
                        <div className="at-tip-title text-cyan-400 font-semibold">{r.properties?.name}</div>
                        <div className="text-[10px] text-faint">HydroSHEDS Flow: {r.properties?.flow_direction}</div>
                        <div className="num text-[9px] text-muted">
                          Channel Width ~{r.properties?.width_m}m · Order {r.properties?.stream_order}
                        </div>
                      </Tooltip>
                    </Polyline>
                  )
                })}

              {/* --- Historical flood inundation extent --- */}
              {showFloodHistory && floodHistoryLayer?.features
                ?.filter((f) => f.properties?.layer === 'flood_history_polygon')
                .map((z, i) => {
                  const pts = extractPolygonCoords(z)
                  if (!pts || pts.length < 3) return null
                  return (
                    <Polygon
                      key={`flood-hist-${i}-${z.properties?.name ?? i}`}
                      positions={pts}
                      pathOptions={{
                        color: z.properties?.stroke || '#06B6D4',
                        fillColor: z.properties?.stroke || '#06B6D4',
                        fillOpacity: 0.22,
                        weight: 2,
                        dashArray: '5 6',
                      }}
                    >
                      <Tooltip direction="top" opacity={1} className="at-tip">
                        <div className="at-tip-title font-semibold text-cyan-300">{z.properties?.name}</div>
                        <div className="text-[10px] text-faint">{z.properties?.event}</div>
                        <div className="num text-[9px] text-live">{z.properties?.risk_level}</div>
                      </Tooltip>
                    </Polygon>
                  )
                })}

              {/* --- EMERGENCY EVACUATION ROUTES & SAFE SHELTERS --- */}
              {showEvacuationRoutes && (
                <>
                  {/* Subtle background non-selected corridors */}
                  {EVACUATION_ROUTES.filter((r) => !selectedShelter || r.shelter_id !== selectedShelter.id).map((route) => {
                    const isFocus = evacFocusAlert && evacFocusAlert.camera_id === route.camera_id
                    return (
                      <Polyline
                        key={route.id}
                        positions={route.positions}
                        pathOptions={{
                          color: route.color,
                          weight: isFocus ? 4.5 : 3,
                          opacity: isFocus ? 0.85 : 0.55,
                          dashArray: '6 8',
                        }}
                        eventHandlers={{
                          click: () => handleShelterSelect(route.shelter_id),
                        }}
                      >
                        <Tooltip direction="top" opacity={1} className="at-tip">
                          <div className="at-tip-title font-semibold" style={{ color: route.color }}>
                            🚶 {route.name}
                          </div>
                          <div className="text-[10px] text-white">Safe Assembly: {route.dest}</div>
                          <div className="num text-[9px] text-emerald-300 font-semibold mt-0.5">
                            {route.distance} · {route.elev_gain}
                          </div>
                          <div className="text-[9px] text-cyan-300 font-semibold mt-0.5">➔ CLICK FOR TURN-BY-TURN DIRECTIONS</div>
                        </Tooltip>
                      </Polyline>
                    )
                  })}

                  {/* Active Selected Evacuation Route with high-contrast road casing and glow */}
                  {selectedShelter && (
                    <>
                      {/* Glow underlay */}
                      <Polyline
                        positions={selectedShelter.positions}
                        pathOptions={{
                          color: selectedShelter.color,
                          weight: 12,
                          opacity: 0.38,
                          lineCap: 'round',
                          lineJoin: 'round',
                        }}
                      />
                      {/* High-contrast solid road casing */}
                      <Polyline
                        positions={selectedShelter.positions}
                        pathOptions={{
                          color: '#ffffff',
                          weight: 5.5,
                          opacity: 0.95,
                          lineCap: 'round',
                          lineJoin: 'round',
                        }}
                      />
                      {/* Core highlighted route */}
                      <Polyline
                        positions={selectedShelter.positions}
                        pathOptions={{
                          color: selectedShelter.color,
                          weight: 3.8,
                          opacity: 1,
                          dashArray: '8 6',
                          lineCap: 'round',
                          lineJoin: 'round',
                        }}
                      >
                        <Tooltip direction="top" opacity={1} className="at-tip">
                          <div className="at-tip-title font-semibold text-emerald-300">
                            🛡️ ACTIVE ROUTE: {selectedShelter.originName.split(' ')[0]} → {selectedShelter.shortName}
                          </div>
                          <div className="num text-[9px] text-cyan-300">
                            {selectedShelter.distance} · {selectedShelter.vehicleTime} · {selectedShelter.elevGain}
                          </div>
                          <div className="text-[9px] text-white">All roads safe and elevated above flood level</div>
                        </Tooltip>
                      </Polyline>

                      {/* Waypoint pins along the active navigation route */}
                      {selectedShelter.waypoints?.map((wp, idx) => (
                        <WaypointPin
                          key={`wp-${selectedShelter.id}-${idx}`}
                          wp={wp}
                          isStart={wp.type === 'start'}
                          isEnd={wp.type === 'end'}
                          index={idx}
                        />
                      ))}
                    </>
                  )}

                  {/* Evacuation Shelters & Relief Sanctuaries */}
                  {EVACUATION_SHELTERS.map((s) => (
                    <ShelterPin
                      key={s.id}
                      s={s}
                      isSelected={selectedShelterId === s.id}
                      onSelect={handleShelterSelect}
                    />
                  ))}
                </>
              )}

              {/* Camera FOV Wedges */}
              {geo.map((c) => {
                const cid = cameraId(c)
                const st = stateOf(c)
                const color = STATE_FILL[st] || STATE_FILL.normal
                const isSel = selected === cid
                const fov = fovPoints(c)
                if (!fov || fov.length < 3) return null
                return (
                  <Polygon
                    key={`fov-${cid}`}
                    positions={fov}
                    pathOptions={{
                      color,
                      weight: 1.2,
                      fillColor: color,
                      fillOpacity: isSel ? 0.22 : 0.09,
                      dashArray: '3 5',
                    }}
                    eventHandlers={{ click: () => select(cid) }}
                  />
                )
              })}

              {/* Inter-camera adjacency links */}
              {links.map((l) => {
                if (
                  !Number.isFinite(l.a.lat) ||
                  !Number.isFinite(l.a.lng) ||
                  !Number.isFinite(l.b.lat) ||
                  !Number.isFinite(l.b.lng)
                ) {
                  return null
                }
                return (
                  <Polyline
                    key={`link-${l.id}`}
                    positions={[[l.a.lat, l.a.lng], [l.b.lat, l.b.lng]]}
                    pathOptions={{
                      color: l.alerting ? l.color : 'rgba(255,255,255,.3)',
                      weight: l.alerting ? 2.5 : 1.2,
                      dashArray: '6 8',
                      opacity: l.alerting ? 0.95 : 0.65,
                    }}
                  />
                )
              })}

              {/* High-visibility HTML Camera Pins */}
              {geo.map((c) => (
                <CameraPin
                  key={`pin-${cameraId(c)}`}
                  c={c}
                  isSel={selected === cameraId(c)}
                  onSelect={() => select(cameraId(c))}
                />
              ))}
            </>
          ) : null}
        </MapContainer>

        {drawer && selectedCam ? (
          <Drawer
            camera={selectedCam}
            forecast={forecast}
            alerts={alerts}
            onClose={() => setDrawer(false)}
            onSelect={(cid) => storeActions.select(cid)}
          />
        ) : null}

        {/* Turn-by-Turn Safe Evacuation Route Directions Drawer */}
        {selectedShelter ? (
          <EvacuationDirectionsDrawer
            shelter={selectedShelter}
            allShelters={EVACUATION_SHELTERS}
            onClose={() => storeActions.clearShelter()}
            onSelectShelter={handleShelterSelect}
            onZoomRoute={() => {
              if (mapInstanceRef.current && selectedShelter.positions) {
                try {
                  mapInstanceRef.current.fitBounds(selectedShelter.positions, { padding: [65, 65], maxZoom: 16 })
                } catch {
                  /* ignore */
                }
              }
            }}
            onFocusWaypoint={(stepNum) => {
              if (mapInstanceRef.current && selectedShelter.waypoints) {
                const wp = selectedShelter.waypoints[Math.min(stepNum - 1, selectedShelter.waypoints.length - 1)]
                if (wp?.pos) {
                  mapInstanceRef.current.setView(wp.pos, 16, { animate: true })
                }
              }
            }}
          />
        ) : null}

        {/* Expanded Map Header Banner */}
        {expanded ? (
          <div className="absolute top-3 left-1/2 -translate-x-1/2 z-[1000] flex items-center gap-3 rounded-full border border-white/16 bg-[#0a0f16]/95 px-4 py-1.5 backdrop-blur-md shadow-2xl">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyan-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-cyan-500"></span>
            </span>
            <span className="num text-[11px] font-semibold tracking-wide text-white uppercase">
              Expanded Fullscreen Map · {activeTheme.label}
            </span>
            {showEvacuationRoutes ? (
              <span className="rounded bg-emerald-500/20 border border-emerald-500/40 px-2 py-0.5 text-[9px] font-semibold text-emerald-300 uppercase tracking-wider">
                Evacuation Corridors Active
              </span>
            ) : null}
            <button
              onClick={() => storeActions.setMapExpanded(false)}
              className="flex items-center gap-1 rounded-full border border-white/14 bg-white/6 px-2.5 py-0.5 text-[10px] font-medium text-muted hover:text-white hover:bg-white/12 hover:border-white/30 transition cursor-pointer"
              title="Return to standard 3-column operator console"
            >
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path d="M4 14h6v6M20 10h-6V4M14 10l7-7M10 14l-7 7" />
              </svg>
              <span>Collapse Map</span>
            </button>
          </div>
        ) : null}

        {/* Evacuation Centers & Corridors Floating Legend & Status Panel */}
        {showEvacuationRoutes ? (
          <div className="absolute top-12 left-3 z-[900] max-w-[340px] rounded-lg border border-emerald-500/35 bg-[#0a0f16]/95 p-3 backdrop-blur-md shadow-2xl text-[10px]">
            <div className="flex items-center justify-between border-b border-white/10 pb-1.5">
              <span className="font-semibold text-emerald-400 uppercase tracking-wider flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
                Safe Evacuation Centers
              </span>
              <button
                onClick={() => {
                  storeActions.setShowEvacuationRoutes(false)
                  storeActions.clearShelter()
                }}
                className="text-faint hover:text-white cursor-pointer px-1"
                title="Hide evacuation routes"
              >
                ✕
              </button>
            </div>
            <div className="mt-1 text-[9px] text-faint">
              👉 Click any center to view safe road navigation route
            </div>
            {evacFocusAlert ? (
              <div className="mt-1.5 rounded bg-amber-500/10 border border-amber-500/30 px-2 py-1 text-[9px] text-amber-200">
                Focused Hazard: <strong className="text-white">{evacFocusAlert.camera_id}</strong> · {evacFocusAlert.severity?.toUpperCase()}
              </div>
            ) : null}
            <div className="mt-2 space-y-1.5">
              {EVACUATION_SHELTERS.map((s) => {
                const isSel = selectedShelterId === s.id
                return (
                  <div
                    key={s.id}
                    onClick={() => handleShelterSelect(s.id)}
                    className="rounded border p-2 transition cursor-pointer"
                    style={{
                      borderColor: isSel ? '#22D3EE' : 'rgba(255,255,255,0.08)',
                      borderLeftColor: s.color,
                      borderLeftWidth: 3.5,
                      background: isSel ? 'rgba(34,211,238,0.12)' : 'rgba(255,255,255,0.02)',
                      boxShadow: isSel ? '0 0 12px rgba(34,211,238,0.2)' : 'none',
                    }}
                  >
                    <div className="font-semibold text-white truncate flex items-center justify-between">
                      <span className="flex items-center gap-1 truncate">
                        <span>🛡️ {s.shortName}</span>
                      </span>
                      <span className="num shrink-0 text-[8px] font-bold" style={{ color: isSel ? '#22D3EE' : '#10B981' }}>
                        {isSel ? 'ACTIVE ➔' : s.distance}
                      </span>
                    </div>
                    <div className="text-[9px] text-muted truncate mt-0.5">{s.name}</div>
                    <div className="flex items-center justify-between text-[8.5px] mt-1 text-faint">
                      <span className="text-emerald-300 font-semibold">{s.elevGain}</span>
                      <span className="num text-cyan-300">{s.vehicleTime}</span>
                    </div>
                    <div className="flex items-center justify-between text-[8px] text-faint mt-0.5">
                      <span className="text-faint">{s.elev.split('(')[0].trim()}</span>
                      <span className="num text-muted">{s.cap.split('·')[0].trim()}</span>
                    </div>
                  </div>
                )
              })}
            </div>
            <div className="mt-2 pt-1.5 border-t border-white/8 text-[8px] text-faint flex items-center justify-between">
              <span>4 High-Ground Hubs Clear</span>
              <span className="num text-emerald-400 font-medium">NDRF / SDRF Ready</span>
            </div>
          </div>
        ) : null}

        {/* Real Place Geographic Info HUD */}
        <div className="absolute bottom-2.5 left-3 z-[900] flex flex-wrap items-center gap-2 rounded-md border border-white/12 bg-[#0a0f16]/92 px-3 py-1.5 backdrop-blur-md shadow-xl">
          <span className="relative inline-block h-2 w-2 rounded-full bg-live animate-pulse" />
          <div className="flex flex-col">
            <span className="text-[10px] font-semibold tracking-wide text-white">
              UTTARAKHAND, IN · UPPER GANGA & ALAKNANDA BASIN
            </span>
            <span className="num text-[8px] text-faint">
              {defaultCenter[0].toFixed(4)}°N {defaultCenter[1].toFixed(4)}°E · {activeTheme.label.toUpperCase()} · 335m–620m AMSL
            </span>
          </div>

          {floodHistoryLayer && showFloodHistory ? (
            <span className="num rounded bg-cyan-500/15 border border-cyan-500/40 px-1.5 py-0.5 text-[8px] text-cyan-300 uppercase tracking-wider font-semibold">
              GEE: COPERNICUS DEM + HydroSHEDS
            </span>
          ) : null}

          <button
            onClick={recenter}
            className="rounded border border-white/10 bg-white/4 px-1.5 py-0.5 text-[8px] uppercase tracking-wider text-muted hover:border-live/40 hover:text-live transition cursor-pointer"
            title="Recenter view on Ganga river reach"
          >
            ⌖ Recenter Ganga Reach
          </button>
        </div>
      </div>
    </Panel>
  )
}

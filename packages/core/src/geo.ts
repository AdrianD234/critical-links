/**
 * Geodesy for NZTM2000 (EPSG:2193) <-> WGS84 (EPSG:4326).
 *
 * All analytical distance work happens in EPSG:2193 metres. WGS84 is produced
 * only for web delivery (GeoJSON / MapLibre).
 *
 * Implementation is the Redfearn series for Transverse Mercator, with the
 * NZTM2000 parameters published by LINZ. Accuracy is verified in
 * tests/unit/geo.test.ts against coordinates reprojected by the ArcGIS service
 * itself, so the projection is checked against an independent implementation
 * rather than against itself.
 *
 * Known limitation: grid distance in a Transverse Mercator projection differs
 * from ground distance by the point scale factor (0.9996 on the central
 * meridian, rising to roughly 1.0006 at the east/west extremes of New Zealand).
 * The worst-case error is about 0.06%. Detour RATIOS are essentially unaffected
 * because numerator and denominator carry the same distortion. This is recorded
 * in docs/KNOWN_LIMITATIONS.md.
 */

// --- NZTM2000 parameters (LINZ) ---------------------------------------------
const a = 6378137.0; // GRS80 semi-major axis
const f = 1 / 298.257222101; // GRS80 flattening
const phi0 = 0.0; // origin latitude (radians)
const lambda0 = (173.0 * Math.PI) / 180.0; // central meridian
const N0 = 10000000.0; // false northing
const E0 = 1600000.0; // false easting
const k0 = 0.9996; // central meridian scale factor

const e2 = 2 * f - f * f;
const n = f / (2 - f);
const n2 = n * n;
const n3 = n2 * n;
const n4 = n3 * n;

const A0 = 1 - e2 / 4 - (3 * e2 * e2) / 64 - (5 * e2 * e2 * e2) / 256;
const A2 = (3 / 8) * (e2 + (e2 * e2) / 4 + (15 * e2 * e2 * e2) / 128);
const A4 = (15 / 256) * (e2 * e2 + (3 * e2 * e2 * e2) / 4);
const A6 = (35 * e2 * e2 * e2) / 3072;

/** Meridian arc distance from the equator to latitude `phi` (radians). */
function meridianArc(phi: number): number {
  return (
    a *
    (A0 * phi -
      A2 * Math.sin(2 * phi) +
      A4 * Math.sin(4 * phi) -
      A6 * Math.sin(6 * phi))
  );
}

const m0 = meridianArc(phi0);

/** metres per degree of meridian arc, used for the footpoint latitude series */
const G =
  a * (1 - n) * (1 - n2) * (1 + (9 * n2) / 4 + (225 * n4) / 64) * (Math.PI / 180);

export interface LatLon {
  lat: number;
  lon: number;
}
export interface Nztm {
  x: number;
  y: number;
}

/** WGS84 (degrees) -> NZTM2000 (metres). */
export function latLonToNztm(lat: number, lon: number): Nztm {
  const phi = (lat * Math.PI) / 180;
  const lambda = (lon * Math.PI) / 180;

  const sinPhi = Math.sin(phi);
  const cosPhi = Math.cos(phi);
  const t = Math.tan(phi);
  const t2 = t * t;
  const t4 = t2 * t2;
  const t6 = t4 * t2;

  const w = 1 - e2 * sinPhi * sinPhi;
  const rho = (a * (1 - e2)) / Math.pow(w, 1.5);
  const nu = a / Math.sqrt(w);
  const psi = nu / rho;
  const psi2 = psi * psi;
  const psi3 = psi2 * psi;
  const psi4 = psi3 * psi;

  const om = lambda - lambda0;
  const om2 = om * om;
  const om4 = om2 * om2;
  const om6 = om4 * om2;
  const om8 = om4 * om4;

  const m = meridianArc(phi);

  const T1 = (om2 / 2) * nu * sinPhi * cosPhi;
  const T2 =
    (om4 / 24) * nu * sinPhi * Math.pow(cosPhi, 3) * (4 * psi2 + psi - t2);
  const T3 =
    (om6 / 720) *
    nu *
    sinPhi *
    Math.pow(cosPhi, 5) *
    (8 * psi4 * (11 - 24 * t2) -
      28 * psi3 * (1 - 6 * t2) +
      psi2 * (1 - 32 * t2) -
      psi * 2 * t2 +
      t4);
  const T4 =
    (om8 / 40320) *
    nu *
    sinPhi *
    Math.pow(cosPhi, 7) *
    (1385 - 3111 * t2 + 543 * t4 - t6);

  const y = N0 + k0 * (m - m0 + T1 + T2 + T3 + T4);

  const T5 = (om2 / 6) * cosPhi * cosPhi * (psi - t2);
  const T6 =
    (om4 / 120) *
    Math.pow(cosPhi, 4) *
    (4 * psi3 * (1 - 6 * t2) + psi2 * (1 + 8 * t2) - psi * 2 * t2 + t4);
  const T7 =
    (om6 / 5040) * Math.pow(cosPhi, 6) * (61 - 479 * t2 + 179 * t4 - t6);

  const x = E0 + k0 * nu * om * cosPhi * (1 + T5 + T6 + T7);

  return { x, y };
}

/** NZTM2000 (metres) -> WGS84 (degrees). */
export function nztmToLatLon(x: number, y: number): LatLon {
  const Np = y - N0;
  const mp = m0 + Np / k0;
  const sigma = (mp / G) * (Math.PI / 180);

  // footpoint latitude
  const phiP =
    sigma +
    ((3 * n) / 2 - (27 * n3) / 32) * Math.sin(2 * sigma) +
    ((21 * n2) / 16 - (55 * n4) / 32) * Math.sin(4 * sigma) +
    ((151 * n3) / 96) * Math.sin(6 * sigma) +
    ((1097 * n4) / 512) * Math.sin(8 * sigma);

  const sinPhiP = Math.sin(phiP);
  const w = 1 - e2 * sinPhiP * sinPhiP;
  const rho = (a * (1 - e2)) / Math.pow(w, 1.5);
  const nu = a / Math.sqrt(w);
  const psi = nu / rho;
  const psi2 = psi * psi;
  const psi3 = psi2 * psi;
  const psi4 = psi3 * psi;

  const t = Math.tan(phiP);
  const t2 = t * t;
  const t4 = t2 * t2;
  const t6 = t4 * t2;

  const Ep = x - E0;
  const xx = Ep / (k0 * nu);
  const x3 = xx * xx * xx;
  const x5 = x3 * xx * xx;
  const x7 = x5 * xx * xx;

  const c = t / (k0 * rho);

  const T10 = c * ((xx * Ep) / 2);
  const T11 = c * ((Ep * x3) / 24) * (-4 * psi2 + 9 * psi * (1 - t2) + 12 * t2);
  const T12 =
    c *
    ((Ep * x5) / 720) *
    (8 * psi4 * (11 - 24 * t2) -
      12 * psi3 * (21 - 71 * t2) +
      15 * psi2 * (15 - 98 * t2 + 15 * t4) +
      180 * psi * (5 * t2 - 3 * t4) +
      360 * t4);
  const T13 =
    c * ((Ep * x7) / 40320) * (1385 + 3633 * t2 + 4095 * t4 + 1575 * t6);

  const phi = phiP - T10 + T11 - T12 + T13;

  const sec = 1 / Math.cos(phiP);
  const T14 = xx * sec;
  const T15 = (x3 / 6) * sec * (psi + 2 * t2);
  const T16 =
    (x5 / 120) *
    sec *
    (-4 * psi3 * (1 - 6 * t2) + psi2 * (9 - 68 * t2) + 72 * psi * t2 + 24 * t4);
  const T17 = (x7 / 5040) * sec * (61 + 662 * t2 + 1320 * t4 + 720 * t6);

  const lambda = lambda0 + T14 - T15 + T16 - T17;

  return { lat: (phi * 180) / Math.PI, lon: (lambda * 180) / Math.PI };
}

/**
 * Planar length of a polyline given as a flat [x0,y0,x1,y1,...] coordinate run
 * in EPSG:2193. Correct to use Euclidean arithmetic here: EPSG:2193 is a
 * projected metric CRS.
 */
export function polylineLength(
  coords: Float64Array | number[],
  start = 0,
  end = coords.length,
): number {
  let total = 0;
  for (let i = start; i + 3 < end; i += 2) {
    const dx = coords[i + 2] - coords[i];
    const dy = coords[i + 3] - coords[i + 1];
    total += Math.hypot(dx, dy);
  }
  return total;
}

export function euclid(ax: number, ay: number, bx: number, by: number): number {
  return Math.hypot(bx - ax, by - ay);
}

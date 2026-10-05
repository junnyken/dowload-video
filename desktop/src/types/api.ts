/**
 * Contract types for the VidGrab desktop client (Phase 32C plan, sections 2b and WS3).
 *
 * IMPORTANT: none of these endpoints exist on the server yet
 * (https://dvid-api.vibe1.tinhgon.xyz). They are the proposed contract only.
 *
 * Field names are exactly those in the plan. The plan does not fix scalar
 * types; the choices below are assumptions to confirm with the backend:
 * - sizes are bytes as numbers, heights are pixels as numbers;
 * - `expiresAt` is an ISO 8601 / RFC 3339 UTC timestamp string;
 * - `thumbnail` and `directUrl` are absolute https URLs.
 */

export type IsoDateTime = string;

// ---------------------------------------------------------------------------
// POST /api/v1/client/resolve  (server-assisted resolution, WS3)
// ---------------------------------------------------------------------------

export interface ClientResolveRequest {
  url: string;
  clientVersion: string;
  deviceId: string;
}

export interface ClientResolveFormat {
  id: string;
  label: string;
  ext: string;
  height: number | null;
  codec: string;
  sizeBytes?: number;
  /** Short-lived. The client must not follow redirects to private addresses. */
  directUrl: string;
  /** Extra request headers the CDN needs (e.g. Referer). */
  headers?: Record<string, string>;
}

export interface ClientResolveResponse {
  platform: string;
  title: string;
  thumbnail: string;
  formats: ClientResolveFormat[];
  expiresAt: IsoDateTime;
}

// ---------------------------------------------------------------------------
// GET /api/v1/client/version
// ---------------------------------------------------------------------------

export interface ClientVersionResponse {
  latest: string;
  minSupported: string;
  notes: string;
  downloadUrl: string;
}

// ---------------------------------------------------------------------------
// POST /api/v1/resolve-link  (delivery routing, section 2b)
// ---------------------------------------------------------------------------

export interface ResolveLinkRequest {
  url: string;
}

/** T0 server proxy | T1 direct handoff | T2 local desktop client. */
export type Delivery = 'proxy' | 'handoff' | 'local';

interface ResolveLinkOptionBase {
  id: string;
  label: string;
  height: number | null;
  codec: string;
  sizeBytes?: number;
  hasAudio: boolean;
  expiresAt?: IsoDateTime;
}

/**
 * Routing rule 2 is encoded in the type: an option that needs merging can
 * never be delivered as 'handoff' (T1).
 */
export type ResolveLinkOption = ResolveLinkOptionBase &
  (
    | { delivery: 'handoff'; requiresMerge: false }
    | { delivery: Exclude<Delivery, 'handoff'>; requiresMerge: boolean }
  );

export interface ResolveLinkResponse {
  platform: string;
  title: string;
  thumbnail: string;
  options: ResolveLinkOption[];
}

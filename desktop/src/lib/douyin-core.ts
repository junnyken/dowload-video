// Pure logic of the Douyin channel listing (0.18.2). No Tauri, React or
// network imports so `node --test` can run it (like quota-core.ts).
// lib/douyin.ts wires it to the server and the page window.
import type { ChannelListing } from './types';

/** "Tất cả" in the channel form: the most one scan asks for. */
export const DOUYIN_ALL = 500;

/** The server listed one page and Douyin has more than was asked for: read the rest in the app's page window. */
export function needsMore(first: ChannelListing, limit: number): boolean {
  return first.moreHint === 'douyin_extension' && first.videos.length < limit;
}

/**
 * The page window's listing merged into the server's: the page's order
 * (newest first, the whole channel), the server's title / duration /
 * thumbnail where both have a video (the page has no duration and a rougher
 * title), and the server's channel name and avatar. Server-only videos (the
 * page missed them) go last. Cut to `limit`.
 */
export function mergeListings(server: ChannelListing, page: ChannelListing, limit: number): ChannelListing {
  const byId = new Map(server.videos.map((v) => [v.id, v]));
  const seen = new Set<string>();
  const videos = [];
  for (const p of page.videos) {
    if (seen.has(p.id)) continue;
    seen.add(p.id);
    const s = byId.get(p.id);
    videos.push(s ? { ...p, ...s, thumbnail: s.thumbnail ?? p.thumbnail } : p);
  }
  for (const s of server.videos) if (!seen.has(s.id)) { seen.add(s.id); videos.push(s); }
  return {
    ...server,
    title: server.title || page.title,
    thumbnail: server.thumbnail ?? page.thumbnail,
    videos: videos.slice(0, limit),
    cap: undefined,
    moreHint: null,
  };
}

/** True when `other` lists more of the channel than `first` (worth showing instead). */
export const listsMore = (first: ChannelListing, other: ChannelListing | null): other is ChannelListing =>
  !!other && other.videos.length > first.videos.length;

/** GET /api/v1/channel-relay/{token}: what the extension has sent so far (0.18.5). */
export type RelayDoc = {
  status: 'waiting' | 'scrolling' | 'login' | 'done'; // login: Douyin's sign-in wall (extension 5.11.1)
  channelUrl: string;
  channelTitle: string | null;
  items: { id: string; url: string; title: string | null }[];
  skipped: number;
};

/** The extension's list as a listing, merged into the server's (its titles / durations win where both have a video). */
export function relayListing(server: ChannelListing, doc: RelayDoc): ChannelListing {
  const page: ChannelListing = {
    ...server,
    title: doc.channelTitle || server.title,
    videos: doc.items.map((i) => ({
      id: i.id, url: i.url, title: i.title || `Douyin ${i.id.slice(-6)}`, duration: null, uploadDate: null, thumbnail: null,
    })),
  };
  return mergeListings(server, page, doc.items.length + server.videos.length);
}

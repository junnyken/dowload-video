// Types from docs/desktop/C1-CONTRACT.md (section 1) plus UI-only types.
import type { Media } from './media';

export type ProbeFormat = {
  id: string;
  label: string;
  height: number | null;
  ext: string;
  vcodec: string | null;
  acodec: string | null;
  fps: number | null;
  filesize: number | null;
  requiresMerge: boolean;
  audioOnly: boolean;
};

export type ProbeResult = {
  url: string;
  platform: string;
  title: string;
  thumbnail: string | null;
  duration: number | null;
  uploader: string | null;
  formats: ProbeFormat[];
};

export type HistoryItem = {
  id: string;
  url: string;
  title: string;
  platform: string;
  formatLabel: string;
  filePath: string | null;
  fileSize: number | null;
  state: 'completed' | 'failed';
  errorCode: string | null;
  createdAt: string;
  finishedAt: string | null;
  synced: boolean;
};

export type Stage = 'downloading' | 'merging' | 'processing';

export type ProgressEvent = {
  jobId: string;
  stage: Stage;
  percent: number | null;
  downloadedBytes: number | null;
  totalBytes: number | null;
  speedBps: number | null;
  etaSec: number | null;
};

export type LogEvent = { jobId: string; line: string };

export type DoneEvent = {
  jobId: string;
  state: 'completed' | 'failed' | 'paused' | 'cancelled';
  filePath?: string;
  fileSize?: number;
  errorCode?: string;
  errorMessage?: string;
  /** true when the run used the user's saved cookies (PLAN-32D L1). */
  cookiesUsed?: boolean;
  /** 0.21.0 (quality #3): the file measured with ffprobe; absent when it could not be read. */
  media?: Media;
};

export type ToolVersions = {
  ytdlp: string;
  /** Task #6333: 'server' = a newer yt-dlp approved on the server is in use; absent from older apps. */
  ytdlpSource?: 'bundled' | 'server';
  ffmpeg: string;
  deno: string;
};

export type ClientVersionInfo = {
  latest: string;
  minSupported?: string;
  notes?: string;
  downloadUrl?: string;
};

export type ChannelVideo = {
  id: string;
  url: string;
  title: string;
  duration: number | null;
  uploadDate: string | null; // YYYYMMDD
  thumbnail: string | null;
  /** 0.17.0: when the listing has it (YouTube Shorts, TikTok by id). */
  viewCount?: number | null;
};

/** 0.17.0: optional conditions for a watched channel's new videos (channel-filter.ts). */
export type ChannelFilter = {
  minSeconds: number | null;
  maxSeconds: number | null;
  minViews: number | null;
  maxAgeDays: number | null;
  includeWords: string[];
  excludeWords: string[];
};

export type ChannelListing = {
  channelId: string;
  url: string;
  title: string;
  platform: string;
  uploader: string | null;
  thumbnail: string | null;
  videos: ChannelVideo[];
  truncated: boolean;
  cap?: number; // Douyin: how many downloads the person still has today (the listing is cut to this)
  moreHint?: string | null; // Douyin: 'douyin_extension' when the channel has more than the server listed
  pageNote?: string | null; // Douyin (0.18.4): why the app's page window listed nothing more — diagnostic text
};

export type ChannelMode = 'download' | 'notify';
export type ChannelInterval = 1 | 3 | 6 | 12 | 24;

export type Channel = {
  id: string;
  url: string;
  title: string;
  platform: string;
  thumbnail: string | null;
  mode: ChannelMode;
  quality: string; // preset id or "best"
  outDir: string;
  checkEveryHours: ChannelInterval;
  enabled: boolean;
  lastCheckedAt: string | null;
  lastError: string | null;
  pendingNew: ChannelVideo[];
  createdAt: string;
  /** 0.17.0; absent on channels saved by older versions = no filter. */
  filter?: ChannelFilter;
};

export type Screen = 'download' | 'channels' | 'watch' | 'queue' | 'history' | 'settings';

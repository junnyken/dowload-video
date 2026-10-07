// Types from docs/desktop/C1-CONTRACT.md (section 1) plus UI-only types.

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
};

export type ToolVersions = { ytdlp: string; ffmpeg: string; deno: string };

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
};

export type Screen = 'download' | 'channels' | 'queue' | 'history' | 'settings';

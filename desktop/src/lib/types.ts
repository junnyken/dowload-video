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

export type Screen = 'download' | 'queue' | 'history' | 'settings';

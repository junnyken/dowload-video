// Server files are named by a file id (their basename), never by the server
// path. The backend returns `*_file_id` next to each legacy path field; the
// path fields disappear once EXPOSE_LEGACY_PATHS is switched off, so read
// the id first and only fall back to the basename of a legacy path.

const baseName = (p) => (typeof p === 'string' && p ? p.split(/[\\/]/).pop() || null : null);

/** File id of the video/main file in a fetch-link response or history row. */
export const localFileId = (d) => d?.local_file_id || baseName(d?.local_file_path);

/** File id of the audio file in a fetch-link response or history row. */
export const localMp3Id = (d) => d?.local_mp3_file_id || baseName(d?.local_mp3_path);

/** Audio first, then video — what the download buttons used to pick. */
export const localAnyId = (d) => localMp3Id(d) || localFileId(d);

/** Video first, then audio. */
export const localVideoFirstId = (d) => localFileId(d) || localMp3Id(d);

/** Id from a value that may be a legacy path, a bare id, or empty. */
export const toFileId = (v) => baseName(v);

/** /api/v1/download-local link for a file id. `base` is the API origin. */
export const localDownloadUrl = (base, fileId, filename) =>
  `${base}/api/v1/download-local?file=${encodeURIComponent(fileId)}&filename=${encodeURIComponent(filename)}`;

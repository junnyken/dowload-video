// C0 spike UI: just enough to exercise the four Tauri commands.
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';

type LogEvent = { jobId: string; stream: 'stdout' | 'stderr'; line: string };
type DoneEvent = { jobId: string; exitCode: number | null; cancelled: boolean };

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const urlInput = $<HTMLInputElement>('url');
const startBtn = $<HTMLButtonElement>('start');
const cancelBtn = $<HTMLButtonElement>('cancel');
const folderLabel = $<HTMLSpanElement>('folder');
const logBox = $<HTMLPreElement>('log');

let outDir: string | null = null;
let jobId: string | null = null;

function log(text: string) {
  logBox.textContent += text + '\n';
  logBox.scrollTop = logBox.scrollHeight;
}

function setRunning(running: boolean) {
  startBtn.disabled = running;
  cancelBtn.disabled = !running;
}

async function init() {
  $('version').textContent = 'v' + (await invoke<string>('get_version'));

  await listen<LogEvent>('download://log', ({ payload }) => {
    if (payload.jobId === jobId) log(payload.line);
  });
  await listen<DoneEvent>('download://done', ({ payload }) => {
    if (payload.jobId !== jobId) return;
    log(payload.cancelled ? '-- cancelled' : `-- finished (exit code ${payload.exitCode ?? 'unknown'})`);
    jobId = null;
    setRunning(false);
  });

  $('pick').addEventListener('click', async () => {
    const dir = await invoke<string | null>('pick_folder');
    if (dir) {
      outDir = dir;
      folderLabel.textContent = dir;
    }
  });

  $('form').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!outDir) return log('Choose a folder first.');
    setRunning(true);
    try {
      jobId = await invoke<string>('start_download', { url: urlInput.value, outDir, formatId: null });
      log(`-- started ${jobId}`);
    } catch (err) {
      log(`-- error: ${String(err)}`);
      setRunning(false);
    }
  });

  cancelBtn.addEventListener('click', async () => {
    if (!jobId) return;
    try {
      await invoke<boolean>('cancel_download', { jobId });
    } catch (err) {
      log(`-- cancel error: ${String(err)}`);
    }
  });
}

init().catch((err) => log(`-- init error: ${String(err)}`));

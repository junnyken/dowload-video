import { createStore } from './store';

// ---- toasts -----------------------------------------------------------------
export type Toast = { id: number; kind: 'success' | 'error' | 'info'; text: string };
export const toasts = createStore<Toast[]>([]);
let tid = 1;
export function toast(kind: Toast['kind'], text: string) {
  const id = tid++;
  toasts.set((l) => [...l.slice(-3), { id, kind, text }]);
  setTimeout(() => dismissToast(id), kind === 'error' ? 8000 : 4500);
}
export function dismissToast(id: number) {
  toasts.set((l) => l.filter((t) => t.id !== id));
}

// ---- confirm dialog -----------------------------------------------------------
export type ConfirmReq = { title: string; text: string; okLabel: string; danger?: boolean; resolve: (v: boolean) => void };
export const confirmState = createStore<ConfirmReq | null>(null);
export function confirmDialog(o: Omit<ConfirmReq, 'resolve'>): Promise<boolean> {
  return new Promise((resolve) => confirmState.set({ ...o, resolve }));
}

// ---- navigation ----------------------------------------------------------------
import type { Screen } from './types';
export const nav = createStore<{ screen: Screen; signIn: boolean }>({ screen: 'download', signIn: false });
export const go = (screen: Screen) => nav.set((s) => ({ ...s, screen }));
export const openSignIn = (open = true) => nav.set((s) => ({ ...s, signIn: open }));

// ---- "Tải lại": hand a URL to the Download screen ---------------------------------
export const prefill = createStore<string | null>(null);
export function redownload(url: string) {
  prefill.set(url);
  go('download');
}

// ---- "Mở ở mục Kênh": hand a channel URL to the Channels screen ----------------------
export const channelPrefill = createStore<string | null>(null);
export function openInChannels(url: string) {
  channelPrefill.set(url);
  go('channels');
}

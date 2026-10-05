import { useSyncExternalStore } from 'react';

/** Tiny external store: immutable state, subscribe, React hook. */
export function createStore<T>(initial: T) {
  let state = initial;
  const subs = new Set<() => void>();
  return {
    get: () => state,
    set(next: T | ((s: T) => T)) {
      const v = typeof next === 'function' ? (next as (s: T) => T)(state) : next;
      if (v === state) return;
      state = v;
      subs.forEach((f) => f());
    },
    subscribe(f: () => void) {
      subs.add(f);
      return () => subs.delete(f);
    },
    use(): T {
      return useSyncExternalStore(
        (f) => {
          subs.add(f);
          return () => subs.delete(f);
        },
        () => state,
      );
    },
  };
}

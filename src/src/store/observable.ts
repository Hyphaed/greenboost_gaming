// A BehaviorSubject in ~40 lines, plus the hook that binds it to React.
//
// Why this exists: every view in this app used to invoke its own Tauri
// command into its own useState. `get_global_settings` alone was read in six
// places across four files, and Games.tsx held three separate copies of it ,
// one of which (the launch handler) re-fetched from the backend on every
// click precisely because it could not trust the copy sitting next to it.
// Navigating away unmounted a view and threw its state out, which hid the
// staleness across views while leaving it live inside them.
//
// A store here holds one current value, hands it to any subscriber
// immediately, and pushes every later change to all of them. That is the
// BehaviorSubject contract, and `useSyncExternalStore` is React's supported
// way to read from exactly this shape without tearing during concurrent
// rendering , which is why this is ~40 lines of primitive rather than a
// dependency.

import { useSyncExternalStore } from "react";

export interface Store<T> {
  /** Current value. Always defined , that's the "Behavior" part. */
  get(): T;
  /** Replace the value and notify subscribers. No-op if Object.is-equal. */
  set(next: T): void;
  /** Derive the next value from the current one. */
  update(fn: (current: T) => T): void;
  /** Subscribe; returns an unsubscribe function. */
  subscribe(listener: () => void): () => void;
}

export function createStore<T>(initial: T): Store<T> {
  let value = initial;
  const listeners = new Set<() => void>();

  return {
    get: () => value,
    set(next: T) {
      // Bail on identical values so a poll that returns an unchanged object
      // reference doesn't re-render every subscriber twice a second.
      if (Object.is(value, next)) return;
      value = next;
      // Copy before iterating: a listener may unsubscribe during dispatch
      // (React does exactly this when a component unmounts mid-notify).
      for (const l of [...listeners]) l();
    },
    update(fn) { this.set(fn(value)); },
    subscribe(listener) {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
  };
}

/**
 * Read a store's value in a component and re-render when it changes.
 *
 * The store must return a stable reference for unchanged values , this reads
 * `get()` directly rather than mapping/selecting, because returning a fresh
 * object from a snapshot on every call makes React loop forever. Select with
 * a plain expression on the result instead:
 *
 *     const settings = useStore(globalSettings);
 *     const on = !!settings?.mangohud_enabled;
 */
export function useStore<T>(store: Store<T>): T {
  return useSyncExternalStore(store.subscribe, store.get, store.get);
}

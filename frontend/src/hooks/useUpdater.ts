import { useState, useEffect, useCallback, useRef } from "react";

/** How often to poll for updates (6 hours in ms) */
const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000;

export interface UpdateInfo {
  version: string;
  date?: string;
  body?: string;
}

export type UpdateStatus =
  | "idle"
  | "checking"
  | "available"
  | "downloading"
  | "error";

export interface UseUpdaterReturn {
  status: UpdateStatus;
  updateInfo: UpdateInfo | null;
  error: string | null;
  progress: number;
  checkForUpdates: () => Promise<void>;
  downloadAndInstall: () => Promise<void>;
  dismissUpdate: () => void;
}

/** Whether we're running inside a Tauri webview */
function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export function useUpdater(): UseUpdaterReturn {
  const [status, setStatus] = useState<UpdateStatus>("idle");
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const updateRef = useRef<unknown>(null);

  const checkForUpdates = useCallback(async () => {
    if (!isTauri()) return;

    setStatus("checking");
    setError(null);
    try {
      const { check } = await import("@tauri-apps/plugin-updater");
      const update = await check();
      if (update) {
        updateRef.current = update;
        setUpdateInfo({
          version: update.version,
          date: update.date ?? undefined,
          body: update.body ?? undefined,
        });
        setStatus("available");
      } else {
        setStatus("idle");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to check for updates");
      setStatus("error");
    }
  }, []);

  const downloadAndInstall = useCallback(async () => {
    const update = updateRef.current as {
      downloadAndInstall: (
        cb?: (event: {
          event: string;
          data?: { contentLength?: number; chunkLength?: number };
        }) => void,
      ) => Promise<void>;
    } | null;
    if (!update) return;

    setStatus("downloading");
    setProgress(0);
    try {
      let totalBytes = 0;
      let downloadedBytes = 0;
      await update.downloadAndInstall((event) => {
        if (event.event === "Started" && event.data?.contentLength) {
          totalBytes = event.data.contentLength;
        } else if (event.event === "Progress" && event.data?.chunkLength) {
          downloadedBytes += event.data.chunkLength;
          if (totalBytes > 0) {
            setProgress(Math.round((downloadedBytes / totalBytes) * 100));
          }
        }
      });
      const { relaunch } = await import("@tauri-apps/plugin-process");
      await relaunch();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to install update");
      setStatus("error");
    }
  }, []);

  const dismissUpdate = useCallback(() => {
    setStatus("idle");
    setUpdateInfo(null);
    updateRef.current = null;
  }, []);

  // Check on mount + every 6 hours
  useEffect(() => {
    if (!isTauri()) return;

    checkForUpdates();
    const interval = setInterval(checkForUpdates, CHECK_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [checkForUpdates]);

  return {
    status,
    updateInfo,
    error,
    progress,
    checkForUpdates,
    downloadAndInstall,
    dismissUpdate,
  };
}

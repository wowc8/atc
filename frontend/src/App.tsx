import { useCallback, useEffect, useRef, useState } from "react";
import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
  Outlet,
  useLocation,
} from "react-router-dom";
import { AppProvider, useAppContext } from "./context/AppContext";
import ResizeHandle from "./components/common/ResizeHandle";
import TowerBar from "./components/tower/TowerBar";
import TowerPanel from "./components/tower/TowerPanel";
import UpdateBanner from "./components/common/UpdateBanner";
import { useUpdater } from "./hooks/useUpdater";
import {
  clampTowerWidth,
  isSideTowerRoute,
  readStoredTowerWidth,
  shouldShowTowerPanel,
  SIDE_TOWER_WIDTH_KEY,
} from "./layout/towerSplit";
import Dashboard from "./pages/Dashboard";
import ProjectView from "./pages/ProjectView";
import UsagePage from "./pages/UsagePage";
import ContextPage from "./pages/ContextPage";
import "./App.css";

const isTauri =
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

function StartupGate({ children }: { children: React.ReactNode }) {
  const { backendReady, backendError } = useAppContext();

  if (!isTauri || backendReady) {
    return <>{children}</>;
  }

  if (backendError) {
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          height: "100vh",
          gap: 12,
          color: "#ef4444",
        }}
      >
        <span style={{ fontSize: 16 }}>Failed to connect to ATC backend</span>
        <span style={{ fontSize: 13, color: "#9ca3af" }}>{backendError}</span>
      </div>
    );
  }

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        height: "100vh",
        gap: 12,
        color: "#9ca3af",
      }}
    >
      <span style={{ fontSize: 16 }}>Starting ATC...</span>
    </div>
  );
}

function Layout() {
  const updater = useUpdater();
  const location = useLocation();
  const showTower = shouldShowTowerPanel(location.pathname);
  const showSideTower = isSideTowerRoute(location.pathname);
  const shellBodyRef = useRef<HTMLDivElement>(null);
  const [towerWidth, setTowerWidth] = useState(readStoredTowerWidth);

  useEffect(() => {
    window.localStorage.setItem(SIDE_TOWER_WIDTH_KEY, String(towerWidth));
  }, [towerWidth]);

  useEffect(() => {
    if (!showSideTower) {
      return;
    }

    const syncWidth = () => {
      const containerWidth =
        shellBodyRef.current?.offsetWidth ?? window.innerWidth;
      setTowerWidth((current) => clampTowerWidth(current, containerWidth));
    };

    syncWidth();
    window.addEventListener("resize", syncWidth);
    return () => window.removeEventListener("resize", syncWidth);
  }, [showSideTower]);

  const handleTowerResize = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      e.preventDefault();
      const startX = e.clientX;
      const startWidth = towerWidth;

      const onMove = (ev: MouseEvent) => {
        const containerWidth =
          shellBodyRef.current?.offsetWidth ?? window.innerWidth;
        const delta = startX - ev.clientX;
        setTowerWidth(clampTowerWidth(startWidth + delta, containerWidth));
      };

      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };

      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    [towerWidth],
  );

  return (
    <div className="app-shell">
      {(updater.status === "available" || updater.status === "downloading") &&
        updater.updateInfo && (
          <UpdateBanner
            updateInfo={updater.updateInfo}
            status={updater.status}
            progress={updater.progress}
            onInstall={updater.downloadAndInstall}
            onDismiss={updater.dismissUpdate}
          />
        )}
      <TowerBar />
      <div
        ref={shellBodyRef}
        className={`app-shell__body${showSideTower ? " app-shell__body--side-tower" : ""}`}
        style={
          showSideTower
            ? { ["--tower-width" as string]: `${towerWidth}px` }
            : undefined
        }
      >
        <main className="app-shell__main">
          <Outlet context={updater} />
        </main>
        {showSideTower && (
          <ResizeHandle direction="col" onMouseDown={handleTowerResize} />
        )}
        {showTower && (
          <div className="app-shell__tower">
            <TowerPanel orientation={showSideTower ? "side" : "bottom"} />
          </div>
        )}
      </div>
    </div>
  );
}

export default function App() {
  return (
    <AppProvider>
      <StartupGate>
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/" element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/projects/:id" element={<ProjectView />} />
              <Route path="/usage" element={<UsagePage />} />
              <Route path="/context" element={<ContextPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </StartupGate>
    </AppProvider>
  );
}

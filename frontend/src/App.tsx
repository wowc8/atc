import { BrowserRouter, Routes, Route, Navigate, Outlet } from "react-router-dom";
import { AppProvider } from "./context/AppContext";
import TowerBar from "./components/tower/TowerBar";
import TowerPanel from "./components/tower/TowerPanel";
import UpdateBanner from "./components/common/UpdateBanner";
import { useUpdater } from "./hooks/useUpdater";
import Dashboard from "./pages/Dashboard";
import ProjectView from "./pages/ProjectView";
import UsagePage from "./pages/UsagePage";
import ContextPage from "./pages/ContextPage";

function Layout() {
  const updater = useUpdater();

  return (
    <>
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
      <main style={{ flex: 1, overflow: "auto", minHeight: 0 }}>
        <Outlet context={updater} />
      </main>
      <TowerPanel />
    </>
  );
}

export default function App() {
  return (
    <AppProvider>
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
    </AppProvider>
  );
}

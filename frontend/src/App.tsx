import { BrowserRouter, Routes, Route, Navigate, Outlet, useLocation } from "react-router-dom";
import { AppProvider } from "./context/AppContext";
import TowerBar from "./components/tower/TowerBar";
import TowerPanel from "./components/tower/TowerPanel";
import Dashboard from "./pages/Dashboard";
import ProjectView from "./pages/ProjectView";
import SettingsPage from "./pages/SettingsPage";
import UsagePage from "./pages/UsagePage";

/** Pages where the Tower panel should NOT appear */
const HIDE_TOWER_PATHS = ["/settings", "/usage"];

function Layout() {
  const { pathname } = useLocation();
  const showTower = !HIDE_TOWER_PATHS.includes(pathname);

  return (
    <>
      <TowerBar />
      <main style={{ flex: 1, overflow: "auto", minHeight: 0 }}>
        <Outlet />
      </main>
      {showTower && <TowerPanel />}
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
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/usage" element={<UsagePage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AppProvider>
  );
}

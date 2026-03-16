import { useState } from "react";
import { useAppContext } from "../context/AppContext";
import "./SettingsPage.css";

export default function SettingsPage() {
  const { state } = useAppContext();
  const [backendUrl] = useState("http://127.0.0.1:8420");

  return (
    <div className="settings-page" data-testid="settings-page">
      <h1>Settings</h1>

      <div className="settings-page__grid">
        <section className="panel settings-page__section">
          <h2>Connection</h2>
          <div className="form-group">
            <label htmlFor="backend-url">Backend URL</label>
            <input
              id="backend-url"
              type="text"
              value={backendUrl}
              readOnly
            />
          </div>
          <div className="settings-page__status">
            <span className="settings-page__dot settings-page__dot--connected" />
            Connected
          </div>
        </section>

        <section className="panel settings-page__section">
          <h2>Tower Status</h2>
          <div className="settings-page__info-row">
            <span className="settings-page__label">Status</span>
            <span className="settings-page__value">
              {state.brainStatus.status}
            </span>
          </div>
          <div className="settings-page__info-row">
            <span className="settings-page__label">Active Projects</span>
            <span className="settings-page__value">
              {state.brainStatus.active_projects}
            </span>
          </div>
        </section>

        <section className="panel settings-page__section">
          <h2>Appearance</h2>
          <div className="settings-page__info-row">
            <span className="settings-page__label">Theme</span>
            <span className="settings-page__value">Dark</span>
          </div>
        </section>
      </div>
    </div>
  );
}

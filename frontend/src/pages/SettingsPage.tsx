import { useState } from "react";
import { useAppContext } from "../context/AppContext";
import "./SettingsPage.css";

const GITHUB_ORG_KEY = "atc:github_default_org";

export default function SettingsPage() {
  const { state } = useAppContext();
  const [backendUrl] = useState("http://127.0.0.1:8420");
  const [githubOrg, setGithubOrg] = useState(
    () => localStorage.getItem(GITHUB_ORG_KEY) ?? "",
  );

  function handleGithubOrgChange(value: string) {
    setGithubOrg(value);
    if (value.trim()) {
      localStorage.setItem(GITHUB_ORG_KEY, value.trim());
    } else {
      localStorage.removeItem(GITHUB_ORG_KEY);
    }
  }

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
          <h2>GitHub Defaults</h2>
          <div className="form-group">
            <label htmlFor="github-org">Default Org / Username</label>
            <input
              id="github-org"
              type="text"
              value={githubOrg}
              onChange={(e) => handleGithubOrgChange(e.target.value)}
              placeholder="my-org"
            />
            <span className="form-hint">
              Pre-fills the GitHub Repo field when creating new projects.
            </span>
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

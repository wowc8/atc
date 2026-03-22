import { useState } from "react";
import { useAppContext } from "../../context/AppContext";
import { BackupPanel } from "../settings/BackupPanel";
import "./SettingsPane.css";

interface Props {
  onClose: () => void;
}

const GITHUB_ORG_KEY = "atc:github_default_org";

export default function SettingsPane({ onClose }: Props) {
  const { state } = useAppContext();
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
    <div className="settings-pane" data-testid="settings-pane">
      <div className="settings-pane__header">
        <h2 className="settings-pane__title">Settings</h2>
        <button
          className="settings-pane__close"
          onClick={onClose}
          data-testid="close-settings-pane"
        >
          Close
        </button>
      </div>

      <div className="settings-pane__body">
        <section className="panel settings-pane__section">
          <h3>Connection</h3>
          <div className="form-group">
            <label htmlFor="backend-url">Backend URL</label>
            <input
              id="backend-url"
              type="text"
              value="http://127.0.0.1:8420"
              readOnly
            />
          </div>
          <div className="settings-pane__status">
            <span className="settings-pane__dot settings-pane__dot--connected" />
            Connected
          </div>
        </section>

        <section className="panel settings-pane__section">
          <h3>GitHub Defaults</h3>
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

        <BackupPanel />

        <section className="panel settings-pane__section">
          <h3>Tower Status</h3>
          <div className="settings-pane__info-row">
            <span className="settings-pane__label">Status</span>
            <span className="settings-pane__value">
              {state.brainStatus.status}
            </span>
          </div>
          <div className="settings-pane__info-row">
            <span className="settings-pane__label">Active Projects</span>
            <span className="settings-pane__value">
              {state.brainStatus.active_projects}
            </span>
          </div>
        </section>
      </div>
    </div>
  );
}

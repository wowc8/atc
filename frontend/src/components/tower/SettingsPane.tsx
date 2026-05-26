import { useEffect, useState } from "react";
import { useAppContext } from "../../context/AppContext";
import { api } from "../../utils/api";
import type { AgentProviderConfig, ProviderInfo } from "../../types";
import { BackupPanel } from "../settings/BackupPanel";
import { ResourceLimitsPanel } from "../settings/ResourceLimitsPanel";
import "./SettingsPane.css";

interface Props {
  onClose: () => void;
}

const GITHUB_ORG_KEY = "atc:github_default_org";

const EMPTY_PROVIDER_CONFIG: AgentProviderConfig = {
  default: "claude_code",
  opencode_url: "http://localhost:4096",
  tmux_session: "atc",
  claude_command: "claude",
  codex_command: "codex",
};

export default function SettingsPane({ onClose }: Props) {
  const { state } = useAppContext();
  const [githubOrg, setGithubOrg] = useState(
    () => localStorage.getItem(GITHUB_ORG_KEY) ?? "",
  );
  const [providerConfig, setProviderConfig] = useState<AgentProviderConfig>(EMPTY_PROVIDER_CONFIG);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [savingProvider, setSavingProvider] = useState(false);
  const [providerMessage, setProviderMessage] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    void (async () => {
      try {
        const [cfg, providerList] = await Promise.all([
          api.get<AgentProviderConfig>("/settings/agent-provider"),
          api.get<ProviderInfo[]>("/settings/providers"),
        ]);
        if (!mounted) return;
        setProviderConfig(cfg);
        setProviders(providerList);
      } catch (err) {
        if (!mounted) return;
        setProviderMessage(err instanceof Error ? err.message : "Failed to load provider settings");
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  function handleGithubOrgChange(value: string) {
    setGithubOrg(value);
    if (value.trim()) {
      localStorage.setItem(GITHUB_ORG_KEY, value.trim());
    } else {
      localStorage.removeItem(GITHUB_ORG_KEY);
    }
  }

  async function saveProviderConfig(next: Partial<AgentProviderConfig>) {
    const optimistic = { ...providerConfig, ...next };
    setProviderConfig(optimistic);
    setSavingProvider(true);
    setProviderMessage(null);
    try {
      const saved = await api.put<AgentProviderConfig>("/settings/agent-provider", next);
      setProviderConfig(saved);
      setProviderMessage("Provider settings saved");
    } catch (err) {
      setProviderMessage(err instanceof Error ? err.message : "Failed to save provider settings");
    } finally {
      setSavingProvider(false);
    }
  }

  const terminalBackedProviders = new Set(["claude_code", "codex"]);

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
          <h3>Agent Provider</h3>
          <div className="form-group">
            <label htmlFor="provider-default">Default Provider</label>
            <select
              id="provider-default"
              value={providerConfig.default}
              onChange={(e) => void saveProviderConfig({ default: e.target.value })}
              disabled={savingProvider}
            >
              {providers.map((provider) => (
                <option key={provider.name} value={provider.name}>
                  {provider.name}
                </option>
              ))}
            </select>
            <span className="form-hint">
              {terminalBackedProviders.has(providerConfig.default)
                ? "This provider uses live tmux terminal panes in the app."
                : "This provider may use a non-terminal control flow even if a visibility pane exists."} Default provider applies to new projects. Existing projects keep their saved provider until changed per-project, and any already-running Tower session keeps its current provider until restarted.
            </span>
          </div>

          <div className="form-group">
            <label htmlFor="provider-claude-command">Claude Code Command</label>
            <input
              id="provider-claude-command"
              type="text"
              value={providerConfig.claude_command}
              onChange={(e) => setProviderConfig((prev) => ({ ...prev, claude_command: e.target.value }))}
              onBlur={() => void saveProviderConfig({ claude_command: providerConfig.claude_command })}
            />
          </div>

          <div className="form-group">
            <label htmlFor="provider-codex-command">Codex Command</label>
            <input
              id="provider-codex-command"
              type="text"
              value={providerConfig.codex_command}
              onChange={(e) => setProviderConfig((prev) => ({ ...prev, codex_command: e.target.value }))}
              onBlur={() => void saveProviderConfig({ codex_command: providerConfig.codex_command })}
            />
          </div>

          <div className="form-group">
            <label htmlFor="provider-opencode-url">OpenCode URL</label>
            <input
              id="provider-opencode-url"
              type="text"
              value={providerConfig.opencode_url}
              onChange={(e) => setProviderConfig((prev) => ({ ...prev, opencode_url: e.target.value }))}
              onBlur={() => void saveProviderConfig({ opencode_url: providerConfig.opencode_url })}
            />
          </div>

          <div className="form-group">
            <label htmlFor="provider-tmux-session">tmux Session</label>
            <input
              id="provider-tmux-session"
              type="text"
              value={providerConfig.tmux_session}
              onChange={(e) => setProviderConfig((prev) => ({ ...prev, tmux_session: e.target.value }))}
              onBlur={() => void saveProviderConfig({ tmux_session: providerConfig.tmux_session })}
            />
          </div>

          {providerMessage && <div className="form-hint">{providerMessage}</div>}
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

        <ResourceLimitsPanel />

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

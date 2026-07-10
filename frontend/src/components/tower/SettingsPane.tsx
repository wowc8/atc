import { useEffect, useMemo, useState } from "react";
import { useAppContext } from "../../context/AppContext";
import { api } from "../../utils/api";
import type {
  AgentProviderConfig,
  ProviderHelperSettings,
  ProviderInfo,
} from "../../types";
import { BackupPanel } from "../settings/BackupPanel";
import { ResourceLimitsPanel } from "../settings/ResourceLimitsPanel";
import "./SettingsPane.css";

interface Props {
  onClose: () => void;
}

const GITHUB_ORG_KEY = "atc:github_default_org";

const EMPTY_PROVIDER_CONFIG: AgentProviderConfig = {
  default: "codex",
  opencode_url: "http://localhost:4096",
  tmux_session: "atc",
  claude_command: "claude",
  codex_command: "codex",
};

const EMPTY_HELPER_SETTINGS: ProviderHelperSettings = {
  enabled: true,
  default_visibility: "hidden",
  audit_enabled: true,
};

const HELPER_VISIBILITY_LABELS: Record<
  ProviderHelperSettings["default_visibility"],
  string
> = {
  hidden: "Hidden — normal workflow UI stays quiet, audit stays on",
  summary: "Summary — show lifecycle and result summaries",
  full: "Full — show prompts, output, timings, tokens, and events",
};

export default function SettingsPane({ onClose }: Props) {
  const { state } = useAppContext();
  const [githubOrg, setGithubOrg] = useState(
    () => globalThis.localStorage?.getItem(GITHUB_ORG_KEY) ?? "",
  );
  const [providerConfig, setProviderConfig] = useState<AgentProviderConfig>(
    EMPTY_PROVIDER_CONFIG,
  );
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [savingProvider, setSavingProvider] = useState(false);
  const [providerMessage, setProviderMessage] = useState<string | null>(null);
  const [helperSettings, setHelperSettings] = useState<ProviderHelperSettings>(
    EMPTY_HELPER_SETTINGS,
  );
  const [savingHelpers, setSavingHelpers] = useState(false);
  const [helperMessage, setHelperMessage] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    void (async () => {
      try {
        const [cfg, providerList, helpers] = await Promise.all([
          api.get<AgentProviderConfig>("/settings/agent-provider"),
          api.get<ProviderInfo[]>("/settings/providers"),
          api.get<ProviderHelperSettings>("/settings/provider-helpers"),
        ]);
        if (!mounted) return;
        setProviderConfig(cfg);
        setProviders(providerList);
        setHelperSettings(helpers);
      } catch (err) {
        if (!mounted) return;
        setProviderMessage(
          err instanceof Error
            ? err.message
            : "Failed to load provider settings",
        );
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  const activeProjects = useMemo(
    () => state.projects.filter((project) => project.status === "active"),
    [state.projects],
  );

  const activeTowerProject = state.towerDetail.current_project_id
    ? (state.projects.find(
        (project) => project.id === state.towerDetail.current_project_id,
      ) ?? null)
    : null;
  const terminalBackedProviders = new Set(["claude_code", "codex"]);

  function handleGithubOrgChange(value: string) {
    setGithubOrg(value);
    if (value.trim()) {
      globalThis.localStorage?.setItem(GITHUB_ORG_KEY, value.trim());
    } else {
      globalThis.localStorage?.removeItem(GITHUB_ORG_KEY);
    }
  }

  async function saveProviderConfig(next: Partial<AgentProviderConfig>) {
    const optimistic = { ...providerConfig, ...next };
    const providerChanged =
      next.default !== undefined && next.default !== providerConfig.default;
    setProviderConfig(optimistic);
    setSavingProvider(true);
    setProviderMessage(null);
    if (providerChanged) {
      window.dispatchEvent(new CustomEvent("atc:provider-switching"));
    }
    try {
      const saved = await api.put<AgentProviderConfig>(
        "/settings/agent-provider",
        next,
      );
      setProviderConfig(saved);
      setProviderMessage(
        providerChanged
          ? "Provider updated globally. Existing sessions were restarted or marked for replacement as needed."
          : "Provider settings saved",
      );
      if (providerChanged) {
        window.dispatchEvent(new CustomEvent("atc:provider-switched"));
      }
    } catch (err) {
      setProviderMessage(
        err instanceof Error ? err.message : "Failed to save provider settings",
      );
      if (providerChanged) {
        window.dispatchEvent(new CustomEvent("atc:provider-switched"));
      }
    } finally {
      setSavingProvider(false);
    }
  }

  async function saveHelperSettings(next: Partial<ProviderHelperSettings>) {
    const optimistic: ProviderHelperSettings = {
      ...helperSettings,
      ...next,
      audit_enabled: true,
    };
    setHelperSettings(optimistic);
    setSavingHelpers(true);
    setHelperMessage(null);
    try {
      const saved = await api.put<ProviderHelperSettings>(
        "/settings/provider-helpers",
        next,
      );
      setHelperSettings(saved);
      setHelperMessage(
        "Provider helper visibility saved. Audit logging remains enabled.",
      );
    } catch (err) {
      setHelperMessage(
        err instanceof Error
          ? err.message
          : "Failed to save provider helper settings",
      );
      setHelperSettings((prev) => ({ ...prev, audit_enabled: true }));
    } finally {
      setSavingHelpers(false);
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
          <h3>Agent Provider</h3>
          <div className="form-group">
            <label htmlFor="provider-default">Global Provider</label>
            <select
              id="provider-default"
              value={providerConfig.default}
              onChange={(e) =>
                void saveProviderConfig({ default: e.target.value })
              }
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
                : "This provider may use a non-terminal control flow even if a visibility pane exists."}{" "}
              Changing the global provider affects all new sessions immediately.
              Existing live Tower, Leader, and Ace sessions will be restarted or
              recreated as needed so stale provider-bound sessions do not
              linger.
            </span>
          </div>

          <div className="form-group">
            <label htmlFor="provider-claude-command">Claude Code Command</label>
            <input
              id="provider-claude-command"
              type="text"
              value={providerConfig.claude_command}
              onChange={(e) =>
                setProviderConfig((prev) => ({
                  ...prev,
                  claude_command: e.target.value,
                }))
              }
              onBlur={() =>
                void saveProviderConfig({
                  claude_command: providerConfig.claude_command,
                })
              }
            />
          </div>

          <div className="form-group">
            <label htmlFor="provider-codex-command">Codex Command</label>
            <input
              id="provider-codex-command"
              type="text"
              value={providerConfig.codex_command}
              onChange={(e) =>
                setProviderConfig((prev) => ({
                  ...prev,
                  codex_command: e.target.value,
                }))
              }
              onBlur={() =>
                void saveProviderConfig({
                  codex_command: providerConfig.codex_command,
                })
              }
            />
          </div>

          <div className="form-group">
            <label htmlFor="provider-opencode-url">OpenCode URL</label>
            <input
              id="provider-opencode-url"
              type="text"
              value={providerConfig.opencode_url}
              onChange={(e) =>
                setProviderConfig((prev) => ({
                  ...prev,
                  opencode_url: e.target.value,
                }))
              }
              onBlur={() =>
                void saveProviderConfig({
                  opencode_url: providerConfig.opencode_url,
                })
              }
            />
          </div>

          <div className="form-group">
            <label htmlFor="provider-tmux-session">tmux Session</label>
            <input
              id="provider-tmux-session"
              type="text"
              value={providerConfig.tmux_session}
              onChange={(e) =>
                setProviderConfig((prev) => ({
                  ...prev,
                  tmux_session: e.target.value,
                }))
              }
              onBlur={() =>
                void saveProviderConfig({
                  tmux_session: providerConfig.tmux_session,
                })
              }
            />
          </div>

          <div
            className="settings-pane__provider-status"
            data-testid="provider-global-status"
          >
            <div className="settings-pane__info-row">
              <span className="settings-pane__label">Global provider</span>
              <span className="settings-pane__value">
                {providerConfig.default}
              </span>
            </div>
            <div className="settings-pane__info-row">
              <span className="settings-pane__label">Active projects</span>
              <span className="settings-pane__value">
                {activeProjects.length}
              </span>
            </div>
            <div className="settings-pane__info-row">
              <span className="settings-pane__label">Live Tower session</span>
              <span className="settings-pane__value">
                {state.towerDetail.current_session_id
                  ? `${activeTowerProject?.name ?? "Global"} (${providerConfig.default})`
                  : "Not running"}
              </span>
            </div>
          </div>

          {providerMessage && (
            <div className="form-hint">{providerMessage}</div>
          )}
        </section>

        <section
          className="panel settings-pane__section"
          data-testid="provider-helper-settings"
        >
          <h3>Provider Helper Subagents</h3>
          <div className="form-group settings-pane__switch-row">
            <div>
              <label htmlFor="provider-helpers-enabled">
                Allow provider helpers
              </label>
              <span className="form-hint">
                Providers may use private/background helper subagents for Tower,
                Leader, or Ace work. Helpers never become operator-visible ATC
                roles.
              </span>
            </div>
            <input
              id="provider-helpers-enabled"
              type="checkbox"
              checked={helperSettings.enabled}
              disabled={savingHelpers}
              onChange={(e) =>
                void saveHelperSettings({ enabled: e.target.checked })
              }
            />
          </div>

          <div className="form-group">
            <label htmlFor="provider-helper-visibility">
              Default Helper Visibility
            </label>
            <select
              id="provider-helper-visibility"
              value={helperSettings.default_visibility}
              disabled={savingHelpers || !helperSettings.enabled}
              onChange={(e) =>
                void saveHelperSettings({
                  default_visibility: e.target
                    .value as ProviderHelperSettings["default_visibility"],
                })
              }
            >
              <option value="hidden">Hidden</option>
              <option value="summary">Summary</option>
              <option value="full">Full</option>
            </select>
            <span className="form-hint">
              {HELPER_VISIBILITY_LABELS[helperSettings.default_visibility]}
            </span>
          </div>

          <div
            className="settings-pane__provider-status"
            data-testid="provider-helper-status"
          >
            <div className="settings-pane__info-row">
              <span className="settings-pane__label">Helpers</span>
              <span className="settings-pane__value">
                {helperSettings.enabled ? "Enabled" : "Disabled"}
              </span>
            </div>
            <div className="settings-pane__info-row">
              <span className="settings-pane__label">Visibility</span>
              <span className="settings-pane__value">
                {helperSettings.default_visibility}
              </span>
            </div>
            <div className="settings-pane__info-row">
              <span className="settings-pane__label">Audit logging</span>
              <span className="settings-pane__value">
                {helperSettings.audit_enabled ? "Always on" : "Unavailable"}
              </span>
            </div>
          </div>

          <span className="form-hint">
            Visibility controls display only. Durable helper audit records are
            still written even when helper work is hidden from normal workflow
            panels.
          </span>
          {helperMessage && <div className="form-hint">{helperMessage}</div>}
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

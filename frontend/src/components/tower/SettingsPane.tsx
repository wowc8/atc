import { useEffect, useMemo, useState } from "react";
import { useAppContext } from "../../context/AppContext";
import { api } from "../../utils/api";
import type { AgentProviderConfig, Project, ProviderInfo } from "../../types";
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
  const { state, dispatch } = useAppContext();
  const [githubOrg, setGithubOrg] = useState(
    () => localStorage.getItem(GITHUB_ORG_KEY) ?? "",
  );
  const [providerConfig, setProviderConfig] = useState<AgentProviderConfig>(EMPTY_PROVIDER_CONFIG);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [savingProvider, setSavingProvider] = useState(false);
  const [providerMessage, setProviderMessage] = useState<string | null>(null);
  const [providerActionProjectId, setProviderActionProjectId] = useState<string>("");
  const [applyingProvider, setApplyingProvider] = useState(false);

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

  const activeProjects = useMemo(
    () => state.projects.filter((project) => project.status === "active"),
    [state.projects],
  );

  const selectedProviderActionProject =
    activeProjects.find((project) => project.id === providerActionProjectId) ?? null;
  const activeTowerProject = state.towerDetail.current_project_id
    ? state.projects.find((project) => project.id === state.towerDetail.current_project_id) ?? null
    : null;
  const terminalBackedProviders = new Set(["claude_code", "codex"]);
  const savedProjectProvider = selectedProviderActionProject?.agent_provider ?? null;
  const defaultMatchesSelectedProject = Boolean(
    savedProjectProvider && providerConfig.default === savedProjectProvider,
  );
  const towerAlreadyOnSelectedProject = Boolean(
    state.towerDetail.current_session_id &&
      activeTowerProject &&
      selectedProviderActionProject &&
      activeTowerProject.id === selectedProviderActionProject.id,
  );
  const towerNeedsRestartForSelectedProject = Boolean(
    selectedProviderActionProject &&
      terminalBackedProviders.has(selectedProviderActionProject.agent_provider) &&
      (!towerAlreadyOnSelectedProject || activeTowerProject?.agent_provider !== selectedProviderActionProject.agent_provider),
  );
  const canRestartTowerWithSelectedProject = Boolean(
    selectedProviderActionProject && terminalBackedProviders.has(selectedProviderActionProject.agent_provider),
  );

  useEffect(() => {
    if (providerActionProjectId) return;
    if (activeTowerProject) {
      setProviderActionProjectId(activeTowerProject.id);
      return;
    }
    if (activeProjects.length > 0) {
      setProviderActionProjectId(activeProjects[0].id);
    }
  }, [providerActionProjectId, activeProjects, activeTowerProject]);

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

  async function handleApplyProviderSettingToProject() {
    if (!providerConfig.default || !providerActionProjectId) return;
    setApplyingProvider(true);
    setProviderMessage(null);
    try {
      const updatedProject = await api.patch<Project>(`/projects/${providerActionProjectId}/agent-provider`, {
        agent_provider: providerConfig.default,
      });
      dispatch({ type: "UPDATE_PROJECT", payload: updatedProject });
      setProviderMessage(
        state.towerDetail.current_session_id && activeTowerProject?.id === providerActionProjectId
          ? "Project provider updated. Restart Tower below to apply it to the live session."
          : "Project provider updated.",
      );
    } catch (err) {
      setProviderMessage(err instanceof Error ? err.message : "Failed to apply provider to project");
    } finally {
      setApplyingProvider(false);
    }
  }

  async function handleRestartTowerForSelectedProject() {
    if (!providerActionProjectId || !canRestartTowerWithSelectedProject) return;
    setApplyingProvider(true);
    setProviderMessage(null);
    try {
      if (state.towerDetail.current_session_id) {
        await api.post("/tower/stop");
      }
      const res = await api.post<{ session_id?: string }>("/tower/start", {
        project_id: providerActionProjectId,
      });
      setProviderMessage("Tower restarted with the selected project provider.");
      dispatch({
        type: "SET_TOWER_DETAIL",
        payload: {
          state: "managing",
          current_session_id: res.session_id ?? null,
          current_project_id: providerActionProjectId,
          current_goal: null,
          leader_session_id: null,
        },
      });
    } catch (err) {
      setProviderMessage(err instanceof Error ? err.message : "Failed to restart Tower");
    } finally {
      setApplyingProvider(false);
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

          <div className="settings-pane__provider-actions">
            <div className="form-group">
              <label htmlFor="provider-action-project">Apply default provider to project</label>
              <select
                id="provider-action-project"
                value={providerActionProjectId}
                onChange={(e) => setProviderActionProjectId(e.target.value)}
                disabled={applyingProvider || activeProjects.length === 0}
                data-testid="provider-action-project"
              >
                <option value="" disabled>
                  Select active project...
                </option>
                {activeProjects.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.name}
                  </option>
                ))}
              </select>
              <span className="form-hint">
                Changing the default provider only affects new projects. Use these actions to update an existing project and, if needed, restart the live Tower session with that project context.
              </span>
            </div>

            {selectedProviderActionProject && (
              <div className="settings-pane__provider-status" data-testid="provider-action-status">
                <div className="settings-pane__info-row">
                  <span className="settings-pane__label">Default provider</span>
                  <span className="settings-pane__value">{providerConfig.default}</span>
                </div>
                <div className="settings-pane__info-row">
                  <span className="settings-pane__label">Selected project provider</span>
                  <span className="settings-pane__value">{savedProjectProvider}</span>
                </div>
                <div className="settings-pane__info-row">
                  <span className="settings-pane__label">Live Tower session</span>
                  <span className="settings-pane__value">
                    {state.towerDetail.current_session_id
                      ? `${activeTowerProject?.name ?? "Unknown"} (${activeTowerProject?.agent_provider ?? "unknown"})`
                      : "Not running"}
                  </span>
                </div>
              </div>
            )}

            <div className="settings-pane__provider-action-buttons">
              <button
                className="btn btn-sm"
                onClick={() => void handleApplyProviderSettingToProject()}
                disabled={applyingProvider || !providerActionProjectId || defaultMatchesSelectedProject}
                data-testid="provider-apply-project"
              >
                {applyingProvider ? "Applying..." : defaultMatchesSelectedProject ? "Project already matches default" : "Apply default to project"}
              </button>
              <button
                className="btn btn-sm"
                onClick={() => void handleRestartTowerForSelectedProject()}
                disabled={applyingProvider || !canRestartTowerWithSelectedProject || !towerNeedsRestartForSelectedProject}
                data-testid="provider-restart-tower"
              >
                {applyingProvider ? "Applying..." : towerNeedsRestartForSelectedProject ? "Restart Tower with selected project" : "Tower already matches selected project"}
              </button>
            </div>

            <span className="form-hint">
              {towerNeedsRestartForSelectedProject
                ? "The saved project provider and the live Tower session are still distinct. Restart Tower after applying if you want the visible session to move over too."
                : "The selected project and live Tower session already line up, so no restart is needed right now."}
            </span>
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

import ContextHub from "../components/context/ContextHub";
import "./ContextPage.css";

export default function ContextPage() {
  return (
    <div className="context-page" data-testid="context-page">
      <h1>Context</h1>
      <div className="panel">
        <ContextHub
          scope="global"
          showScopeTabs
          availableScopes={["global", "project", "tower", "leader", "ace"]}
        />
      </div>
    </div>
  );
}

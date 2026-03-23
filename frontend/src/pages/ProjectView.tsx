import { useState, useCallback, useRef, useEffect } from "react";
import { useParams } from "react-router-dom";
import { useAppContext } from "../context/AppContext";
import StatusBadge from "../components/common/StatusBadge";
import LeaderConsole from "../components/leader/LeaderConsole";
import TaskBoard from "../components/leader/TaskBoard";
import AceList from "../components/ace/AceList";
import AceConsole from "../components/ace/AceConsole";
import ContextHub from "../components/context/ContextHub";
import ResizeHandle from "../components/common/ResizeHandle";
import "./ProjectView.css";

const MIN_LEFT = 280;
const MIN_RIGHT = 400;
const MIN_TASKS = 120;
const MIN_CTX = 80;
const MAX_CTX = 400;

function readStorage(key: string, fallback: number): number {
  const v = localStorage.getItem(key);
  return v !== null ? Number(v) : fallback;
}

export default function ProjectView() {
  const { id } = useParams<{ id: string }>();
  const { state, fetchAll } = useAppContext();
  const { projects, sessions, leaders, taskGraphs } = state;

  const project = projects.find((p) => p.id === id);
  const projectSessions = sessions.filter((s) => s.project_id === id);
  const leader = id ? leaders[id] : undefined;
  const projectTaskGraphs = id ? (taskGraphs[id] ?? []) : [];

  // null = collapsed Ace list, string = expanded Ace panel with tabs
  const [expandedAceId, setExpandedAceId] = useState<string | null>(null);

  // If the expanded Ace was removed, collapse back to list
  const expandedAceExists =
    expandedAceId !== null &&
    projectSessions.some((s) => s.id === expandedAceId);
  const activeAceId = expandedAceExists ? expandedAceId : null;

  // Resize state — pixel sizes persisted to localStorage
  const [leftWidth, setLeftWidth] = useState(() =>
    readStorage("atc:pv:split", Math.round(window.innerWidth * 0.4))
  );
  const [tasksHeight, setTasksHeight] = useState(() =>
    readStorage("atc:pv:tasks-h", 240)
  );
  const [ctxHeight, setCtxHeight] = useState(() =>
    readStorage("atc:pv:ctx-h", 180)
  );

  const layoutRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    localStorage.setItem("atc:pv:split", String(leftWidth));
  }, [leftWidth]);
  useEffect(() => {
    localStorage.setItem("atc:pv:tasks-h", String(tasksHeight));
  }, [tasksHeight]);
  useEffect(() => {
    localStorage.setItem("atc:pv:ctx-h", String(ctxHeight));
  }, [ctxHeight]);

  const handleSplitDrag = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      const startX = e.clientX;
      const startWidth = leftWidth;

      const onMove = (ev: MouseEvent) => {
        const containerWidth =
          layoutRef.current?.offsetWidth ?? window.innerWidth;
        const next = Math.max(
          MIN_LEFT,
          Math.min(
            startWidth + ev.clientX - startX,
            containerWidth - MIN_RIGHT - 4
          )
        );
        setLeftWidth(next);
      };

      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };

      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    [leftWidth]
  );

  const handleTasksDrag = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      const startY = e.clientY;
      const startHeight = tasksHeight;

      const onMove = (ev: MouseEvent) => {
        const next = Math.max(MIN_TASKS, startHeight + ev.clientY - startY);
        setTasksHeight(next);
      };

      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };

      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    [tasksHeight]
  );

  const handleCtxDrag = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      const startY = e.clientY;
      const startHeight = ctxHeight;

      const onMove = (ev: MouseEvent) => {
        // Drag up (negative delta) → context grows; drag down → context shrinks
        const next = Math.max(
          MIN_CTX,
          Math.min(MAX_CTX, startHeight - (ev.clientY - startY))
        );
        setCtxHeight(next);
      };

      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      };

      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    [ctxHeight]
  );

  if (!project) {
    return (
      <div className="project-view" data-testid="project-view">
        <div className="project-view__empty">Project not found.</div>
      </div>
    );
  }

  return (
    <div className="project-view" data-testid="project-view">
      {/* Header */}
      <div className="project-view__header">
        <div className="project-view__title">
          <h1>{project.name}</h1>
          <StatusBadge status={project.status} />
        </div>
        {project.description && (
          <p className="project-view__desc">{project.description}</p>
        )}
      </div>

      {/* Main layout: top row (left+right) stacked above full-width context */}
      <div className="project-view__layout" ref={layoutRef}>
        {/* Top row: left column + vertical handle + right column */}
        <div className="project-view__top">
          {/* Left column: tasks + aces */}
          <aside className="project-view__left" style={{ width: leftWidth }}>
            <div
              className="panel project-view__tasks"
              style={{ height: tasksHeight }}
            >
              <TaskBoard
                projectId={project.id}
                taskGraphs={projectTaskGraphs}
                onRefresh={fetchAll}
              />
            </div>

            <ResizeHandle direction="row" onMouseDown={handleTasksDrag} />

            {/* Aces panel: collapses to list or expands to tabbed console IN-PLACE */}
            <div
              className={`panel project-view__aces${activeAceId ? " project-view__aces--expanded" : ""}`}
            >
              {activeAceId ? (
                <AceConsole
                  projectId={project.id}
                  sessions={projectSessions}
                  activeAceId={activeAceId}
                  onRefresh={fetchAll}
                  onSelectAce={(sid) => setExpandedAceId(sid)}
                  onCollapse={() => setExpandedAceId(null)}
                />
              ) : (
                <AceList
                  projectId={project.id}
                  sessions={projectSessions}
                  onRefresh={fetchAll}
                  onExpand={(sid) => setExpandedAceId(sid)}
                  compact
                />
              )}
            </div>
          </aside>

          <ResizeHandle direction="col" onMouseDown={handleSplitDrag} />

          {/* Right column — Leader always here, fills full height of top row */}
          <main className="project-view__right">
            <div className="panel project-view__leader">
              <LeaderConsole
                projectId={project.id}
                leader={leader}
                project={project}
                onRefresh={fetchAll}
              />
            </div>
          </main>
        </div>

        {/* Context resize handle — sits at top edge of context panel */}
        <ResizeHandle direction="row" onMouseDown={handleCtxDrag} />

        {/* Context panel — full width, resizable height */}
        <div
          className="panel project-view__context"
          style={{ height: ctxHeight }}
        >
          <ContextHub
            scope="project"
            projectId={project.id}
            showScopeTabs={false}
          />
        </div>
      </div>
    </div>
  );
}

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TaskBoard from "../TaskBoard";
import type { TaskGraph } from "../../../types";

// Mock api module
vi.mock("../../../utils/api", () => ({
  api: {
    post: vi.fn().mockResolvedValue({}),
    patch: vi.fn().mockResolvedValue({}),
    delete: vi.fn().mockResolvedValue(undefined),
  },
}));

const mockRefresh = vi.fn().mockResolvedValue(undefined);

const sampleTasks: TaskGraph[] = [
  {
    id: "t1",
    project_id: "p1",
    title: "Task One",
    description: "First task",
    status: "todo",
    assigned_ace_id: "ace-abcdef12",
    dependencies: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "t2",
    project_id: "p1",
    title: "Task Two",
    description: null,
    status: "in_progress",
    assigned_ace_id: null,
    dependencies: ["t1"],
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "t3",
    project_id: "p1",
    title: "Task Three",
    description: "Done task",
    status: "done",
    assigned_ace_id: null,
    dependencies: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
];

beforeEach(() => {
  vi.clearAllMocks();
});

describe("TaskBoard", () => {
  it("renders the task board", () => {
    render(
      <TaskBoard
        projectId="p1"
        taskGraphs={[]}
        onRefresh={mockRefresh}
      />,
    );
    expect(screen.getByTestId("task-board")).toBeInTheDocument();
  });

  it("shows empty message when no tasks", () => {
    render(
      <TaskBoard
        projectId="p1"
        taskGraphs={[]}
        onRefresh={mockRefresh}
      />,
    );
    expect(screen.getByText("No tasks yet.")).toBeInTheDocument();
  });

  it("renders kanban view by default", () => {
    render(
      <TaskBoard
        projectId="p1"
        taskGraphs={sampleTasks}
        onRefresh={mockRefresh}
      />,
    );
    expect(screen.getByTestId("kanban-view")).toBeInTheDocument();
  });

  it("shows task cards in kanban columns", () => {
    render(
      <TaskBoard
        projectId="p1"
        taskGraphs={sampleTasks}
        onRefresh={mockRefresh}
      />,
    );
    const cards = screen.getAllByTestId("task-card");
    expect(cards).toHaveLength(3);
    expect(screen.getByText("Task One")).toBeInTheDocument();
    expect(screen.getByText("Task Two")).toBeInTheDocument();
    expect(screen.getByText("Task Three")).toBeInTheDocument();
  });

  it("shows assignee on card", () => {
    render(
      <TaskBoard
        projectId="p1"
        taskGraphs={sampleTasks}
        onRefresh={mockRefresh}
      />,
    );
    expect(screen.getByText("ace-abcd")).toBeInTheDocument();
  });

  it("shows description on card", () => {
    render(
      <TaskBoard
        projectId="p1"
        taskGraphs={sampleTasks}
        onRefresh={mockRefresh}
      />,
    );
    expect(screen.getByText("First task")).toBeInTheDocument();
  });

  it("toggles to table view", async () => {
    const user = userEvent.setup();
    render(
      <TaskBoard
        projectId="p1"
        taskGraphs={sampleTasks}
        onRefresh={mockRefresh}
      />,
    );
    await user.click(screen.getByTestId("view-toggle-table"));
    expect(screen.getByTestId("table-view")).toBeInTheDocument();
    expect(screen.queryByTestId("kanban-view")).not.toBeInTheDocument();
  });

  it("toggles back to kanban view", async () => {
    const user = userEvent.setup();
    render(
      <TaskBoard
        projectId="p1"
        taskGraphs={sampleTasks}
        onRefresh={mockRefresh}
      />,
    );
    await user.click(screen.getByTestId("view-toggle-table"));
    expect(screen.getByTestId("table-view")).toBeInTheDocument();

    await user.click(screen.getByTestId("view-toggle-kanban"));
    expect(screen.getByTestId("kanban-view")).toBeInTheDocument();
  });

  it("shows table rows with task data", async () => {
    const user = userEvent.setup();
    render(
      <TaskBoard
        projectId="p1"
        taskGraphs={sampleTasks}
        onRefresh={mockRefresh}
      />,
    );
    await user.click(screen.getByTestId("view-toggle-table"));

    const tableView = screen.getByTestId("table-view");
    expect(within(tableView).getByText("Task One")).toBeInTheDocument();
    expect(within(tableView).getByText("Task Two")).toBeInTheDocument();
    expect(within(tableView).getByText("Task Three")).toBeInTheDocument();
  });

  it("shows view toggle controls", () => {
    render(
      <TaskBoard
        projectId="p1"
        taskGraphs={sampleTasks}
        onRefresh={mockRefresh}
      />,
    );
    expect(screen.getByTestId("view-toggle")).toBeInTheDocument();
    expect(screen.getByTestId("view-toggle-kanban")).toBeInTheDocument();
    expect(screen.getByTestId("view-toggle-table")).toBeInTheDocument();
  });

  it("shows add task button", () => {
    render(
      <TaskBoard
        projectId="p1"
        taskGraphs={sampleTasks}
        onRefresh={mockRefresh}
      />,
    );
    expect(screen.getByTestId("add-task-btn")).toBeInTheDocument();
  });

  it("opens create form on add click", async () => {
    const user = userEvent.setup();
    render(
      <TaskBoard
        projectId="p1"
        taskGraphs={sampleTasks}
        onRefresh={mockRefresh}
      />,
    );
    await user.click(screen.getByTestId("add-task-btn"));
    expect(screen.getByTestId("create-form")).toBeInTheDocument();
    expect(screen.getByTestId("create-input")).toBeInTheDocument();
  });

  it("creates a task on submit", async () => {
    const { api } = await import("../../../utils/api");
    const user = userEvent.setup();
    render(
      <TaskBoard
        projectId="p1"
        taskGraphs={[]}
        onRefresh={mockRefresh}
      />,
    );
    await user.click(screen.getByTestId("add-task-btn"));
    await user.type(screen.getByTestId("create-input"), "New task");
    await user.click(screen.getByTestId("create-submit"));

    expect(api.post).toHaveBeenCalledWith("/projects/p1/task-graphs", {
      title: "New task",
    });
    expect(mockRefresh).toHaveBeenCalled();
  });

  it("shows column headers in kanban view", () => {
    render(
      <TaskBoard
        projectId="p1"
        taskGraphs={sampleTasks}
        onRefresh={mockRefresh}
      />,
    );
    // Column headers are in h4 elements
    const kanban = screen.getByTestId("kanban-view");
    const headings = within(kanban).getAllByRole("heading", { level: 4 });
    const headingTexts = headings.map((h) => h.textContent);
    expect(headingTexts).toContain("Todo1");
    expect(headingTexts).toContain("In Progress1");
    expect(headingTexts).toContain("Done1");
  });
});

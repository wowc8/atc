import type { Task } from "../types";

export interface MilestoneStatus {
  completed: number;
  current: number;
  label: string;
}

/** Extract milestone number from a task title prefix like "M1:", "M2 -", "[M3]", etc. */
function parseMilestone(title: string): number | null {
  const match = title.match(/^\[?[Mm](\d+)[^\d]?/);
  return match && match[1] ? parseInt(match[1], 10) : null;
}

/**
 * Derive milestone status from a project's task list.
 * Groups tasks by milestone number, finds the highest fully-done milestone,
 * and returns a human-readable label.
 */
export function getProjectMilestoneStatus(tasks: Task[]): MilestoneStatus {
  // Group tasks by milestone number
  const byMilestone = new Map<number, Task[]>();

  for (const task of tasks) {
    const m = parseMilestone(task.title);
    if (m !== null) {
      const group = byMilestone.get(m) ?? [];
      group.push(task);
      byMilestone.set(m, group);
    }
  }

  if (byMilestone.size === 0) {
    return { completed: 0, current: 0, label: "planning" };
  }

  const milestones = [...byMilestone.keys()].sort((a, b) => a - b);

  // Highest milestone where every task is done
  let completed = 0;
  for (const m of milestones) {
    const taskList = byMilestone.get(m)!;
    if (taskList.every((t) => t.status === "done" || t.status === "cancelled")) {
      completed = m;
    } else {
      break;
    }
  }

  // Current milestone = next after completed, or first if none done
  const firstIncomplete = milestones.find((m) => m > completed);
  const current = firstIncomplete ?? milestones[milestones.length - 1] ?? 0;

  let label: string;
  if (completed === 0 && current === 0) {
    label = "planning";
  } else if (completed === current) {
    label = `M${completed} done`;
  } else if (completed > 0) {
    label = `M${completed} done · working M${current}`;
  } else {
    label = `M${current} · in progress`;
  }

  return { completed, current, label };
}

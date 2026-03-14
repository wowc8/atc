# Patterns

> Canonical implementations for common tasks. AI agents must follow these exactly.

## Creating a New Session (DB-First)

```python
# 1. Write session row to DB FIRST
session = Session(id=uuid4(), status="connecting", ...)
await db.insert_session(session)

# 2. Publish creation event
await event_bus.publish("session_created", session)

# 3. Spawn tmux pane
try:
    pane = await spawn_tmux_pane(session)
    await db.update_session(session.id, status="idle", tmux_pane=pane)
except Exception as e:
    await db.update_session(session.id, status="error")
    await failure_log(level="error", category="creation_failure", ...)
    raise
```

## Sending Instructions to a Pane

```python
async def send_instruction(pane: str, text: str):
    await check_tui_ready(pane)          # verify alternate_on == False
    await tmux_send_keys(pane, text)
    await tmux_send_keys(pane, "Enter")  # no await gap between these two
    await verify_instruction_received(pane, text)  # capture-pane after 2s
```

## Writing a New API Endpoint

1. Add route to the appropriate router in `src/atc/api/routers/`
2. Use Pydantic models for request/response
3. Return proper HTTP status codes
4. Log errors via `failure_log()`, not `print()`

```python
@router.get("/{id}")
async def get_project(id: str) -> ProjectResponse:
    project = await db.get_project(id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectResponse.from_model(project)
```

## Writing a New Migration

```bash
./scripts/new_migration.sh add_my_table
```

Migration rules:
- Use `CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`
- All tables have `created_at TEXT NOT NULL` and `updated_at TEXT NOT NULL`
- Never edit existing migration files
- Include a comment explaining what this migration does

## Adding a New WebSocket Channel

1. Register the channel name in `src/atc/api/ws/hub.py`
2. Publish from the relevant event handler in core
3. Subscribe from the frontend in `AppContext.tsx`

## Calling failure_log()

```python
from atc.core.failure_log import failure_log

try:
    await risky_operation()
except SpecificError as e:
    await failure_log(
        level="error",
        category="creation_failure",
        message=f"Failed to spawn pane for {session_id}",
        entity_type="ace",
        entity_id=session_id,
        project_id=project_id,
        context={"command": cmd, "exit_code": rc},
        exc=e,
    )
```

## Frontend Component Pattern

```tsx
interface Props {
  projectId: string;
}

export default function MyComponent({ projectId }: Props) {
  const { data, isLoading } = useQuery({
    queryKey: ["my-data", projectId],
    queryFn: () => fetchMyData(projectId),
  });

  if (isLoading) return <div>Loading...</div>;
  return <div>{/* render data */}</div>;
}
```

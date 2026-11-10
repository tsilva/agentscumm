# Optional room scanning

Use a short hover survey when identifying ambiguous objects or exits is likely to avoid failed actions or repeated searches. Choose plausible targets from a fresh screenshot and the current objective. Clear targets and previously confirmed, unchanged views usually need no scan. Avoid exhaustive grids and rescanning every visit: movements and images also cost actions, tokens and time.

## Establish the room and view

Recognize rooms from distinctive layout, landmarks and known connections. Assign a short stable name, such as `warehouse-office`; this is the agent's memory label, not an engine-provided ID. Track changed objects or characters as room state. A guard leaving or a safe opening need not create a new room identity.

Keep scrolling positions or camera angles as separate views of the same room. One representative saved screenshot per useful view is generally sufficient; a collage is optional. Associate coordinates with the view and screenshot dimensions, including any black borders. After scrolling, camera movement, scene transitions, resolution changes or a save restore, reobserve and match the layout before reusing coordinates. Reconfirm moving or changed targets even when the background matches.

## Collect hover evidence

Use the game's normal hover labels if it provides them. If label behavior is unknown, try one plausible target first to establish whether hovering reveals readable text and which normal verb/cursor mode applies. A blank or unreadable result does not prove there is no object. Some games require another ordinary interaction to identify objects; do not keep scanning for labels the game does not expose.

For independent candidates in a stable view, submit one `act` containing repeated `move → wait → screenshot` groups. The wait gives the game a chance to update its label before capture. Start with a short delay, such as 200 ms, and adjust from observed results. Include a screenshot step after each candidate.

Example for two candidates. Use the active session and pick the coordinates from a fresh screenshot.

```json
{
  "session_id": "QZ7mK3tV2pA",
  "request_id": "F4nRq8D1zUw",
  "actions": [
    { "type": "move", "x": 320, "y": 180 },
    { "type": "wait", "ms": 200 },
    { "type": "screenshot" },
    { "type": "move", "x": 480, "y": 260 },
    { "type": "wait", "ms": 200 },
    { "type": "screenshot" }
  ]
}
```

Respect the current schema limits: presently 32 steps, eight explicit screenshots, and a 20-second combined hold/wait budget. Use a small useful batch, not the maximum by default. Keep clicks and keys that might alter the scene outside a hover survey. Avoid edge positions that trigger scrolling unless that is the intended next operation. A batch cannot adapt to its intermediate images; end it before an action whose result determines the next target.

## Interpret the returned captures

Read screenshot results from the ordered `steps` array. Entries with `frame_id` and `capture_ms` correspond to attached images in the same order. In the example, zero-based steps 2 and 5 produce the two images.

Read the exact visible label and associate it with that group's pointer coordinates. Distinguish confirmed labels from visual guesses; a label identifies a target, not its correct verb, item, prerequisite or puzzle solution. Check that text has updated rather than carrying over from the previous hover. Repeated points yielding the same object label may be the same hotspot, not separate objects. If a label is absent or unclear, try a targeted adjustment or longer dwell only when useful; do not invent a name or enumerate every pixel.

If the view changes during the batch, stop interpreting later coordinates against the old view and obtain a fresh observation. If the batch fails, use only steps and captures actually returned. Retry an uncertain response with the identical request ID and arguments to retrieve that attempt; use a new request ID for a changed survey.

## Keep useful room knowledge

Summarize useful discoveries in the run's existing free-form `memory.md`. Include enough to reuse or verify them: room name and landmarks, view/reference screenshot, confirmed label and coordinates, relevant state or prerequisites, and evidence of failed or successful interactions. Store uncertain identifications as uncertain. No fixed room schema or separate file per room is required.

Save valuable returned images under this run's `screenshots/` and reference their paths; rolling frame IDs alone are not durable evidence. Keep the current room and immediate plan in context, with older room knowledge in the run files. On revisits or after context compaction, retrieve the relevant notes and representative image when needed. Update memory at meaningful discoveries or state changes rather than after each hover. Keep inventory items and page/slot changes distinct from room hotspots.

The benefit comes from reusing confirmed targets and avoiding mistakes. Batching reduces round trips, but a survey still adds pointer movements and observations; do not claim fewer underlying game inputs or a measured speedup merely because they fit in one call.

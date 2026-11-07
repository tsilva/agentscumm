import { readFile } from 'node:fs/promises';
import { z } from 'zod';

const slug = z.string().regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/);
const bankPredicate = z.object({
  bank: z.string().regex(/^[a-z][a-z0-9_]{0,39}$/),
  index: z.number().int().min(0).max(4095),
  equals: z.union([z.number().int(), z.boolean()]),
}).strict();
const roomPredicate = z.object({
  room_id: z.number().int().nonnegative(),
  room_ready: z.boolean().optional(),
}).strict();
const mapSchema = z.object({
  schema_version: z.literal(1),
  game: slug,
  engine_id: z.string().min(1).max(80),
  milestones: z.array(z.object({
    id: slug,
    label: z.string().min(1).max(200),
    when: z.union([bankPredicate, roomPredicate]),
  }).strict()).min(1).max(100),
}).strict();

export async function loadGameProgress(game) {
  slug.parse(game);
  let source;
  try {
    source = await readFile(new URL(`./progress-maps/${game}.json`, import.meta.url), 'utf8');
  } catch (error) {
    if (error.code === 'ENOENT') return;
    throw error;
  }
  const map = mapSchema.parse(JSON.parse(source));
  if (map.game !== game || new Set(map.milestones.map(({ id }) => id)).size !== map.milestones.length) {
    throw new Error(`Invalid game or duplicate milestone IDs in progress map: ${game}`);
  }
  return new GameProgress(map);
}

class GameProgress {
  constructor(map) {
    this.map = map;
    this.variableMilestones = map.milestones.filter(({ when }) => 'bank' in when);
    this.roomMilestones = map.milestones.filter(({ when }) => 'room_id' in when);
    this.reached = new Set();
    this.variablesAvailable = this.variableMilestones.length === 0;
    this.roomsAvailable = false;
  }

  accept(engineId, rooms, gameEvents) {
    this.engineMatches = engineId === this.map.engine_id;
    if (!this.engineMatches) return;
    // The provider queues rooms and writes separately on the same native clock.
    const records = [...gameEvents, ...rooms].sort((a, b) => a.native_time_ms - b.native_time_ms);
    for (const record of records) {
      if (record.event === 'game_variables_baseline') {
        this.reached.clear();
        this.variablesAvailable = this.variableMilestones.every(({ when }) =>
          typeof record.banks[when.bank]?.[when.index] === typeof when.equals);
        if (this.variablesAvailable) for (const { id, when } of this.variableMilestones) {
          if (record.banks[when.bank][when.index] === when.equals) this.reached.add(id);
        }
      } else if (record.event === 'game_variables_change') {
        if (this.variablesAvailable) for (const { id, when } of this.variableMilestones) {
          if (record.bank === when.bank && record.index === when.index && record.value === when.equals) this.reached.add(id);
        }
      } else {
        this.roomsAvailable = true;
        for (const { id, when } of this.roomMilestones) {
          if (record.room_id === when.room_id &&
            (when.room_ready === undefined || record.room_ready === when.room_ready)) this.reached.add(id);
        }
      }
    }
  }

  report() {
    const { game, milestones } = this.map;
    const info = { game, total: milestones.length };
    if (this.engineMatches === false) return { ...info, state: 'unavailable', reason: 'engine_mismatch' };
    if (!this.variablesAvailable) return { ...info, state: 'unavailable', reason: 'missing_game_variables' };
    if (!this.variableMilestones.length && !this.roomsAvailable) {
      return { ...info, state: 'unavailable', reason: 'missing_room_telemetry' };
    }
    const reached = milestones.filter(({ id }) => this.reached.has(id));
    return { ...info, state: 'tracked', completed: reached.length,
      percent: Math.round(reached.length * 100 / milestones.length),
      latest_milestone: reached.at(-1)?.label ?? null,
      reached: reached.map(({ id }) => id) };
  }
}

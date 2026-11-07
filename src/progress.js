import { readFile } from 'node:fs/promises';
import { parse } from 'yaml';
import { z } from 'zod';

const slug = z.string().regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/);
const roomId = z.number().int().nonnegative();
const bankIndex = z.number().int().min(0).max(4095);
const integer = z.number().int();
const mask = integer.min(1).max(0x7fffffff);
const equals = z.union([integer, z.boolean()]);
const oneOf = z.array(integer).min(1).max(16);
const bankName = z.string().regex(/^[a-z][a-z0-9_]{0,39}$/);
const bankSlot = z.object({
  bank: bankName,
  index: bankIndex,
}).strict();
const keyedSlot = z.object({ bank: bankName, key: z.string().min(1).max(80) }).strict();
const flagSlot = bankSlot.extend({
  mask: mask.refine(value => (value & (value - 1)) === 0, 'Expected a single-bit mask'),
});
const bankPredicate = z.union([
  bankSlot.extend({ equals }),
  bankSlot.extend({ mask, equals: integer }),
  bankSlot.extend({ one_of: oneOf }),
]);
const roomPredicate = z.object({
  room_ready: z.boolean().optional(),
});
const predicate = z.union([
  bankPredicate,
  roomPredicate.extend({ room: slug }).strict(),
  roomPredicate.extend({ room_id: roomId }).strict(),
  z.object({ variable: slug, equals }).strict(),
  z.object({ variable: slug, one_of: oneOf }).strict(),
  z.object({ flag: slug, equals: z.boolean().default(true) }).strict(),
  z.object({ object: slug, equals: integer }).strict(),
  z.object({ object: slug, one_of: oneOf }).strict(),
  z.object({ milestone: slug }).strict(),
  z.object({ all: z.array(z.lazy(() => predicate)).min(1).max(16) }).strict(),
  z.object({ any: z.array(z.lazy(() => predicate)).min(1).max(16) }).strict(),
]);
const mapSchema = z.object({
  schema_version: z.literal(1),
  game: slug,
  engine_id: z.string().min(1).max(80),
  edition: z.object({ game_id: slug, index_md5: z.string().regex(/^[a-f0-9]{32}$/) }).strict().optional(),
  scoring: z.literal('highest_stage').optional(),
  telemetry: z.literal('snapshot').optional(),
  rooms: z.record(slug, roomId).optional(),
  variables: z.record(slug, z.union([bankSlot, keyedSlot])).optional(),
  flags: z.record(slug, flagSlot).optional(),
  objects: z.record(slug, bankIndex).optional(),
  milestones: z.array(z.object({
    id: slug,
    label: z.string().min(1).max(200),
    when: predicate,
  }).strict()).min(1).max(100),
}).strict();

export async function loadGameProgress(game) {
  slug.parse(game);
  let source;
  try {
    source = await readFile(new URL(`./game-internals/${game}.yaml`, import.meta.url), 'utf8');
  } catch (error) {
    if (error.code === 'ENOENT') return;
    throw error;
  }
  const map = mapSchema.parse(parse(source, { maxAliasCount: 0, stringKeys: true }));
  const ids = new Set();
  for (const milestone of map.milestones) {
    const { id, when } = milestone;
    if (map.game !== game || ids.has(id) || leaves(when).some(rule => rule.milestone && !ids.has(rule.milestone))) {
      throw new Error(`Invalid game, milestone ID or milestone reference in game internals map: ${game}`);
    }
    milestone.when = resolveReferences(when, map, id);
    ids.add(id);
  }
  return new GameProgress(map);
}

function resolveReferences(rule, map, milestone) {
  const lookup = (section, label) => {
    const entries = map[section];
    if (!entries || !Object.hasOwn(entries, label)) {
      throw new Error(`Unknown ${section.slice(0, -1)} "${label}" in milestone "${milestone}" for game internals map: ${map.game}`);
    }
    return entries[label];
  };
  if (rule.all) return { all: rule.all.map(child => resolveReferences(child, map, milestone)) };
  if (rule.any) return { any: rule.any.map(child => resolveReferences(child, map, milestone)) };
  if ('room' in rule) {
    const { room, ...condition } = rule;
    return { ...condition, room_id: lookup('rooms', room) };
  }
  if ('variable' in rule) {
    const { variable, ...condition } = rule;
    const slot = lookup('variables', variable);
    if ('key' in slot && map.telemetry !== 'snapshot') {
      throw new Error(`Variable "${variable}" in game internals map ${map.game} requires snapshot telemetry`);
    }
    return { ...slot, ...condition };
  }
  if ('flag' in rule) {
    const flag = lookup('flags', rule.flag);
    return { ...flag, equals: rule.equals ? flag.mask : 0 };
  }
  if ('object' in rule) {
    const { object, ...condition } = rule;
    return { bank: 'object_state', index: lookup('objects', object), ...condition };
  }
  return rule;
}

function leaves(rule) {
  const children = rule.all ?? rule.any;
  return children ? children.flatMap(leaves) : [rule];
}

class GameProgress {
  constructor(map) {
    this.map = map;
    this.variableRules = map.milestones.flatMap(({ when }) => leaves(when)).filter(rule => 'bank' in rule);
    this.reached = new Set();
    this.variablesAvailable = this.variableRules.length === 0;
  }

  get usesSnapshots() {
    return this.map.telemetry === 'snapshot';
  }

  acceptSnapshot(identity, snapshot) {
    const { banks, room_id, room_ready, sample_time_ms: native_time_ms } = snapshot;
    const rooms = room_id === null ? [] : [{ room_id, room_ready, native_time_ms }];
    // Rebuild from the sampled save state, including after an in-game load.
    this.accept(identity.engine_id, rooms, [{ event: 'game_variables_baseline', native_time_ms, banks }], identity);
  }

  matches(rule, room) {
    if (rule.all) return rule.all.every(child => this.matches(child, room));
    if (rule.any) return rule.any.some(child => this.matches(child, room));
    if (rule.milestone) return this.reached.has(rule.milestone);
    if ('room_id' in rule) return room?.room_id === rule.room_id &&
      (rule.room_ready === undefined || room.room_ready === rule.room_ready);
    const value = this.banks?.[rule.bank]?.[rule.key ?? rule.index];
    if (rule.one_of) return rule.one_of.includes(value);
    if (rule.mask) return typeof value === 'number' && (value & rule.mask) === rule.equals;
    return value === rule.equals;
  }

  accept(engineId, rooms, gameEvents, identity) {
    this.engineMatches = engineId === this.map.engine_id;
    this.editionMatches = !this.map.edition || (identity?.engine_id === engineId &&
      identity.game_id === this.map.edition.game_id && identity.index_md5 === this.map.edition.index_md5);
    if (!this.engineMatches || !this.editionMatches) return;
    // The provider queues rooms and writes separately on the same native clock.
    const records = [...gameEvents, ...rooms].sort((a, b) => a.native_time_ms - b.native_time_ms);
    for (const record of records) {
      if (record.event === 'game_variables_baseline') {
        this.reached.clear();
        this.room = undefined;
        this.banks = structuredClone(record.banks);
        this.variablesAvailable = this.variableRules.every(rule =>
          typeof this.banks[rule.bank]?.[rule.key ?? rule.index] === typeof (rule.equals ?? rule.one_of[0]));
      } else if (record.event === 'game_variables_change') {
        if (this.banks) this.banks[record.bank][record.index] = record.value;
      } else {
        this.room = record;
      }
      for (const milestone of this.map.milestones) {
        // A bank write can match independently without reusing a stale room sample.
        if (this.variablesAvailable && this.matches(milestone.when, record.event ? undefined : record)) {
          this.reached.add(milestone.id);
        }
      }
    }
  }

  report() {
    const { game, milestones } = this.map;
    const info = { game, total: milestones.length };
    if (this.engineMatches === false) return { ...info, state: 'unavailable', reason: 'engine_mismatch' };
    if (this.editionMatches === false) return { ...info, state: 'unavailable', reason: 'edition_mismatch' };
    if (!this.variablesAvailable) return { ...info, state: 'unavailable', reason: 'missing_game_variables' };
    if (!this.variableRules.length && !this.room) {
      return { ...info, state: 'unavailable', reason: 'missing_room_telemetry' };
    }
    const reached = this.earnedMilestones;
    return { ...info, state: 'tracked', completed: reached.length,
      percent: Math.round(reached.length * 100 / milestones.length),
      latest_milestone: reached.at(-1)?.label ?? null,
      reached: reached.map(({ id }) => id) };
  }

  get nextMilestone() {
    const reached = this.earnedMilestones;
    return this.map.milestones.find(milestone => !reached.includes(milestone))?.label ?? null;
  }

  get earnedMilestones() {
    const { milestones, scoring } = this.map;
    return scoring === 'highest_stage'
      ? milestones.slice(0, milestones.findLastIndex(({ id }) => this.reached.has(id)) + 1)
      : milestones.filter(({ id }) => this.reached.has(id));
  }
}

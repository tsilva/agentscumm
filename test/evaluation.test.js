import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { once } from 'node:events';
import { RunEvaluation, parseCondition } from '../src/evaluation.js';
import { GameRuntime } from '../src/runtime.js';
import { startFixture } from './fixture.js';

const condition = { all: [{ variable: 'engine_id', equals: 'queen' },
  { variable: 'room_id', equals: 71 }, { variable: 'room_ready', equals: true }] };
const variableCondition = { all: [{ variable: 'engine_id', equals: 'queen' },
  { variable: 'game_state.93', from: 0, equals: 1 }] };
const state = (room_id, native_time_ms, room_ready = true) => ({ schema_version: 1,
  engine_id: 'queen', room_id, room_name: 'Synthetic room', room_ready, native_time_ms });

test('strict typed conditions, ordered transient arrivals, and latched success', () => {
  for (const bad of [{ all: [] }, { all: [{ variable: 'unknown', equals: 1 }] },
    { all: [{ variable: 'room_id', equals: '71' }] }, { ...condition, code: 'true' }]) {
    assert.throws(() => parseCondition(bad));
  }
  const evaluation = new RunEvaluation({ condition });
  evaluation.accept({ current: state(70, 1), events: [] });
  assert.equal(evaluation.result, undefined);
  evaluation.accept({ current: state(71, 2, false), events: [] });
  assert.equal(evaluation.result, undefined, 'A pending arrival is not success');
  evaluation.accept({ current: state(72, 4), events: [state(71, 3)] });
  assert.equal(evaluation.result.success, true, 'A short visit between polls is retained');
  assert.equal(evaluation.result.variables.room_id, 71);
  assert.equal(evaluation.signal.signal.aborted, true);
  evaluation.fail(new Error('later'));
  assert.equal(evaluation.result.success, true);
  const invalid = new RunEvaluation({ condition });
  assert.throws(() => invalid.accept({ current: state(72, 4), events: [state(71, 3), {}] }));
  assert.equal(invalid.result, undefined, 'Malformed telemetry cannot partially satisfy the goal');
});

const baseline = (value, native_time_ms) => ({ event: 'game_variables_baseline', native_time_ms,
  banks: { game_state: [...Array(93).fill(0), value] } });
const change = (previous, value, native_time_ms) => ({ event: 'game_variables_change', native_time_ms,
  bank: 'game_state', index: 93, previous, value });

const queenSlug = 'flight-of-the-amazon-queen';
function progressBaseline(native_time_ms, achieved = []) {
  const bank = Array(211).fill(0);
  for (const [index, value] of achieved) bank[index] = value;
  return { event: 'game_variables_baseline', native_time_ms, banks: { game_state: bank } };
}

test('Queen progress follows the active save and reaches 100% on the credits cutaway', async () => {
  const evaluation = new RunEvaluation({ condition: { all: [{ variable: 'room_id', equals: 110 }] } });
  await evaluation.beginSession(queenSlug);
  const six = [[3, 1], [101, 1], [123, 2], [41, 2], [67, 2], [105, 1]];
  evaluation.accept({ current: state(67, 2), events: [], gameVariableEvents: [progressBaseline(1, six)] });
  assert.equal(evaluation.progress.completed, 23);
  assert.equal(evaluation.progress.percent, 96);
  evaluation.accept({ current: state(70, 4), events: [], gameVariableEvents: [progressBaseline(3)] });
  assert.deepEqual(evaluation.progress.reached, []);
  const crossing = { event: 'game_variables_change', native_time_ms: 5,
    bank: 'game_state', index: 101, previous: 0, value: 1 };
  evaluation.accept({ current: state(21, 7), events: [],
    gameVariableEvents: [crossing, { ...crossing, native_time_ms: 6, previous: 1, value: 0 }] });
  assert.equal(evaluation.progress.reached.at(-1), 'jungle-crossing');
  evaluation.accept({ current: state(67, 9), events: [], gameVariableEvents: [progressBaseline(8, six)] });
  evaluation.accept({ current: state(111, 11), events: [state(110, 10, false)], gameVariableEvents: [] });
  assert.equal(evaluation.result.progress.completed, 24);
  assert.equal(evaluation.result.progress.percent, 100);
  assert.equal(evaluation.result.variables.room_ready, false);
});

test('progress stays unavailable without a bank and a stop ignores later room records', async () => {
  const evaluation = new RunEvaluation({ condition });
  await evaluation.beginSession(queenSlug);
  evaluation.accept({ current: state(70, 1), events: [] });
  assert.equal(evaluation.progress.reason, 'missing_game_variables');
  evaluation.accept({ current: state(110, 5, false), events: [state(71, 4)],
    gameVariableEvents: [progressBaseline(2, [[3, 1]])] });
  assert.equal(evaluation.result.progress.reached.at(-1), 'hotel-escape');
  assert.equal(evaluation.result.variables.room_id, 71);

  const newSession = new RunEvaluation();
  await newSession.beginSession(queenSlug);
  newSession.accept({ current: state(70, 10), events: [], gameVariableEvents: [progressBaseline(9, [[3, 1]])] });
  await newSession.beginSession('unmapped-game');
  newSession.accept({ current: state(1, 1), events: [] });
  assert.equal(newSession.progress, undefined);
});

test('Last Crusade scores the credits by outcome and rebuilds progress on restore', async () => {
  const evaluation = new RunEvaluation();
  await evaluation.beginSession('indiana-jones-and-the-last-crusade');
  const identity = { engine_id: 'scumm', game_id: 'indy3', index_md5: '9c0fee288ad564a7d25ec3e841810d79' };
  const globals = Array(100).fill(0);
  const objects = Array(157).fill(0);
  globals[94] = 0x0081;
  globals[95] = 0x0008;
  function accept(room, time, changes, game = identity) {
    evaluation.accept({ identity: game, current: { ...state(room, time, false), engine_id: 'scumm' },
      events: [], gameVariableEngine: 'scumm', gameVariableEvents: changes });
  }
  const baseline = time => ({ event: 'game_variables_baseline', native_time_ms: time,
    banks: { scumm_vars: globals, object_state: objects } });
  const outcome = (time, previous, value) => ({ event: 'game_variables_change', native_time_ms: time,
    bank: 'scumm_vars', index: 99, previous, value });
  accept(82, 1, [baseline(1)], { ...identity, index_md5: '0'.repeat(32) });
  assert.equal(evaluation.progress.reason, 'edition_mismatch');
  accept(82, 2, [baseline(2)]);
  assert.equal(evaluation.progress.completed, 19);
  accept(94, 3, [outcome(3, 0, 2)]);
  assert.equal(evaluation.progress.completed, 19);
  accept(82, 4, [outcome(4, 2, 4)]);
  assert.equal(evaluation.progress.completed, 19, 'An outcome update must not match the previous credits room');
  accept(94, 5, []);
  assert.equal(evaluation.progress.completed, 20);
  assert.equal(evaluation.progressTracker.nextMilestone, null);
  globals.fill(0);
  objects[156] = 1;
  accept(22, 6, [baseline(6)]);
  assert.equal(evaluation.progress.completed, 7);
  assert.equal(evaluation.progressTracker.nextMilestone, 'Got inside Castle Brunwald');
  objects[156] = 0;
  accept(22, 7, [baseline(7)]);
  assert.equal(evaluation.progress.completed, 0);
  accept(22, 10, [
    { event: 'game_variables_change', native_time_ms: 8, bank: 'object_state', index: 156, previous: 0, value: 1 },
    { event: 'game_variables_change', native_time_ms: 9, bank: 'object_state', index: 156, previous: 1, value: 0 },
  ]);
  assert.equal(evaluation.progress.completed, 7, 'A bank-only branch can match between room samples');
  globals[93] = 0x4200;
  globals[95] = 0x0001;
  accept(84, 11, [baseline(11)]);
  assert.equal(evaluation.progress.reached.at(-1), 'passed-first-trial');
  globals[95] |= 0x0002;
  accept(85, 12, [baseline(12)]);
  assert.equal(evaluation.progress.reached.at(-1), 'passed-second-trial');
});

test('Fate of Atlantis tracks shared milestones and requires the surviving ending', async () => {
  const evaluation = new RunEvaluation();
  await evaluation.beginSession('indiana-jones-and-the-fate-of-atlantis');
  const identity = { engine_id: 'scumm', game_id: 'atlantis', index_md5: '182344899c2e2998fca0bebcd82aa81a' };
  const bits = Array(81).fill(0);
  const objects = Array(937).fill(0);
  const globals = Array(239).fill(0);
  const baseline = native_time_ms => ({ event: 'game_variables_baseline', native_time_ms,
    banks: { scumm_bit_bytes: bits, object_state: objects, scumm_vars: globals } });
  function accept(room, time, gameVariableEvents = [], game = identity) {
    evaluation.accept({ identity: game, current: { ...state(room, time, false), engine_id: 'scumm' },
      events: [], gameVariableEngine: 'scumm', gameVariableEvents });
  }

  accept(48, 1, [baseline(1)], { ...identity, index_md5: '0'.repeat(32) });
  assert.equal(evaluation.progress.reason, 'edition_mismatch');
  accept(68, 2, [baseline(2)]);
  assert.equal(evaluation.progress.completed, 0);
  bits[9] = 0x81;
  accept(10, 3, [baseline(3)]);
  assert.deepEqual(evaluation.progress.reached, ['new-york-theater']);
  bits[26] = 0x14;
  accept(1, 4, [baseline(4)]);
  assert.equal(evaluation.progress.latest_milestone, "Read Plato's Lost Dialogue");
  globals[238] = 2;
  accept(1, 4.5, [baseline(4.5)]);
  assert.equal(evaluation.progress.reached.at(-1), 'chose-story-path');
  accept(51, 5);
  assert.equal(evaluation.progress.latest_milestone, 'Entered the Crete Labyrinth');
  accept(48, 6);
  assert.equal(evaluation.progress.reached.at(-1), 'atlantis');
  objects[582] = 1;
  accept(98, 7, [baseline(7)]);
  assert.equal(evaluation.progress.reached.at(-1), 'opened-airlock', 'The airlock exit persists across saves');
  accept(46, 7.5);
  assert.equal(evaluation.progress.reached.at(-1), 'canal');
  accept(94, 7.75);
  assert.equal(evaluation.progress.reached.at(-1), 'middle-ring');
  accept(55, 8);
  assert.equal(evaluation.progress.reached.at(-1), 'inner-ring');
  accept(23, 9);
  assert.equal(evaluation.progress.reached.at(-1), 'colossus');
  objects[264] = 3;
  accept(23, 9.5, [baseline(9.5)]);
  assert.equal(evaluation.progress.reached.at(-1), 'loaded-god-machine-disks');
  accept(24, 10);
  assert.equal(evaluation.progress.completed, 21, 'Death retains prior progress');
  accept(96, 11);
  assert.equal(evaluation.progress.completed, 21, 'The ending room alone cannot grant completion');
  accept(25, 12, [{ event: 'game_variables_change', native_time_ms: 12,
    bank: 'scumm_bit_bytes', index: 4, previous: 0, value: 0x10 }]);
  assert.equal(evaluation.progress.completed, 21, 'Starting the escape cannot grant completion');
  accept(96, 13);
  assert.equal(evaluation.progress.percent, 100);
  assert.equal(evaluation.progressTracker.nextMilestone, null);

  bits.fill(0);
  objects.fill(0);
  accept(68, 14, [baseline(14)]);
  assert.equal(evaluation.progress.completed, 0);
  accept(66, 15);
  assert.equal(evaluation.progress.reached.at(-1), 'dived-toward-atlantis');
  accept(82, 16, [baseline(16)]);
  assert.equal(evaluation.progress.reached.at(-1), 'dived-toward-atlantis');
});

test('DreamWeb scores keyed snapshots and rebuilds progress after a restore', async () => {
  const evaluation = new RunEvaluation();
  await evaluation.beginSession('dreamweb');
  const identity = { engine_id: 'dreamweb', game_id: 'dreamweb' };
  const start = { beenMugged: 0, talkedToSparky: 0, card1Money: 0, talkedToAttendant: 0,
    talkedToBoss: 0, gunPassFlag: 0, talkedToRecep: 0, cardPassFlag: 0,
    rockstarDead: 0, generalDead: 0, sartainDead: 0, aideDead: 0,
    canMoveAltar: 0, manDead: 0, madmanFlag: 0 };
  let sampleTime = 0;
  const accept = (room_id, vars) => evaluation.accept({ current: null, events: [],
    progressTelemetry: { identity, snapshot: { schema_version: 1, room_id,
      sample_time_ms: ++sampleTime, banks: { game_vars: vars } } } });

  accept(0, { ...start, manDead: null });
  assert.equal(evaluation.progress.reason, 'missing_game_variables');
  accept(0, start);
  assert.equal(evaluation.progress.completed, 0);
  const funded = { ...start, beenMugged: 1, talkedToSparky: 1, card1Money: 12432 };
  accept(8, funded);
  assert.equal(evaluation.progress.completed, 3);
  const paid = { ...funded, talkedToAttendant: 1, talkedToBoss: 1, gunPassFlag: 1, card1Money: 10432 };
  accept(23, paid);
  assert.equal(evaluation.progress.completed, 5, 'Payment precedes the gun release');
  accept(0, { ...paid, gunPassFlag: 2, talkedToRecep: 1, cardPassFlag: 1 });
  assert.equal(evaluation.progress.completed, 7, 'Payment precedes the hotel keycard');
  accept(0, { ...paid, gunPassFlag: 2, talkedToRecep: 1, cardPassFlag: 2, card1Money: 2132 });
  assert.equal(evaluation.progress.completed, 8);
  accept(24, { ...start, rockstarDead: 1, generalDead: 2, sartainDead: 2 });
  assert.equal(evaluation.progress.completed, 11, 'Aftermath states still count as completed targets');
  const altar = { ...start, rockstarDead: 1, generalDead: 2, sartainDead: 2, aideDead: 1, canMoveAltar: 1 };
  accept(9, altar);
  assert.equal(evaluation.progress.completed, 13);
  accept(14, { ...altar, manDead: 2 });
  assert.equal(evaluation.progress.completed, 14);
  accept(14, { ...altar, madmanFlag: 1 });
  assert.equal(evaluation.progress.percent, 100);
  accept(9, altar);
  assert.equal(evaluation.progress.completed, 13, 'Loading before the subway drops credit for later milestones');
  accept(0, start);
  assert.equal(evaluation.progress.completed, 0);
});

test('Elvira II scores the winning branch separately from a late ritual failure', async () => {
  const evaluation = new RunEvaluation();
  await evaluation.beginSession('elvira-ii-the-jaws-of-cerberus');
  const identity = { engine_id: 'agos', game_id: 'elvira2', index_md5: 'a144d2467e1addee1f705647dacd33df' };
  let sampleTime = 0;
  const accept = (studios, stage, outcome, detected = identity) => {
    const vars = Array(255).fill(0);
    vars[180] = studios;
    vars[186] = stage;
    const banks = outcome === undefined ? { vars } : { vars, elvira2_outcome: { ritual: outcome } };
    evaluation.accept({ current: null, events: [],
      progressTelemetry: { identity: detected, snapshot: { schema_version: 1, room_id: null,
        sample_time_ms: ++sampleTime, banks } } });
    return evaluation.progress;
  };

  assert.equal(accept(0, 0, 0).completed, 0);
  assert.equal(accept(1, 0, 0).completed, 1);
  assert.equal(accept(2, 0, 0).completed, 2);
  assert.equal(accept(3, 0, 0).reached.at(-1), 'rescued-elvira');
  assert.equal(accept(3, 1, 0).reached.at(-1), 'summoned-cerberus');
  assert.equal(accept(3, 2, 0).reached.at(-1), 'bound-cerberus');
  assert.equal(accept(3, 3, 0).completed, 6, 'Lance use precedes the winning script');
  assert.equal(accept(3, 3, 2).completed, 6, 'Late failure shares the same ritual stage');
  assert.equal(accept(3, 20, 2).completed, 3, 'Early failure clears the ritual stage');
  assert.equal(accept(3, 3, 1).reached.at(-1), 'winning-ending');
  assert.equal(accept(0, 0, 1).percent, 100, 'The scripted Start load resets vars during the winning sequence');
  assert.equal(accept(2, 0, 0).completed, 2, 'A restore uses the current save timeline');
  assert.equal(accept(3, 3).reason, 'missing_game_variables');
  assert.equal(accept(3, 3, 1, { ...identity, index_md5: undefined }).reason, 'edition_mismatch');
  assert.equal(accept(3, 3, 1, { ...identity, index_md5: '0'.repeat(32) }).reason, 'edition_mismatch');
});

test('Sierra maps match their edition and distinguish the winning branches', async t => {
  const editions = [
    ['space-quest-the-sarien-encounter', 'agi', 'sq1', '0b216d931e95750f1f4837d6a4b821e5', 64, {}, 43],
    ['space-quest-the-sarien-encounter-sq1sci', 'sci', 'sq1sci', '1dd1b7c042f0c7345730a94633d283fe', 2, { globals: { 12: 68 } }],
    ['space-quest-ii-chapter-ii-vohaul-s-revenge', 'agi', 'sq2', '28add5125484302d213911df60d2aded', 95, { flag_bytes: { 8: 16 } }],
    ['space-quest-iii-the-pirates-of-pestulon', 'sci', 'sq3', 'cc1a975fa0f80941b9485866ee6ff634', 117, { globals: { 12: 116 } }],
    ['space-quest-iv-roger-wilco-and-the-time-rippers', 'sci', 'sq4', 'bd29d3fb8ea4a8ee638edaf40aa5f5ed', 540, { globals: { 12: 556 } }],
    ['space-quest-v-the-next-mutation', 'sci', 'sq5', 'ea937aae231c4acdaeb249dea8f71060', 1041, { globals: { 12: 1060 } }],
    ['space-quest-6-roger-wilco-in-the-spinal-frontier', 'sci', 'sq6', 'ed5ad1dfcfcae482072634c9a69c6a53', 960, { globals: { 12: 540 } }],
    ['king-s-quest-quest-for-the-crown', 'sci', 'kq1sci', 'fcf6a00f2ff4715bf8ea65388458179d', 85, { globals: { 12: 53 } }],
    ['king-s-quest-ii-romancing-the-throne', 'agi', 'kq2', 'b866f0fab2fad91433a637a828cfa410', 107, { flag_bytes: { 4: 4 } }],
    ['king-s-quest-iii-to-heir-is-human', 'agi', 'kq3', '8ab343306df0e2d98f136be4e8cfd0ef', 74, { vars: { 220: 26 } }],
    ['king-s-quest-iv-the-perils-of-rosella', 'sci', 'kq4sci', '53cceea993dc02611cc3cfb3f1c548bb', 694, { globals: { 160: 0 } }],
    ['king-s-quest-v-absence-makes-the-heart-go-yonder', 'sci', 'kq5', 'a8a854af430e25c70e2b986f02239710', 123, { globals: { 12: 120 } }],
    ['king-s-quest-vi-heir-today-gone-tomorrow', 'sci', 'kq6', '8ac79732f6f759158cda43e0b8b1988e', 740, { globals: { 12: 180 } }],
    ['king-s-quest-vii-the-princeless-bride', 'sci', 'kq7', 'a000858d91155b948bf3b23fa71ff79a', 2210, { globals: { 12: 2208 } }],
    ['police-quest-in-pursuit-of-the-death-angel', 'agi', 'pq1', 'cfa93e5f2aa7378bddd10ad6746a2ffb', 104, {}, 103],
    ['police-quest-in-pursuit-of-the-death-angel-pq1sci', 'sci', 'pq1sci', '1bddd987b230086a13da172f85908d2c', 60, { globals: { 12: 58 } }],
    ['police-quest-2-the-vengeance', 'sci', 'pq2', '499de78ae72b7ce219f944c5e7ef0c5b', 91, { globals: { 12: 133 } }],
    ['police-quest-3-the-kindred', 'sci', 'pq3', '5bd82ed17c41e596df61782f850c6ade', 33, { globals: { 12: 57 } }],
    ['police-quest-iv-open-season', 'sci', 'pq4', '069123b257f9e7655f13eb768397c345', 778, { globals: { 12: 755 } }],
    ['police-quest-iv-open-season', 'sci', 'pq4', '069123b257f9e7655f13eb768397c345', 779, { globals: { 12: 778 } }],
  ];
  for (const [slug, engine_id, game_id, index_md5, ending, changes, otherRoom = ending] of editions) {
    await t.test(`${game_id} ending room ${ending}`, async () => {
      const evaluation = new RunEvaluation();
      await evaluation.beginSession(slug);
      const banks = engine_id === 'agi'
        ? { vars: Array(256).fill(0), flag_bytes: Array(32).fill(0) }
        : { globals: Array(256).fill(1) };
      let sample_time_ms = 0;
      function accept(room_id, md5 = index_md5) {
        evaluation.accept({ current: null, events: [], progressTelemetry: {
          identity: { engine_id, game_id, index_md5: md5 },
          snapshot: { schema_version: 1, room_id, sample_time_ms: ++sample_time_ms, banks },
        } });
        return evaluation.progress;
      }
      assert.notEqual(accept(otherRoom).percent, 100);
      for (const [bank, values] of Object.entries(changes)) Object.assign(banks[bank], values);
      assert.equal(accept(ending, '0'.repeat(32)).reason, 'edition_mismatch');
      const victory = accept(ending);
      assert.equal(victory.percent, 100);
      assert.equal(victory.reached.at(-1), 'winning-ending');
    });
  }
});

test('Beneath a Steel Sky scores saved script variables and the winning credits room', async () => {
  const evaluation = new RunEvaluation();
  await evaluation.beginSession('beneath-a-steel-sky');
  const identity = { engine_id: 'sky', game_id: 'sky' };
  const vars = Array(838).fill(0);
  let sampleTime = 0;
  const accept = (room_id, completed, changes = {}, message) => {
    Object.assign(vars, changes);
    evaluation.accept({ current: null, events: [],
      progressTelemetry: { identity, snapshot: { schema_version: 1, room_id,
        sample_time_ms: ++sampleTime, banks: { script_vars: [...vars] } } } });
    assert.equal(evaluation.progress.completed, completed, message);
  };

  accept(0, 0, { 640: 1 });
  assert.equal(evaluation.progress.total, 28);
  accept(2, 1, { 125: 42 });
  accept(4, 2, { 420: 42 });
  accept(12, 3, { 440: 42 });
  accept(7, 4, { 268: 42 }, 'Cutting the cable leaves it to be picked up');
  accept(29, 5, { 268: 69 });
  accept(14, 6, { 451: 42 });
  accept(14, 6, { 258: 1 }, 'A promised jammer is still pending');
  accept(14, 7, { 258: 42 });
  accept(10, 8, { 335: 42 });
  accept(26, 8, { 256: 1 }, 'Arranging surgery leaves the port uninstalled');
  accept(26, 9, { 256: 42 });
  accept(92, 9, { 515: 24828, 519: 24829, 523: 24643 }, 'All three documents must be decrypted');
  accept(92, 10, { 523: 24830 });
  accept(10, 11, { 480: 1 });
  accept(31, 12, { 348: 1 });
  accept(26, 13, { 787: 42 });
  accept(40, 14, { 822: 1 });
  accept(37, 15, { 810: 1, 813: 1 }, 'Loosening the grille leaves the passage closed');
  accept(65, 16, { 813: 2 });
  accept(67, 17, { 681: 1 });
  accept(72, 18, { 698: 1 });
  accept(73, 19, { 703: 1 });
  accept(73, 20, { 702: 1 });
  accept(94, 21, { 640: 0 });
  accept(95, 22, { 641: 1 });
  accept(74, 23, { 642: 1 });
  accept(76, 24, { 713: 1 });
  accept(76, 25, { 714: 1 });
  accept(80, 26, { 719: 2, 707: 1 }, 'Infected tissue still needs to be applied to LINC');
  accept(81, 27, { 707: 2 });
  accept(82, 27);
  accept(102, 27, {}, 'The death screen does not complete the game');
  accept(85, 28);
  assert.equal(evaluation.progress.percent, 100);
  vars.fill(0);
  accept(4, 2, { 125: 42, 420: 42, 640: 1 }, 'Restoring an earlier save rebuilds the score');
});

test('game variable transitions ignore restored values and retain the matching snapshot', () => {
  const evaluation = new RunEvaluation({ condition: variableCondition });
  const records = [];
  evaluation.on('telemetry', event => records.push(event));
  const accept = (...gameVariableEvents) => evaluation.accept({ current: state(70, 10), events: [], gameVariableEvents });
  accept(baseline(0, 1), baseline(1, 2));
  assert.equal(evaluation.result, undefined);
  accept(change(1, 0, 3), change(0, 1, 4), change(1, 0, 5));
  assert.equal(evaluation.result.game_variables.game_state[93], 1);
  assert.deepEqual(evaluation.result.game_variable_match, change(0, 1, 4));
  assert.deepEqual(records.filter(event => event.event === 'game_variables_baseline')
    .map(event => event.banks.game_state[93]), [0, 1]);
  assert.deepEqual(records.filter(event => event.event === 'game_variables_change')
    .map(event => event.value), [0, 1, 0]);

  const equality = new RunEvaluation({ condition: { all: [{ variable: 'game_state.93', equals: 1 }] } });
  equality.accept({ current: state(70, 10), events: [], gameVariableEvents: [baseline(1, 2)] });
  assert.equal(equality.result.game_variable_match.event, 'game_variables_baseline');
});

test('game variable evaluation rejects unavailable slots and inconsistent batches before matching', () => {
  const payload = { current: state(70, 10), events: [] };
  const evaluation = new RunEvaluation({ condition: variableCondition });
  assert.throws(() => evaluation.accept(payload), /does not provide game variable telemetry/);
  assert.throws(() => evaluation.accept({ ...payload, gameVariableEvents: [baseline(true, 1)] }),
    /Unavailable or incompatible game variable/);
  assert.throws(() => evaluation.accept({ ...payload, gameVariableEvents: [baseline(0, 1), change(0, 1, 2), change(0, 2, 3)] }),
    /Inconsistent game variable change/);
  assert.equal(evaluation.result, undefined);
  assert.throws(() => parseCondition({ all: [{ variable: 'game_state.4096', equals: 1 }] }), /Invalid game variable index/);
  assert.throws(() => parseCondition({ all: [{ variable: 'game_state.93', from: false, equals: 1 }] }),
    /Transition values must have the same type/);
  const roomBank = new RunEvaluation({ condition: { all: [{ variable: 'room_vars.0', equals: 1 }] } });
  assert.equal(roomBank.requiresRoomTelemetry, false);
});

test('private evaluator interrupts an active wait and prevents remaining inputs', { timeout: 30000 }, async t => {
  const fixture = await startFixture({ evaluation: true });
  const dataDir = await mkdtemp(path.join(tmpdir(), 'agentscumm-evaluation-'));
  const evaluation = new RunEvaluation({ condition, intervalMs: 50 });
  const runtime = new GameRuntime({ baseUrl: fixture.url, dataDir, viewer: false, evaluation });
  t.after(async () => { await runtime.close(); await fixture.close(); await rm(dataDir, { recursive: true, force: true }); });
  const launch = await runtime.start();
  assert.equal((await runtime.waitReady(10000)).phase, 'ready');
  assert.equal(evaluation.result, undefined);
  assert.doesNotMatch(JSON.stringify(runtime.status()), /room_id|room_ready|Synthetic room|Laundry/);
  const request = { session_id: launch.sessionId, request_id: 'goal', actions: [
    { type: 'click', x: 50, y: 50 }, { type: 'wait', milliseconds: 10000 }, { type: 'click', x: 90, y: 90 }] };
  const started = performance.now();
  const result = await runtime.act(request);
  assert.ok(performance.now() - started < 5000, 'Do not wait out the submitted ten-second delay');
  assert.equal(result.action.state, 'stopped');
  assert.equal(evaluation.result.success, true);
  assert.equal(await runtime.shell.evaluate(() => events.filter(e => e.type === 'mousedown').length), 1);
  assert.equal((await runtime.act(request)).replayed, true);
  await assert.rejects(runtime.act({ ...request, request_id: 'after-goal' }), /Stop condition reached/);
  assert.doesNotMatch(JSON.stringify(result.action), /room_id|room_ready/);
});

test('missing engine telemetry fails explicitly without a visual fallback', { timeout: 20000 }, async t => {
  const fixture = await startFixture();
  const dataDir = await mkdtemp(path.join(tmpdir(), 'agentscumm-unsupported-'));
  const evaluation = new RunEvaluation({ condition, intervalMs: 50 });
  const runtime = new GameRuntime({ baseUrl: fixture.url, dataDir, viewer: false, evaluation });
  t.after(async () => { await runtime.close(); await fixture.close(); await rm(dataDir, { recursive: true, force: true }); });
  const terminal = once(evaluation, 'terminal');
  await runtime.start(); await runtime.waitReady(10000);
  const [result] = await terminal;
  assert.equal(result.success, false);
  assert.match(result.error, /does not provide evaluation variables/);
});

test('a slow telemetry read does not stop progress tracking during normal play', async t => {
  const runtime = new GameRuntime({ viewer: false });
  const evaluation = new RunEvaluation();
  await evaluation.beginSession(queenSlug);
  runtime.phase = 'ready';
  runtime.sessionEvaluation = evaluation;
  runtime.log = async () => {};
  runtime.shell = { evaluate: async () => ({ current: state(70, 2), events: [],
    gameVariableEvents: [progressBaseline(1)] }) };
  await runtime.pollEvaluation();
  assert.equal(runtime.spectatorProgress.percent, 0);

  const read = Promise.withResolvers();
  runtime.shell.evaluate = () => read.promise;
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const pending = runtime.pollEvaluation();
  t.mock.timers.tick(2000);
  await new Promise(resolve => setImmediate(resolve));
  read.resolve({ current: state(70, 4), events: [], gameVariableEvents: [
    { event: 'game_variables_change', native_time_ms: 3, bank: 'game_state', index: 91, previous: 0, value: 1 },
  ] });
  await pending;
  assert.equal(evaluation.result, undefined);
  assert.equal(runtime.spectatorProgress.percent, 4);
});

test('normal play logs progress and updates the spectator footer', { timeout: 20000 }, async t => {
  const fixture = await startFixture({ evaluation: 'progress' });
  const dataDir = await mkdtemp(path.join(tmpdir(), 'agentscumm-progress-'));
  const runtime = new GameRuntime({ baseUrl: fixture.url, dataDir, viewer: false });
  t.after(async () => { await runtime.close(); await fixture.close(); await rm(dataDir, { recursive: true, force: true }); });
  const { sessionId } = await runtime.start();
  await runtime.waitReady(10000);
  await runtime.viewer.open({ headless: true });
  const viewer = runtime.viewer.page;
  const expectFooter = text => viewer.waitForFunction(expected => {
    const progress = document.getElementById('story-progress');
    return !progress.hidden && progress.textContent === expected;
  }, text);
  await expectFooter('0% · Next: Secured the sheet rope to the radiator');
  await runtime.act({ session_id: sessionId, request_id: 'first-milestone',
    actions: [{ type: 'click', x: 50, y: 50 }] });
  await expectFooter('13% · Next: Distracted the piranhas');
  const rows = (await readFile(path.join(dataDir, 'runs', `${sessionId}.jsonl`), 'utf8')).trim().split('\n').map(JSON.parse);
  const samples = rows.filter(row => row.type === 'progress');
  assert.ok(samples.every(row => row.sessionId === sessionId));
  assert.deepEqual(samples.at(-1).progress, { game: queenSlug, total: 24, state: 'tracked',
    completed: 3, percent: 13, latest_milestone: 'Reached the crash site',
    reached: ['secured-sheet-rope', 'lola-help', 'hotel-escape'] });
  assert.equal(runtime.status().phase, 'ready');
  assert.doesNotMatch(JSON.stringify(runtime.status()), /latest_milestone|nextMilestone|game_state|room_id/);

  const publish = (bank, room) => runtime.shell.evaluate(({ bank, room }) => {
    globalThis.__scummwebEvaluationGameVariableEvents.push({ ...bank, native_time_ms: performance.now() });
    globalThis.__scummwebEvaluationState = { ...room, native_time_ms: performance.now() };
  }, { bank, room });
  await publish(progressBaseline(0, [[3, 1], [101, 1], [123, 2], [41, 2], [67, 2], [105, 1]]), state(110, 0, false));
  await expectFooter('100%');
  await publish(progressBaseline(0), state(70, 0));
  await expectFooter('0% · Next: Secured the sheet rope to the radiator');
  await runtime.shell.evaluate(() => { globalThis.__scummwebEvaluationGameVariableOverflow = true; });
  await runtime.pollEvaluation();
  assert.equal(runtime.status().phase, 'ready');
  assert.equal(runtime.spectatorProgress, undefined);
  const finalLog = await readFile(path.join(dataDir, 'runs', `${sessionId}.jsonl`), 'utf8');
  assert.match(finalLog, /"type":"progress-error"[^\n]+"message":"[^"]*Game variable telemetry queue overflow/);
  await runtime.closeGame();
  await viewer.locator('#story-progress').waitFor({ state: 'hidden' });
});

test('launcher accepts a variable-only provider and keeps receipts and telemetry off stdout', { timeout: 30000 }, async t => {
  const fixture = await startFixture({ evaluation: 'game_variables_only' });
  const directory = await mkdtemp(path.join(tmpdir(), 'agentscumm-launcher-'));
  const resultFile = path.join(directory, 'result.json');
  const child = spawn(process.execPath, ['src/index.js', '--no-viewer', '--base-url', fixture.url,
    '--data-dir', path.join(directory, 'browser'), '--stop-condition', JSON.stringify(variableCondition),
    '--evaluation-log', path.join(directory, 'evaluation.jsonl'), '--result-file', resultFile, '--telemetry-ms', '50'],
  { cwd: new URL('..', import.meta.url), stdio: ['pipe', 'pipe', 'pipe'] });
  const exit = once(child, 'exit');
  t.after(async () => { if (child.exitCode === null) child.kill(); await exit; await fixture.close(); await rm(directory, { recursive: true, force: true }); });
  let nextId = 0;
  const pending = new Map(), messages = [];
  createInterface({ input: child.stdout }).on('line', line => {
    const message = JSON.parse(line); messages.push(message);
    if (pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id); }
  });
  child.stderr.resume();
  const call = (method, params) => new Promise(resolve => {
    const id = ++nextId; pending.set(id, resolve);
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
  });
  await call('initialize', { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'test', version: '1' } });
  child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');
  const launch = await call('tools/call', { name: 'session', arguments: { operation: 'start' } });
  const session_id = JSON.parse(launch.result.content[0].text).sessionId;
  const ready = await call('tools/call', { name: 'status', arguments: { wait_ms: 10000 } });
  assert.equal(JSON.parse(ready.result.content[0].text).phase, 'ready');
  // Do not depend on the last MCP response surviving transport shutdown; the receipt and exit code are the contract.
  void call('tools/call', { name: 'act', arguments: { session_id, request_id: 'finish', actions: [
    { type: 'click', x: 50, y: 50 }, { type: 'wait', milliseconds: 10000 }, { type: 'click', x: 90, y: 90 }] } });
  assert.deepEqual(await exit, [0, null]);
  const result = JSON.parse(await readFile(resultFile, 'utf8'));
  assert.equal(result.success, true);
  assert.deepEqual(result.variables, { engine_id: 'queen' });
  assert.equal(result.progress.reason, 'missing_game_variables');
  assert.equal(result.game_variables.game_state[93], 1);
  assert.equal(result.game_variable_match.previous, 0);
  assert.ok((await readFile(result.screenshot)).length > 1000);
  const telemetry = (await readFile(path.join(directory, 'evaluation.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
  assert.ok(telemetry.some(event => event.event === 'variables' && event.game_variables.game_state[93] === 0));
  assert.ok(telemetry.some(event => event.event === 'game_variables_baseline' && event.banks.game_state[93] === 0));
  assert.deepEqual(telemetry.filter(event => event.event === 'game_variables_change').map(event => event.value), [1, 0]);
  assert.equal(telemetry.at(-1).event, 'success');
  assert.ok(messages.every(message => message.jsonrpc === '2.0'));
  assert.doesNotMatch(JSON.stringify(messages), /room_id|room_ready|native_time_ms|game_state|game_variables|"progress"/);
});

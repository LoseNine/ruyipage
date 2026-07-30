"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const runtimePath =
  process.env.CEBWM_RUNTIME ||
  "C:\\firefox\\trace\\run_cebwm_runtime.js";
const dumpDir =
  process.env.CEBWM_RUNTIME_DUMP_EVAL_DIR ||
  "C:\\firefox\\trace\\runtime_eval_dump_test";

fs.rmSync(dumpDir, { recursive: true, force: true });
fs.mkdirSync(dumpDir, { recursive: true });

const output = execFileSync(process.execPath, [runtimePath], {
  encoding: "utf8",
  maxBuffer: 32 * 1024 * 1024,
  env: {
    ...process.env,
    CEBWM_RUNTIME_DUMP_EVAL_DIR: dumpDir,
  },
});
const jsonStart = output.indexOf("{");
assert.notStrictEqual(jsonStart, -1, `runtime did not emit JSON: ${output}`);
const summary = JSON.parse(output.slice(jsonStart));

assert(Array.isArray(summary.evalSourceDumps), "missing evalSourceDumps");
assert(summary.evalSourceDumps.length > 0, "expected at least one eval dump");

const firstDump = summary.evalSourceDumps[0];
assert.strictEqual(firstDump.index, 0);
assert(firstDump.length > 200000, `unexpected eval source length: ${firstDump.length}`);
assert(firstDump.sha256 && /^[0-9a-f]{64}$/.test(firstDump.sha256));

const dumpPath = path.join(dumpDir, "eval_0.js");
assert(fs.existsSync(dumpPath), `missing dump file: ${dumpPath}`);
const dumped = fs.readFileSync(dumpPath, "utf8");
assert.strictEqual(dumped.length, firstDump.length);
assert(dumped.includes("function _$jx"), "eval dump does not include VM dispatcher");

console.log("cebwm runtime eval dump verification passed");

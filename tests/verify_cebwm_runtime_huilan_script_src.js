"use strict";

const assert = require("assert");
const { execFileSync } = require("child_process");

const runtimePath =
  process.env.CEBWM_RUNTIME ||
  "C:\\firefox\\trace\\run_cebwm_runtime.js";

const output = execFileSync(process.execPath, [runtimePath], {
  encoding: "utf8",
  maxBuffer: 32 * 1024 * 1024,
});
const jsonStart = output.indexOf("{");
assert.notStrictEqual(jsonStart, -1, `runtime did not emit JSON: ${output}`);
const summary = JSON.parse(output.slice(jsonStart));

assert(
  !summary.huilanError ||
    !/Cannot read properties of undefined \(reading 'substr'\)/.test(
      summary.huilanError.message
    ),
  `Huilan script-src probe still sees undefined src: ${JSON.stringify(summary.huilanError)}`
);

console.log("cebwm runtime Huilan script src verification passed");

const assert = require("assert");
const { execFileSync } = require("child_process");

const runtimePath =
  process.env.CEBWM_RUNTIME ||
  "C:\\firefox\\trace\\run_cebwm_runtime.js";

const output = execFileSync(process.execPath, [runtimePath], {
  encoding: "utf8",
  maxBuffer: 16 * 1024 * 1024,
});
const jsonStart = output.indexOf("{");
assert.notStrictEqual(jsonStart, -1, `runtime did not emit JSON: ${output}`);
const summary = JSON.parse(output.slice(jsonStart));
const cookieSets = summary.finalEvents
  .filter((event) => event.type === "document.cookie.set")
  .map((event) => event.value);
const finalCookie = summary.cookie || "";

assert.strictEqual(
  cookieSets[0],
  "enable_pXlaX0mT0vLD=true; Secure",
  `unexpected first cookie set: ${cookieSets[0]}`
);
assert(
  !finalCookie.includes("enable_undefined"),
  `final cookie contains enable_undefined: ${finalCookie}`
);
assert(
  !finalCookie.includes("undefinedT"),
  `final cookie contains undefinedT: ${finalCookie}`
);
assert(
  finalCookie.includes("pXlaX0mT0vLDP="),
  `final cookie lacks pXlaX0mT0vLDP=: ${finalCookie}`
);

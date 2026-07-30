"use strict";

const assert = require("assert");
const { execFileSync } = require("child_process");

const runtimePath =
  process.env.CEBWM_RUNTIME ||
  "C:\\firefox\\trace\\run_cebwm_runtime.js";
const purecalcPath =
  process.env.CEBWM_PURECALC ||
  "C:\\firefox\\trace\\cebwm_purecalc.js";
const pure = require(purecalcPath);

const output = execFileSync(process.execPath, [runtimePath], {
  encoding: "utf8",
  maxBuffer: 128 * 1024 * 1024,
  env: {
    ...process.env,
    CEBWM_RUNTIME_PROBE_CRYPTO: "1",
    CEBWM_RUNTIME_PROBE_BYTECODE: "1",
    CEBWM_RUNTIME_BYTECODE_OFFSETS: "712",
    CEBWM_RUNTIME_BYTECODE_RADIUS: "40",
    CEBWM_RUNTIME_BYTECODE_TRACE_LIMIT: "500",
  },
});
const jsonStart = output.indexOf("{");
assert.notStrictEqual(jsonStart, -1, `runtime did not emit JSON: ${output}`);
const summary = JSON.parse(output.slice(jsonStart));

function findVmMetaWithStackArg(meta, predicate) {
  for (let current = meta; current; current = current.parentVmCallMeta) {
    const stackArgs = Array.isArray(current.stackArgs) ? current.stackArgs : [];
    if (stackArgs.some((stackArg) => predicate(stackArg, current))) return current;
  }
  return null;
}

function arraySummaryHex(summary) {
  assert(summary && Array.isArray(summary.items), "array summary lacks complete items");
  return summary.items.map((value) => value.toString(16).padStart(2, "0")).join("");
}

function findArrayAppendSegments(calls, objectId) {
  return calls
    .map((event, index) => {
      const beforeArgs = Array.isArray(event.argsBefore) ? event.argsBefore : [];
      const afterArgs = Array.isArray(event.argsAfter) ? event.argsAfter : [];
      const argIndex = beforeArgs.findIndex((value) => value && value.objectId === objectId);
      if (argIndex === -1) return null;
      const before = beforeArgs[argIndex];
      const after = afterArgs[argIndex];
      if (!after || before.length === after.length) return null;
      assert(after.length > before.length, `array append segment shrank at call ${index}`);
      assert(Array.isArray(after.items), `array append segment lacks items at call ${index}`);
      return {
        index,
        event,
        opcode: event.opcode,
        propertyKey: event.propertyKey,
        opcodePc: event.opcodePc,
        beforeLen: before.length,
        afterLen: after.length,
        bytes: after.items.slice(before.length, after.length),
      };
    })
    .filter(Boolean);
}

assert.strictEqual(summary.cryptoProbe && summary.cryptoProbe.enabled, true);
assert(Array.isArray(summary.cryptoProbe.events), "missing cryptoProbe.events");

assert(
  summary.cryptoProbe.instrumentedEvalSources.length > 0,
  "runtime did not instrument eval sources"
);

const cookiePair = String(summary.cookie || "")
  .split(/;\s*/)
  .find((item) => item.startsWith("pXlaX0mT0vLDP="));
assert(cookiePair, `missing pXlaX0mT0vLDP cookie: ${summary.cookie}`);
const cookieValue = cookiePair.split("=").slice(1).join("=");
const decodedCookie = pure.parseVersionedEncodedBytes(cookieValue);
const decodedCookieHexPrefix = decodedCookie.bytes
  .slice(0, 32)
  .map((value) => value.toString(16).padStart(2, "0"))
  .join("");

assert(
  Array.isArray(summary.cryptoProbe.cookieCipherBoundaries),
  "missing cryptoProbe.cookieCipherBoundaries"
);
const cookieBoundary = summary.cryptoProbe.cookieCipherBoundaries.find(
  (event) =>
    event.cookieName === "pXlaX0mT0vLDP" &&
    event.decoded &&
    event.decoded.length === decodedCookie.bytes.length &&
    event.decoded.hexPrefix === decodedCookieHexPrefix
);
assert(cookieBoundary, "missing decoded boundary for final pXlaX0mT0vLDP cookie");
assert.strictEqual(cookieBoundary.decoded.length, decodedCookie.bytes.length);
assert.strictEqual(cookieBoundary.decoded.hexPrefix, decodedCookieHexPrefix);
assert(cookieBoundary.evalLocation, "missing cookie boundary eval location");
assert(cookieBoundary.stack.some((line) => line.includes("eval#0")));

const cookieMatch = summary.cryptoProbe.cookieCipherMatches.find(
  (match) => match.cookieName === "pXlaX0mT0vLDP"
);
assert(cookieMatch, "missing crypto return match for pXlaX0mT0vLDP");
assert.strictEqual(cookieMatch.cookieDecodedLength, decodedCookie.bytes.length);
assert.strictEqual(cookieMatch.matchOffset, 0);

const matchedCall = summary.cryptoProbe.events.find(
  (event) =>
    event.type === "crypto.call" &&
    event.name === cookieMatch.matchedReturnName &&
    event.index === cookieMatch.returnEventIndex - 1
);
assert(matchedCall, "missing call event for matched crypto return");
assert(matchedCall.args[0].complete, "matched crypto arg0 was truncated");
assert(matchedCall.args[1].complete, "matched crypto arg1 was truncated");
assert(Array.isArray(matchedCall.stack), "matched crypto call lacks stack");
assert(matchedCall.stack.some((line) => line.includes("eval#0")));
assert(matchedCall.vmCallMeta, "matched crypto call lacks VM call metadata");
assert.strictEqual(matchedCall.vmCallMeta.argc, 2);
assert(Number.isInteger(matchedCall.vmCallMeta.pc), "VM call metadata lacks pc");
assert(Number.isInteger(matchedCall.vmCallMeta.opcode), "VM call metadata lacks opcode");
assert(
  Number.isInteger(matchedCall.vmCallMeta.opcodePc),
  "VM call metadata lacks opcodePc"
);
assert(
  Number.isInteger(matchedCall.vmCallMeta.opcodeByteAtPc),
  "VM call metadata lacks opcodeByteAtPc"
);
assert.strictEqual(
  matchedCall.vmCallMeta.opcodeByteAtPc,
  matchedCall.vmCallMeta.opcode
);
assert(
  Array.isArray(matchedCall.vmCallMeta.bytecodeWindow) &&
    matchedCall.vmCallMeta.bytecodeWindow.length > 0,
  "VM call metadata lacks bytecode window"
);
assert.strictEqual(
  matchedCall.vmCallMeta.bytecodeWindow[
    matchedCall.vmCallMeta.opcodePc - matchedCall.vmCallMeta.bytecodeWindowStart
  ],
  matchedCall.vmCallMeta.opcode
);
assert(
  Array.isArray(matchedCall.vmCallMeta.stackArgs),
  "VM call metadata lacks stackArgs"
);
assert.strictEqual(matchedCall.vmCallMeta.stackArgs.length, 2);
for (const [index, stackArg] of matchedCall.vmCallMeta.stackArgs.entries()) {
  assert.strictEqual(stackArg.slot, matchedCall.vmCallMeta.argBase + index);
  assert.strictEqual(stackArg.value.type, matchedCall.args[index].type);
  assert.strictEqual(stackArg.value.length, matchedCall.args[index].length);
  assert.strictEqual(stackArg.value.complete, matchedCall.args[index].complete);
  assert.strictEqual(stackArg.value.hexPrefix, matchedCall.args[index].hexPrefix);
}
assert(
  Array.isArray(matchedCall.vmCallMeta.argProducers),
  "VM call metadata lacks argProducers"
);
assert.strictEqual(matchedCall.vmCallMeta.argProducers.length, 2);
assert.deepStrictEqual(
  matchedCall.vmCallMeta.argProducers.map((producer) => ({
    slot: producer.slot,
    pc: producer.pc,
    opcode: producer.opcode,
    operand: producer.operand,
    source: producer.source,
  })),
  [
    { slot: matchedCall.vmCallMeta.argBase, pc: 184, opcode: 53, operand: 7, source: "vmLocal" },
    {
      slot: matchedCall.vmCallMeta.argBase + 1,
      pc: 186,
      opcode: 32,
      operand: 2,
      source: "vmArgument",
    },
  ]
);
for (const [index, producer] of matchedCall.vmCallMeta.argProducers.entries()) {
  assert.strictEqual(producer.value.type, matchedCall.vmCallMeta.stackArgs[index].value.type);
  assert.strictEqual(producer.value.length, matchedCall.vmCallMeta.stackArgs[index].value.length);
  assert.strictEqual(producer.value.complete, matchedCall.vmCallMeta.stackArgs[index].value.complete);
  assert.strictEqual(producer.value.hexPrefix, matchedCall.vmCallMeta.stackArgs[index].value.hexPrefix);
}
const localProducer = matchedCall.vmCallMeta.argProducers[0];
assert.strictEqual(localProducer.source, "vmLocal");
assert(
  Array.isArray(localProducer.localWrites),
  "vmLocal producer lacks localWrites"
);
assert(localProducer.localWrites.length > 0, "vmLocal producer has no local write history");
assert(
  Number.isInteger(localProducer.value.objectId),
  "vmLocal producer value lacks objectId"
);
const seedLocalWrite = localProducer.localWrites.find(
  (write) => write.value && write.value.objectId === localProducer.value.objectId
);
assert(seedLocalWrite, "vmLocal producer lacks seed write for the same array object");
assert.strictEqual(seedLocalWrite.slot, localProducer.operand);
assert.strictEqual(seedLocalWrite.value.type, localProducer.value.type);
assert.strictEqual(seedLocalWrite.value.length, 0);
assert.strictEqual(localProducer.value.length, 128);
assert(
  Array.isArray(localProducer.mutations),
  "vmLocal producer lacks mutation history"
);
const fillMutation = localProducer.mutations.find(
  (mutation) =>
    mutation.opcode === 44 &&
    mutation.propertyKey === 89 &&
    mutation.argIndex === 0 &&
    mutation.before.objectId === localProducer.value.objectId &&
    mutation.after.objectId === localProducer.value.objectId
);
assert(fillMutation, "vmLocal producer lacks opcode44 fill mutation");
assert(Array.isArray(fillMutation.bytecodeWindow), "fill mutation lacks bytecodeWindow");
assert(Array.isArray(fillMutation.argProducers), "fill mutation lacks argProducers");
assert.deepStrictEqual(
  fillMutation.argProducers.map((producer) => ({
    slot: producer.slot,
    pc: producer.pc,
    opcode: producer.opcode,
    operand: producer.operand,
    source: producer.source,
  })),
  [
    { slot: 0, pc: 166, opcode: 53, operand: 7, source: "vmLocal" },
    { slot: 1, pc: 168, opcode: 53, operand: 6, source: "vmLocal" },
  ]
);
assert(
  Array.isArray(fillMutation.argProducers[1].localWrites),
  "fill mutation number producer lacks localWrites"
);
assert.strictEqual(fillMutation.before.length, 0);
assert.strictEqual(fillMutation.after.length, 4);
assert(Array.isArray(fillMutation.argsBefore), "fill mutation lacks argsBefore");
assert.strictEqual(fillMutation.argsBefore[1].type, "number");
const fillNumber = fillMutation.argsBefore[1].value;
assert.deepStrictEqual(fillMutation.after.items, [
  (fillNumber >> 24) & 0xff,
  (fillNumber >> 16) & 0xff,
  (fillNumber >> 8) & 0xff,
  fillNumber & 0xff,
]);
const local6Producer = fillMutation.argProducers[1];
const local6SourceReturn = summary.cryptoProbe.events.find(
  (event) =>
    event.type === "vm.call.return" &&
    event.vmCallMeta &&
    event.vmCallMeta.opcode === 9 &&
    event.vmCallMeta.propertyKey === 88 &&
    event.returnValue &&
    event.returnValue.value === local6Producer.value.value
);
assert(local6SourceReturn, "vmLocal[6] lacks matching VM call return");
assert.strictEqual(local6SourceReturn.vmCallMeta.argc, 1);
assert.strictEqual(local6SourceReturn.vmCallMeta.stackArgs[0].value.length, 124);
const extendMutation = localProducer.mutations.find(
  (mutation) =>
    mutation.opcode === 44 &&
    mutation.propertyKey === 123 &&
    mutation.argIndex === 0 &&
    mutation.before.objectId === localProducer.value.objectId &&
    mutation.after.objectId === localProducer.value.objectId
);
assert(extendMutation, "vmLocal producer lacks opcode44 extend mutation");
assert(Array.isArray(extendMutation.bytecodeWindow), "extend mutation lacks bytecodeWindow");
assert(Array.isArray(extendMutation.argProducers), "extend mutation lacks argProducers");
assert.deepStrictEqual(
  extendMutation.argProducers.map((producer) => ({
    slot: producer.slot,
    pc: producer.pc,
    opcode: producer.opcode,
    operand: producer.operand,
    source: producer.source,
  })),
  [
    { slot: 0, pc: 173, opcode: 53, operand: 7, source: "vmLocal" },
    { slot: 1, pc: 175, opcode: 53, operand: 4, source: "vmLocal" },
  ]
);
assert(
  Array.isArray(extendMutation.argProducers[1].localWrites),
  "extend mutation bytes producer lacks localWrites"
);
assert(
  Array.isArray(extendMutation.argProducers[1].mutations),
  "extend mutation bytes producer lacks mutations"
);
assert.strictEqual(extendMutation.before.length, 4);
assert.strictEqual(extendMutation.after.length, localProducer.value.length);
assert.strictEqual(extendMutation.after.hexPrefix, localProducer.value.hexPrefix);
assert.strictEqual(pure.crc32(extendMutation.argsBefore[1].items), fillNumber >>> 0);
assert(Array.isArray(extendMutation.argsBefore), "extend mutation lacks argsBefore");
assert.strictEqual(extendMutation.argsBefore[1].type, "array");
assert.strictEqual(extendMutation.argProducers[1].value.length, extendMutation.argsBefore[1].length);
assert.strictEqual(
  extendMutation.argProducers[1].value.hexPrefix,
  extendMutation.argsBefore[1].hexPrefix
);
const local4Producer = extendMutation.argProducers[1];
const secondLocal4Mutation = local4Producer.mutations.find(
  (mutation) =>
    mutation.propertyKey === 127 &&
    mutation.before.objectId === local4Producer.value.objectId &&
    mutation.after.objectId === local4Producer.value.objectId &&
    mutation.before.length === 10 &&
    mutation.after.length === 59
);
assert(secondLocal4Mutation, "vmLocal[4] lacks second length-prefixed append mutation");
const local5Producer = secondLocal4Mutation.argProducers[1];
assert.strictEqual(local5Producer.source, "vmLocal");
assert.strictEqual(local5Producer.operand, 5);
assert.strictEqual(local5Producer.value.length, 48);
const local5SourceReturn = summary.cryptoProbe.events.find(
  (event) =>
    event.type === "vm.call.return" &&
    event.returnValue &&
    event.returnValue.objectId === local5Producer.value.objectId
);
assert(local5SourceReturn, "vmLocal[5] lacks matching VM call return");
assert(local5SourceReturn.vmCallMeta, "vmLocal[5] source return lacks VM metadata");
assert.strictEqual(local5SourceReturn.vmCallMeta.opcode, 9);
assert.strictEqual(local5SourceReturn.vmCallMeta.argc, 1);
assert.strictEqual(local5SourceReturn.vmCallMeta.propertyKey, 244);
assert.strictEqual(local5SourceReturn.vmCallMeta.stackArgs[0].value.value, 2);
assert.deepStrictEqual(pure.getCookieSegment48Property244Arg2(), local5SourceReturn.returnValue.items);
const property244Returns = summary.cryptoProbe.events.filter(
  (event) =>
    event.type === "vm.call.return" &&
    event.vmCallMeta &&
    event.vmCallMeta.propertyKey === 244
);
assert(property244Returns.length > 0, "missing propertyKey 244 return events");
for (const event of property244Returns) {
  assert.strictEqual(event.name, "_$hn");
  assert.strictEqual(event.vmCallMeta.opcode, 9);
  assert.strictEqual(event.vmCallMeta.argc, 1);
  assert.strictEqual(event.vmCallMeta.stackArgs[0].value.value, 2);
  assert.strictEqual(event.returnValue.length, 48);
  assert.deepStrictEqual(event.returnValue.items, property244Returns[0].returnValue.items);
}
const thirdLocal4Mutation = local4Producer.mutations.find(
  (mutation) =>
    mutation.propertyKey === 127 &&
    mutation.before.objectId === local4Producer.value.objectId &&
    mutation.after.objectId === local4Producer.value.objectId &&
    mutation.before.length === 59 &&
    mutation.after.length === 124
);
assert(thirdLocal4Mutation, "vmLocal[4] lacks third length-prefixed append mutation");
assert.strictEqual(thirdLocal4Mutation.argsBefore[1].type, "array");
assert.strictEqual(thirdLocal4Mutation.argsBefore[1].length, 64);
const thirdSegmentReturn = summary.cryptoProbe.events.find(
  (event) =>
    event.type === "crypto.return" &&
    event.returnValue &&
    event.returnValue.objectId === thirdLocal4Mutation.argsBefore[1].objectId &&
    event.vmCallMeta &&
    event.vmCallMeta.opcode === 64 &&
    event.vmCallMeta.propertyKey === 170 &&
    event.vmCallMeta.argc === 3 &&
    event.vmCallMeta.stackArgs &&
    event.vmCallMeta.stackArgs[0] &&
    event.vmCallMeta.stackArgs[0].value &&
    event.vmCallMeta.stackArgs[0].value.type === "array" &&
    (event.vmCallMeta.stackArgs[0].value.length === 56 ||
      event.vmCallMeta.stackArgs[0].value.length === 57) &&
    event.vmCallMeta.stackArgs[1] &&
    event.vmCallMeta.stackArgs[1].value &&
    event.vmCallMeta.stackArgs[1].value.type === "array" &&
    event.vmCallMeta.stackArgs[1].value.length === 21 &&
    event.vmCallMeta.stackArgs[2] &&
    event.vmCallMeta.stackArgs[2].value &&
    event.vmCallMeta.stackArgs[2].value.type === "number" &&
    event.vmCallMeta.stackArgs[2].value.value === 0
);
assert(thirdSegmentReturn, "third vmLocal[4] segment lacks matching crypto return");
assert(thirdSegmentReturn.vmCallMeta, "third segment crypto return lacks VM call metadata");
assert.strictEqual(thirdSegmentReturn.vmCallMeta.opcode, 64);
assert.strictEqual(thirdSegmentReturn.vmCallMeta.argc, 3);
assert.strictEqual(thirdSegmentReturn.vmCallMeta.propertyKey, 170);
assert.strictEqual(thirdSegmentReturn.returnValue.length, 64);
assert.strictEqual(thirdSegmentReturn.vmCallMeta.stackArgs[0].value.type, "array");
assert(
  thirdSegmentReturn.vmCallMeta.stackArgs[0].value.length === 56 ||
    thirdSegmentReturn.vmCallMeta.stackArgs[0].value.length === 57
);
assert.strictEqual(thirdSegmentReturn.vmCallMeta.stackArgs[1].value.type, "array");
assert.strictEqual(thirdSegmentReturn.vmCallMeta.stackArgs[1].value.length, 21);
assert.strictEqual(thirdSegmentReturn.vmCallMeta.stackArgs[2].value.type, "number");
assert.strictEqual(thirdSegmentReturn.vmCallMeta.stackArgs[2].value.value, 0);
assert.deepStrictEqual(
  pure.encryptIY(
    thirdSegmentReturn.vmCallMeta.stackArgs[0].value.items,
    thirdSegmentReturn.vmCallMeta.stackArgs[1].value.items,
    thirdSegmentReturn.vmCallMeta.stackArgs[2].value.value
  ),
  thirdSegmentReturn.returnValue.items
);
const thirdSegmentPlainWrite = thirdSegmentReturn.vmCallMeta.stackArgs[0].stackWrites.find(
  (write) =>
    write.source === "vmArgument" &&
    write.operand === 1 &&
    write.value &&
    write.value.type === "array" &&
    write.value.length === thirdSegmentReturn.vmCallMeta.stackArgs[0].value.length &&
    write.value.hexPrefix === thirdSegmentReturn.vmCallMeta.stackArgs[0].value.hexPrefix
);
assert(thirdSegmentPlainWrite, "third segment plaintext lacks vmArgument[1] stack provenance");
const property57Returns = summary.cryptoProbe.events.filter(
  (event) =>
    event.type === "vm.call.return" &&
    event.vmCallMeta &&
    event.vmCallMeta.propertyKey === 57
);
assert(property57Returns.length > 0, "missing propertyKey 57 VM return events");
assert(
  summary.cryptoProbe.vmCallReturnCounts.propertyKey57 >= property57Returns.length,
  "propertyKey 57 return count is inconsistent with recorded events"
);
assert(
  Array.isArray(summary.cryptoProbe.huffmanTables) &&
    summary.cryptoProbe.huffmanTables.length > 0,
  "missing propertyKey 57 Huffman table probe"
);
const runtimeHuffmanTable = summary.cryptoProbe.huffmanTables[0];
const pureHuffmanCodec = pure.buildProperty57HuffmanCodec();
assert.strictEqual(runtimeHuffmanTable.name, "propertyKey57");
assert(["legacy", "current"].includes(runtimeHuffmanTable.sourceName));
assert.strictEqual(runtimeHuffmanTable.fill, 184);
assert.strictEqual(pureHuffmanCodec.fill, runtimeHuffmanTable.fill);
assert.strictEqual(pureHuffmanCodec.table[0].code, 6);
assert.strictEqual(pureHuffmanCodec.table[0].bits, 3);
assert.strictEqual(pureHuffmanCodec.table[255].code, 47);
assert.strictEqual(pureHuffmanCodec.table[255].bits, 6);
assert.strictEqual(runtimeHuffmanTable.entries.length, 256);
for (let index = 0; index < 256; index++) {
  const actual = runtimeHuffmanTable.entries[index];
  const expected = pureHuffmanCodec.table[index];
  assert(actual, `missing runtime Huffman table entry ${index}`);
  assert(expected, `missing pure Huffman table entry ${index}`);
  assert.strictEqual(actual.symbol, index);
  assert.strictEqual(actual.code, expected.code, `Huffman code mismatch at ${index}`);
  assert.strictEqual(actual.bits, expected.bits, `Huffman bit length mismatch at ${index}`);
}
for (const event of property57Returns) {
  assert.strictEqual(event.vmCallMeta.stackArgs[0].value.type, "array");
  assert(event.vmCallMeta.stackArgs[0].value.complete);
  assert(event.returnValue.complete);
  assert.deepStrictEqual(
    pure.property57Encode(event.vmCallMeta.stackArgs[0].value.items),
    event.returnValue.items
  );
}
const thirdSegmentPlainSourceReturn = property57Returns.find(
  (event) =>
    event.returnValue &&
    event.returnValue.objectId === thirdSegmentReturn.vmCallMeta.stackArgs[0].value.objectId
);
assert(thirdSegmentPlainSourceReturn, "third segment plaintext lacks matching propertyKey 57 return");
assert.strictEqual(thirdSegmentPlainSourceReturn.compact, true);
assert.strictEqual(thirdSegmentPlainSourceReturn.vmCallMeta.opcode, 9);
assert.strictEqual(thirdSegmentPlainSourceReturn.vmCallMeta.argc, 1);
assert.strictEqual(
  thirdSegmentPlainSourceReturn.returnValue.length,
  thirdSegmentReturn.vmCallMeta.stackArgs[0].value.length
);
assert.strictEqual(thirdSegmentPlainSourceReturn.vmCallMeta.stackArgs[0].value.type, "array");
assert.strictEqual(thirdSegmentPlainSourceReturn.vmCallMeta.stackArgs[0].value.length, 74);
assert.strictEqual(thirdSegmentPlainSourceReturn.vmCallMeta.argProducers[0].source, "vmArgument");
assert.strictEqual(thirdSegmentPlainSourceReturn.vmCallMeta.argProducers[0].operand, 1);
const thirdSegmentEncodedPlain = pure.property57Encode(
  thirdSegmentPlainSourceReturn.vmCallMeta.stackArgs[0].value.items
);
const thirdSegmentRaw74 = thirdSegmentPlainSourceReturn.vmCallMeta.stackArgs[0].value.items;
assert(
  Array.isArray(summary.cryptoProbe.raw74ArgumentEntries),
  "missing targeted raw74 argument-entry events"
);
const raw74ArgumentEntry = summary.cryptoProbe.raw74ArgumentEntries.find(
  (event) =>
    event.type === "propertyKey57.raw74.argumentEntry" &&
    event.argumentOperand === 1 &&
    event.raw74 &&
    event.raw74.length === 74 &&
    event.raw74.hexPrefix === thirdSegmentPlainSourceReturn.vmCallMeta.stackArgs[0].value.hexPrefix
);
assert(raw74ArgumentEntry, "missing targeted raw74 vmArgument[1] function-entry lineage");
assert(raw74ArgumentEntry.argumentEntry, "raw74 lineage lacks compact argument entry");
assert.strictEqual(raw74ArgumentEntry.argumentEntry.args.length >= 2, true);
const raw74EntryArg = raw74ArgumentEntry.argumentEntry.args.values.find((item) => item.index === 1);
assert(raw74EntryArg, "raw74 lineage lacks argument[1] summary");
assert.deepStrictEqual(raw74EntryArg.value.items, thirdSegmentRaw74);
const raw74Parent31 = summary.cryptoProbe.events
  .map((event) => event.vmCallMeta)
  .find(
    (meta) =>
      meta &&
      meta.parentVmCallMeta &&
      meta.parentVmCallMeta.propertyKey === 31 &&
      meta.parentVmCallMeta.argc === 4 &&
      Array.isArray(meta.parentVmCallMeta.stackArgs) &&
      meta.parentVmCallMeta.stackArgs.length === 4 &&
      meta.parentVmCallMeta.stackArgs[1].value &&
      meta.parentVmCallMeta.stackArgs[1].value.hexPrefix ===
        thirdSegmentPlainSourceReturn.vmCallMeta.stackArgs[0].value.hexPrefix &&
      meta.parentVmCallMeta.stackArgs[2].value &&
      meta.parentVmCallMeta.stackArgs[2].value.length === 21
  );
assert(raw74Parent31, "raw74 lacks parent propertyKey31 argc=4 call metadata");
assert.strictEqual(raw74Parent31.parentVmCallMeta.stackArgs[0].value.length, 8);
assert.strictEqual(raw74Parent31.parentVmCallMeta.stackArgs[1].value.length, 74);
assert.strictEqual(raw74Parent31.parentVmCallMeta.stackArgs[2].value.length, 21);
assert.strictEqual(raw74Parent31.parentVmCallMeta.stackArgs[3].value.value, 1);
const raw74ParentLocalWrite = raw74Parent31.parentVmCallMeta.stackArgs[1].stackWrites.find(
  (write) =>
    write.source === "vmLocal" &&
    write.operand === 3 &&
    write.value &&
    write.value.length === 74 &&
    write.value.hexPrefix === thirdSegmentPlainSourceReturn.vmCallMeta.stackArgs[0].value.hexPrefix
);
assert(raw74ParentLocalWrite, "raw74 parent propertyKey31 arg1 lacks vmLocal[3] stack write");
assert(Array.isArray(summary.cryptoProbe.raw74LocalWrites), "missing targeted raw74 local-write events");
const raw74LocalWrite = summary.cryptoProbe.raw74LocalWrites.find(
  (event) =>
    event.type === "vmLocal.raw74.write" &&
    event.target === "vmLocal" &&
    event.slot === 3 &&
    event.value &&
    event.value.length === 74 &&
    event.value.hexPrefix === thirdSegmentPlainSourceReturn.vmCallMeta.stackArgs[0].value.hexPrefix
);
assert(raw74LocalWrite, "missing targeted raw74 vmLocal[3] writer event");
assert(Array.isArray(raw74LocalWrite.valueStackWrites), "raw74 vmLocal[3] writer lacks source stack writes");
const raw74LocalWriteSource = raw74LocalWrite.valueStackWrites.find(
  (write) =>
    write.source === "vmCallReturn" &&
    write.pc === 19 &&
    write.opcode === 57 &&
    write.value &&
    write.value.length === 74 &&
    write.value.hexPrefix === thirdSegmentPlainSourceReturn.vmCallMeta.stackArgs[0].value.hexPrefix
);
assert(raw74LocalWriteSource, "raw74 vmLocal[3] writer lacks pc19/opcode57 source return");
assert(Array.isArray(summary.cryptoProbe.raw74StackReturns), "missing targeted raw74 stack-return events");
const raw74StackReturn = summary.cryptoProbe.raw74StackReturns.find(
  (event) =>
    event.type === "vmStack.raw74.return" &&
    event.source === "vmCallReturn" &&
    event.pc === 19 &&
    event.opcode === 57 &&
    event.value &&
    event.value.length === 74 &&
    event.value.hexPrefix === thirdSegmentPlainSourceReturn.vmCallMeta.stackArgs[0].value.hexPrefix
);
assert(raw74StackReturn, "missing targeted raw74 opcode57 stack return event");
assert(Array.isArray(summary.cryptoProbe.raw74BuilderCalls), "missing targeted raw74 opcode57 builder calls");
const raw74BuilderCall = summary.cryptoProbe.raw74BuilderCalls.find(
  (event) =>
    event.type === "vmCall.raw74.builder" &&
    event.opcode === 57 &&
    event.argc === 1 &&
    event.returnValue &&
    event.returnValue.length === 74 &&
    event.returnValue.hexPrefix === thirdSegmentPlainSourceReturn.vmCallMeta.stackArgs[0].value.hexPrefix
);
assert(raw74BuilderCall, "missing targeted raw74 opcode57 builder call event");
assert.strictEqual(raw74BuilderCall.stackArgs.length, 1);
assert(Array.isArray(summary.cryptoProbe.raw74ChildVmExits), "missing targeted raw74 child VM exits");
const raw74ChildVmExit = summary.cryptoProbe.raw74ChildVmExits.find(
  (event) =>
    event.type === "vmFunction.raw74.exit" &&
    event.returnValue &&
    event.returnValue.length === 74 &&
    event.returnValue.hexPrefix === thirdSegmentPlainSourceReturn.vmCallMeta.stackArgs[0].value.hexPrefix
);
assert(raw74ChildVmExit, "missing targeted raw74 child VM exit event");
assert(raw74ChildVmExit.args.length >= 1);
assert.strictEqual(raw74ChildVmExit.args.values[0].value.value, 1);
assert.strictEqual(raw74ChildVmExit.bytecodeLength, 161);
assert(
  Array.isArray(summary.cryptoProbe.raw74Property8VmOpcodeTrace),
  "missing raw74 propertyKey8 VM opcode trace"
);
const raw74Property8Trace = summary.cryptoProbe.raw74Property8VmOpcodeTrace.find(
  (event) =>
    event.type === "vmFunction.raw74.property8OpcodeTrace" &&
    event.bytecodeLength === 161 &&
    event.returnValue &&
    event.returnValue.length === 74 &&
    event.returnValue.hexPrefix === thirdSegmentPlainSourceReturn.vmCallMeta.stackArgs[0].value.hexPrefix
);
assert(raw74Property8Trace, "missing opcode trace for raw74 propertyKey8 161-byte VM return");
assert.strictEqual(raw74Property8Trace.args.values[0].value.value, 1);
assert(Array.isArray(raw74Property8Trace.bytecode));
assert.strictEqual(raw74Property8Trace.bytecode.length, 161);
assert(Array.isArray(raw74Property8Trace.opcodeSemantics));
assert(raw74Property8Trace.opcodeSemantics.length > 0, "propertyKey8 VM trace lacks opcode semantics");
assert(Array.isArray(raw74Property8Trace.calls), "propertyKey8 VM trace lacks calls array");
const raw74Property8VoidCall = raw74Property8Trace.calls.find(
  (event) =>
    event.type === "vm.void.call" &&
    event.opcode === 33 &&
    event.opcodePc === 86 &&
    event.opcodeByteAtPc === 33 &&
    event.argc === 1 &&
    event.argsBefore &&
    event.argsBefore[0] &&
    event.argsBefore[0].length === 0 &&
    event.argsAfter &&
    event.argsAfter[0] &&
    event.argsAfter[0].length > 0 &&
    event.stackArgs &&
    event.stackArgs[0] &&
    event.stackArgs[0].stackWrites.some(
      (write) => write.source === "vmLocal" && write.operand === 5 && write.pc === 85
    )
);
assert(raw74Property8VoidCall, "propertyKey8 VM trace lacks pc86 opcode33 local5-mutating void call");
assert.strictEqual(raw74Property8VoidCall.pc, 88);
assert.strictEqual(
  raw74Property8VoidCall.bytecodeWindow[
    raw74Property8VoidCall.opcodePc - raw74Property8VoidCall.bytecodeWindowStart
  ],
  33
);
const raw74Property8AppendSegments = findArrayAppendSegments(
  raw74Property8Trace.calls,
  raw74Property8Trace.returnValue.objectId
);
assert(
  raw74Property8AppendSegments.length > 0,
  "propertyKey8 VM trace lacks raw74 append segments"
);
assert.strictEqual(raw74Property8AppendSegments[0].beforeLen, 0);
assert.strictEqual(
  raw74Property8AppendSegments[raw74Property8AppendSegments.length - 1].afterLen,
  74
);
for (const segment of raw74Property8AppendSegments) {
  assert.strictEqual(segment.opcode, 44);
  assert(
    segment.propertyKey === 87 || segment.propertyKey === 127,
    `unexpected raw74 append propertyKey ${segment.propertyKey}`
  );
  assert.strictEqual(segment.afterLen - segment.beforeLen, segment.bytes.length);
  if (segment.propertyKey === 87) {
    assert.strictEqual(segment.bytes.length, 1);
    assert.strictEqual(segment.event.argc, 2);
    assert.strictEqual(segment.event.argsAfter[0].length, segment.afterLen);
  } else {
    const payload = segment.event.argsBefore.find(
      (value) =>
        value &&
        Array.isArray(value.items) &&
        value.objectId !== raw74Property8Trace.returnValue.objectId
    );
    assert(payload, `propertyKey127 append segment ${segment.index} lacks payload arg`);
    assert.strictEqual(segment.bytes[0], payload.length);
    assert.deepStrictEqual(segment.bytes.slice(1), payload.items);
    assert.strictEqual(segment.bytes.length, payload.length + 1);
  }
}
assert.deepStrictEqual(
  raw74Property8AppendSegments.flatMap((segment) => segment.bytes),
  thirdSegmentRaw74
);
const raw74Property8AppendRanges = raw74Property8AppendSegments.map((segment) => [
  segment.beforeLen,
  segment.afterLen,
  segment.propertyKey,
  segment.bytes.length,
]);
assert.deepStrictEqual(raw74Property8AppendRanges, [
  [0, 1, 87, 1],
  [1, 20, 127, 19],
  [20, 21, 87, 1],
  [21, 34, 127, 13],
  [34, 35, 87, 1],
  [35, 37, 127, 2],
  [37, 38, 87, 1],
  [38, 45, 127, 7],
  [45, 46, 87, 1],
  [46, 62, 127, 16],
  [62, 63, 87, 1],
  [63, 68, 127, 5],
  [68, 69, 87, 1],
  [69, 71, 127, 2],
  [71, 72, 87, 1],
  [72, 74, 127, 2],
]);
const raw74Nested38Exit = summary.cryptoProbe.raw74ChildVmExits.find(
  (event) =>
    event.type === "vmFunction.raw74.exit" &&
    event.bytecodeLength === 38 &&
    event.returnValue &&
    event.returnValue.length === 74 &&
    event.returnValue.hexPrefix === thirdSegmentPlainSourceReturn.vmCallMeta.stackArgs[0].value.hexPrefix
);
assert(raw74Nested38Exit, "missing targeted raw74 nested 38-byte VM exit event");
assert(Array.isArray(summary.cryptoProbe.raw74NestedVmOpcodeTrace), "missing raw74 nested VM opcode trace");
const raw74Nested38Trace = summary.cryptoProbe.raw74NestedVmOpcodeTrace.find(
  (event) =>
    event.type === "vmFunction.raw74.nestedOpcodeTrace" &&
    event.bytecodeLength === 38 &&
    event.returnValue &&
    event.returnValue.length === 74 &&
    event.returnValue.hexPrefix === thirdSegmentPlainSourceReturn.vmCallMeta.stackArgs[0].value.hexPrefix
);
assert(raw74Nested38Trace, "missing opcode trace for raw74 nested 38-byte VM return");
assert.strictEqual(raw74Property8Trace.args.values[1].value.objectId, raw74Nested38Trace.localWrites[0].value.objectId);
assert.deepStrictEqual(raw74Nested38Trace.args.values[0].value.value, 1);
assert.deepStrictEqual(raw74Nested38Trace.returnValue.items, thirdSegmentRaw74);
assert.strictEqual(arraySummaryHex(raw74Property8Trace.returnValue), arraySummaryHex(raw74Nested38Trace.returnValue));
assert(Array.isArray(raw74Nested38Trace.bytecode));
assert.strictEqual(raw74Nested38Trace.bytecode.length, 38);
assert(raw74Nested38Trace.localWrites.length > 0, "nested 38-byte VM trace lacks local writes");
assert(raw74Nested38Trace.stackWrites.length > 0, "nested 38-byte VM trace lacks stack writes");
assert(Array.isArray(raw74Nested38Trace.calls), "nested 38-byte VM trace lacks calls array");
for (const write of raw74Nested38Trace.localWrites) {
  assert(
    Number.isInteger(write.pc) && write.pc >= 0 && write.pc < raw74Nested38Trace.bytecode.length,
    `nested 38-byte VM local write pc is out of frame: ${write.pc}`
  );
}
for (const write of raw74Nested38Trace.stackWrites) {
  assert(
    Number.isInteger(write.pc) && write.pc >= 0 && write.pc < raw74Nested38Trace.bytecode.length,
    `nested 38-byte VM stack write pc is out of frame: ${write.pc}`
  );
}
for (const call of raw74Nested38Trace.calls) {
  assert(
    Number.isInteger(call.pc) && call.pc >= 0 && call.pc < raw74Nested38Trace.bytecode.length,
    `nested 38-byte VM call pc is out of frame: ${call.pc}`
  );
}
assert(
  Array.isArray(raw74Nested38Trace.opcodeSemantics),
  "nested 38-byte VM trace lacks opcode semantics"
);
assert.deepStrictEqual(
  raw74Nested38Trace.opcodeSemantics.map((event) => event.pc),
  [0, 2, 23, 26, 29, 31, 33],
  "nested 38-byte VM opcode semantics must cover every executed loop iteration"
);
const raw74ReturnOpcode36 = raw74Nested38Trace.opcodeSemantics.find(
  (event) => event.pc === 33 && event.opcode === 36
);
assert(raw74ReturnOpcode36, "nested 38-byte VM trace lacks opcode semantic event pc33/opcode36");
assert.deepStrictEqual(raw74ReturnOpcode36.nextBytes, [36, 12, 1, 8, 42]);
assert.strictEqual(raw74ReturnOpcode36.stackDepth, 2);
assert.strictEqual(raw74ReturnOpcode36.stackTop[0].value.value, 1);
assert.strictEqual(raw74ReturnOpcode36.stackTop[1].value.objectId, raw74Nested38Trace.localWrites[0].value.objectId);
assert(raw74ReturnOpcode36.after, "nested 38-byte VM opcode36 semantic event lacks after-state");
assert.strictEqual(raw74ReturnOpcode36.after.stackDepth, 1);
assert(
  raw74ReturnOpcode36.after.stackTop.some(
    (item) =>
      item.value &&
      item.value.type === "array" &&
      item.value.length === 74 &&
      item.value.hexPrefix === thirdSegmentPlainSourceReturn.vmCallMeta.stackArgs[0].value.hexPrefix
  ),
  "nested 38-byte VM opcode36 after-state lacks raw74 stack value"
);
assert(Array.isArray(summary.cryptoProbe.raw74Opcode36Calls), "missing raw74 opcode36 call events");
const raw74Opcode36Call = summary.cryptoProbe.raw74Opcode36Calls.find(
  (event) =>
    event.type === "vmCall.raw74.opcode36" &&
    event.opcode === 36 &&
    event.propertyKey === 8 &&
    event.argc === 2 &&
    event.returnValue &&
    event.returnValue.length === 74 &&
    event.returnValue.hexPrefix === thirdSegmentPlainSourceReturn.vmCallMeta.stackArgs[0].value.hexPrefix
);
assert(raw74Opcode36Call, "missing targeted raw74 opcode36 call event");
assert.strictEqual(raw74Opcode36Call.stackArgs[0].value.value, 1);
assert.strictEqual(
  raw74Opcode36Call.stackArgs[1].value.objectId,
  raw74Nested38Trace.localWrites[0].value.objectId
);
assert.deepStrictEqual(
  pure.buildCookieThirdRaw74({
    typeByte: thirdSegmentRaw74[3],
    seedWord0: pure.readU32be(thirdSegmentRaw74, 4),
    dynamicWord: pure.readU32be(thirdSegmentRaw74, 13),
    dynamicByte17: thirdSegmentRaw74[17],
    dynamicByte18: thirdSegmentRaw74[18],
    markerByte: thirdSegmentRaw74[19],
    flagByte: thirdSegmentRaw74[25],
    stateByte29: thirdSegmentRaw74[29],
    sequenceByte: thirdSegmentRaw74[61],
  }),
  thirdSegmentRaw74
);
const thirdSegmentExpectedPlain = thirdSegmentEncodedPlain.slice();
for (let index = 0; index < 16; index++) {
  thirdSegmentExpectedPlain[index] ^= local5SourceReturn.returnValue.items[index];
}
assert.deepStrictEqual(
  thirdSegmentExpectedPlain,
  thirdSegmentReturn.vmCallMeta.stackArgs[0].value.items
);
assert.deepStrictEqual(
  pure.buildCookieThirdSegmentFromRaw74(
    thirdSegmentPlainSourceReturn.vmCallMeta.stackArgs[0].value.items,
    local5SourceReturn.returnValue.items,
    thirdSegmentReturn.vmCallMeta.stackArgs[1].value.items
  ),
  thirdSegmentReturn.returnValue.items
);
const thirdSegmentKeyWrite = thirdSegmentReturn.vmCallMeta.stackArgs[1].stackWrites.find(
  (write) =>
    write.source === "vmCallReturn" &&
    write.operand === 121 &&
    write.value &&
    write.value.type === "array" &&
    write.value.length === 21 &&
    write.value.hexPrefix === thirdSegmentReturn.vmCallMeta.stackArgs[1].value.hexPrefix
);
assert(thirdSegmentKeyWrite, "third segment key lacks propertyKey 121 stack provenance");
const thirdSegmentKeyReturn = summary.cryptoProbe.events.find(
  (event) =>
    event.type === "vm.call.return" &&
    event.vmCallMeta &&
    event.vmCallMeta.propertyKey === 121 &&
    event.returnValue &&
    event.returnValue.objectId === thirdSegmentReturn.vmCallMeta.stackArgs[1].value.objectId
);
assert(thirdSegmentKeyReturn, "third segment key lacks matching propertyKey 121 return event");
assert.strictEqual(thirdSegmentKeyReturn.vmCallMeta.opcode, 27);
assert.strictEqual(thirdSegmentKeyReturn.vmCallMeta.argc, 0);
assert.strictEqual(thirdSegmentKeyReturn.returnValue.length, 21);
assert(summary.bytecodeProbe && summary.bytecodeProbe.enabled, "missing bytecode probe summary");
const i3CallProbeEvents = summary.bytecodeProbe.i3CallProbeEvents;
assert(
  Array.isArray(i3CallProbeEvents) && i3CallProbeEvents.length > 0,
  "missing propertyKey 506 call probe events"
);
const completeI3KeyWrapEvents = i3CallProbeEvents.filter(
  (event) =>
    event.index === 506 &&
    event.input &&
    event.input.complete &&
    event.input.items &&
    [16, 32].includes(event.input.length) &&
    event.returnValue &&
    event.returnValue.complete &&
    event.returnValue.items &&
    event.returnValue.length === event.input.length + 5
);
assert(
  completeI3KeyWrapEvents.length > 0,
  "missing complete propertyKey 506 key-wrap events"
);
for (const event of completeI3KeyWrapEvents) {
  assert.strictEqual(event.index, 506);
  assert(event.input.complete, "propertyKey 506 input was truncated");
  assert(event.returnValue.complete, "propertyKey 506 return was truncated");
  assert.strictEqual(event.returnValue.length, event.input.length + 5);
  assert.deepStrictEqual(pure.normalizeIYKey(event.returnValue.items), event.input.items);
  assert.deepStrictEqual(
    pure.wrapIYKey(event.input.items, {
      xorByte: event.returnValue.items[event.returnValue.items.length - 1],
      tailBytes: event.returnValue.items.slice(
        event.returnValue.items.length - 5,
        event.returnValue.items.length - 1
      ),
    }),
    event.returnValue.items
  );
}
const property121Returns = summary.cryptoProbe.events.filter(
  (event) =>
    event.type === "vm.call.return" &&
    event.vmCallMeta &&
    event.vmCallMeta.propertyKey === 121
);
assert(property121Returns.length > 0, "missing propertyKey 121 return events");
for (const event of property121Returns) {
  const matchedI3Event = completeI3KeyWrapEvents.find(
    (i3Event) =>
      i3Event.returnValue &&
      event.returnValue &&
      i3Event.returnValue.hexPrefix === event.returnValue.hexPrefix
  );
  assert(matchedI3Event, "propertyKey 121 return lacks matching propertyKey 506 output");
  assert.deepStrictEqual(pure.normalizeIYKey(event.returnValue.items), matchedI3Event.input.items);
}
const property30Returns = summary.cryptoProbe.events.filter(
  (event) =>
    event.type === "vm.call.return" &&
    event.vmCallMeta &&
    event.vmCallMeta.propertyKey === 30 &&
    event.vmCallMeta.stackArgs &&
    event.vmCallMeta.stackArgs[0] &&
    event.vmCallMeta.stackArgs[0].value &&
    event.vmCallMeta.stackArgs[0].value.items &&
    event.returnValue &&
    event.returnValue.items
);
assert(property30Returns.length > 0, "missing propertyKey 30 key rewrap events");
for (const event of property30Returns) {
  const input = event.vmCallMeta.stackArgs[0].value.items;
  const output = event.returnValue.items;
  assert.strictEqual(event.vmCallMeta.opcode, 9);
  assert.strictEqual(event.vmCallMeta.argc, 1);
  assert.strictEqual(event.returnValue.length, event.vmCallMeta.stackArgs[0].value.length);
  assert.deepStrictEqual(pure.normalizeIYKey(output), pure.normalizeIYKey(input));
  assert.deepStrictEqual(
    pure.wrapIYKey(pure.normalizeIYKey(input), {
      xorByte: output[output.length - 1],
      tailBytes: output.slice(output.length - 5, output.length - 1),
    }),
    output
  );
}
const thirdSegmentZeroWrite = thirdSegmentReturn.vmCallMeta.stackArgs[2].stackWrites.find(
  (write) =>
    write.source === "inlineLiteral" &&
    write.value &&
    write.value.type === "number" &&
    write.value.value === 0
);
assert(thirdSegmentZeroWrite, "third segment zero argument lacks inline literal stack provenance");
const thirdSegmentParent = thirdSegmentReturn.vmCallMeta.parentVmCallMeta;
assert(thirdSegmentParent, "third segment lacks parent VM metadata");
const thirdSegmentUrlMeta = findVmMetaWithStackArg(thirdSegmentParent, (stackArg) => {
  const value = stackArg.value;
  return (
    value &&
    value.type === "string" &&
    value.prefix.includes("queryWealthlcc")
  );
});
assert(thirdSegmentUrlMeta, "third segment parent chain lacks business URL VM metadata");
const thirdSegmentUrlArg = thirdSegmentUrlMeta.stackArgs.find(
  (stackArg) =>
    stackArg.value &&
    stackArg.value.type === "string" &&
    stackArg.value.prefix.includes("queryWealthlcc")
);
assert(thirdSegmentUrlArg, "third segment URL argument is not present in VM stackArgs");
assert.strictEqual(thirdSegmentUrlArg.value.length, 113);
assert(
  thirdSegmentUrlArg.value.prefix.includes("queryWealthlcc"),
  "third segment parent URL argument does not match the business URL"
);
const thirdSegmentUrlWrite = thirdSegmentUrlArg.stackWrites.find(
  (write) =>
    (write.source === "propertyGet" || write.source === "vmArgument") &&
    write.value &&
    (write.value.type === "string" || write.value.className === "[object Arguments]")
);
assert(thirdSegmentUrlWrite, "third segment parent URL lacks stack provenance");
const businessWriter = summary.finalEvents.find(
  (event) =>
    event.type === "xhr.send" &&
    event.method === "POST" &&
    typeof event.url === "string" &&
    event.url.includes("queryWealthlccpProducts.portlet") &&
    typeof event.body === "string" &&
    event.body.includes("pageKeyStr=jglc39")
);
assert(businessWriter, "missing final business POST writer event");
assert.strictEqual(
  extendMutation.argsBefore[1].length,
  localProducer.value.length - fillMutation.after.length
);
assert.deepStrictEqual(
  extendMutation.after.items.slice(fillMutation.after.length),
  extendMutation.argsBefore[1].items
);
const cookiePlainArg0 = matchedCall.args[0].items;
const cookiePlainPayload = extendMutation.argsBefore[1].items;
assert.deepStrictEqual(cookiePlainArg0.slice(4), cookiePlainPayload);
assert.deepStrictEqual(cookiePlainArg0.slice(0, 4), pure.u32be(pure.crc32(cookiePlainPayload)));
assert.strictEqual(pure.verifyCrcPayload(cookiePlainArg0), true);
assert.deepStrictEqual(cookiePlainPayload.slice(0, 2), [2, 8]);
assert.strictEqual(cookiePlainPayload[10], 48);
assert.strictEqual(cookiePlainPayload[59], 64);
const cookieSeed8 = cookiePlainPayload.slice(2, 10);
const cookieSegment48 = cookiePlainPayload.slice(11, 59);
const cookieSegment64 = cookiePlainPayload.slice(60, 124);
assert.deepStrictEqual(secondLocal4Mutation.argsBefore[1].items, cookieSegment48);
assert.deepStrictEqual(thirdLocal4Mutation.argsBefore[1].items, cookieSegment64);
const firstLocal4Mutation = local4Producer.mutations.find(
  (mutation) =>
    mutation.propertyKey === 127 &&
    mutation.before.objectId === local4Producer.value.objectId &&
    mutation.after.objectId === local4Producer.value.objectId &&
    mutation.before.length === 1 &&
    mutation.after.length === 10
);
assert(firstLocal4Mutation, "vmLocal[4] lacks seed length-prefixed append mutation");
assert.strictEqual(firstLocal4Mutation.argProducers[1].source, "vmArgument");
assert.strictEqual(firstLocal4Mutation.argProducers[1].operand, 0);
assert.deepStrictEqual(firstLocal4Mutation.argsBefore[1].items, cookieSeed8);
const seedWordMutations = firstLocal4Mutation.argProducers[1].mutations.filter(
  (mutation) => mutation.propertyKey === 89
);
assert.strictEqual(seedWordMutations.length, 2);
const cookieSeedWords = seedWordMutations.map(
  (mutation) => mutation.argsBefore[1].value >>> 0
);
assert.deepStrictEqual(
  pure.buildCookieSeed8FromWords(cookieSeedWords[0], cookieSeedWords[1]),
  cookieSeed8
);
assert.deepStrictEqual(
  pure.buildCookiePlainPayload(cookieSeed8, cookieSegment48, cookieSegment64),
  cookiePlainPayload
);
assert.deepStrictEqual(pure.buildCookiePlainArg0(cookiePlainPayload), cookiePlainArg0);
assert.deepStrictEqual(
  pure.buildCookiePlainFromSegments(cookieSeed8, cookieSegment48, cookieSegment64),
  cookiePlainArg0
);

const matchedReturn = summary.cryptoProbe.events.find(
  (event) => event.index === cookieMatch.returnEventIndex
);
assert(matchedReturn && matchedReturn.returnValue.complete, "matched crypto return was truncated");
const rebuiltReturn = pure.encryptIY(
  matchedCall.args[0].items,
  matchedCall.args[1].items,
  1,
  { ivBytes: matchedReturn.returnValue.items.slice(0, 16) }
);
assert.deepStrictEqual(rebuiltReturn, matchedReturn.returnValue.items);
const structuralSolverResult = pure.solveCebwmCookieFromRuntimeProbeSummary(summary);
assert.strictEqual(structuralSolverResult.cookieName, "pXlaX0mT0vLDP");
assert.strictEqual(structuralSolverResult.matchesCookie, true);
assert.strictEqual(structuralSolverResult.arg0MatchesTrace, true);
assert.strictEqual(structuralSolverResult.segment48MatchesTrace, true);
assert.strictEqual(structuralSolverResult.segment64MatchesTrace, true);
assert.strictEqual(structuralSolverResult.raw74RoundTrip, true);
assert.deepStrictEqual(
  pure.parseVersionedEncodedBytes(structuralSolverResult.value).bytes,
  matchedReturn.returnValue.items
);

console.log("cebwm runtime crypto probe verification passed");

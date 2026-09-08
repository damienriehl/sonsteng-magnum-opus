import { createHash } from "node:crypto";

export function sha256HexSync(value) {
  return createHash("sha256").update(value,"utf8").digest("hex");
}

function assertDataProperties(value,names) {
  if (Object.getOwnPropertySymbols(value).length)
    throw new TypeError("symbol digest keys are unsupported");
  for (const name of names) {
    const descriptor = Object.getOwnPropertyDescriptor(value,name);
    if (!descriptor?.enumerable || !("value" in descriptor))
      throw new TypeError("digest values require enumerable data properties");
  }
}

export function serializeDigestValue(value,seen=new Set()) {
  if (value === null) return "n;";
  if (value === undefined) return "u;";
  if (typeof value === "boolean") return value ? "b1;" : "b0;";
  if (typeof value === "string") {
    const encoded = JSON.stringify(value);
    return `s${encoded.length}:${encoded}`;
  }
  if (typeof value === "number") {
    const encoded = Number.isNaN(value) ? "NaN" : value === Infinity ? "+Infinity" :
      value === -Infinity ? "-Infinity" : Object.is(value,-0) ? "-0" : String(value);
    return `d${encoded.length}:${encoded}`;
  }
  if (typeof value === "bigint") {
    const encoded = value.toString();
    return `i${encoded.length}:${encoded}`;
  }
  if (typeof value !== "object")
    throw new TypeError(`unsupported digest value: ${typeof value}`);
  if (seen.has(value)) throw new TypeError("cyclic digest value");
  seen.add(value);
  let encoded;
  if (Array.isArray(value)) {
    if (Object.getPrototypeOf(value) !== Array.prototype)
      throw new TypeError("nonstandard digest arrays are unsupported");
    const names = Object.getOwnPropertyNames(value).filter((name) => name !== "length");
    if (names.length !== value.length || names.some((name,index) => name !== String(index)))
      throw new TypeError("digest arrays must be dense and property-free");
    assertDataProperties(value,names);
    encoded = `a${value.length}:` + value.map((item) =>
      serializeDigestValue(item,seen)).join("");
  } else {
    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null)
      throw new TypeError("unsupported digest object prototype");
    const names = Object.getOwnPropertyNames(value),keys = Object.keys(value).sort();
    if (names.length !== keys.length)
      throw new TypeError("digest records require enumerable properties");
    assertDataProperties(value,names);
    encoded = `o${keys.length}:` + keys.map((key) =>
      serializeDigestValue(key,seen) + serializeDigestValue(value[key],seen)).join("");
  }
  seen.delete(value);
  return encoded;
}

export function integrityDigest(value) {
  return sha256HexSync(serializeDigestValue(["observer-integrity-v2",value]));
}

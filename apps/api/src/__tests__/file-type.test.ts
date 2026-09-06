import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { detectKind, EXTENSION_FOR_KIND } from "../lib/file-type.js";

const FIXTURES = resolve(
  import.meta.dirname,
  "../../../../services/rag/tests/fixtures",
);

const fixture = (name: string): Buffer =>
  readFileSync(resolve(FIXTURES, name));

describe("detectKind", () => {
  it("recognises a real PDF", () => {
    expect(detectKind(fixture("normalization.pdf"))).toBe("pdf");
  });

  it("recognises a real PPTX", () => {
    // Every OOXML file is a ZIP, so this only passes if the presentation part
    // is actually found rather than the ZIP header alone being accepted.
    expect(detectKind(fixture("indexing.pptx"))).toBe("pptx");
  });

  it("recognises legacy binary PowerPoint by its OLE2 header", () => {
    const ole2 = Buffer.from([
      0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1, 0x00, 0x00,
    ]);
    expect(detectKind(ole2)).toBe("ppt");
  });

  it("rejects a ZIP that is not a presentation", () => {
    // A .docx or a plain archive must not be accepted just for being a ZIP.
    const zip = Buffer.concat([
      Buffer.from([0x50, 0x4b, 0x03, 0x04]),
      Buffer.from("word/document.xml and other entries"),
    ]);
    expect(detectKind(zip)).toBeNull();
  });

  it("rejects an executable renamed to look like a document", () => {
    // The filename and declared MIME type are caller controlled; only the
    // bytes decide, and these bytes are a Windows PE header.
    expect(detectKind(Buffer.from("MZ\x90\x00 this is an exe"))).toBeNull();
  });

  it("rejects plain text", () => {
    expect(detectKind(Buffer.from("just some text"))).toBeNull();
  });

  it("rejects an empty buffer without throwing", () => {
    expect(detectKind(Buffer.alloc(0))).toBeNull();
  });

  it("rejects a truncated PDF header", () => {
    expect(detectKind(Buffer.from("%PD"))).toBeNull();
  });

  it("maps every kind to an extension", () => {
    expect(EXTENSION_FOR_KIND.pdf).toBe("pdf");
    expect(EXTENSION_FOR_KIND.pptx).toBe("pptx");
    expect(EXTENSION_FOR_KIND.ppt).toBe("ppt");
  });
});

import type { DocumentKind } from "@examprep/shared";

/**
 * Detects the real format from the file's own bytes.
 *
 * The browser-supplied MIME type and the filename extension are both attacker
 * controlled, and this file is about to be handed to a document parser. What
 * the bytes say is the only trustworthy signal.
 */

const PDF_MAGIC = Buffer.from("%PDF-");
const ZIP_MAGIC = Buffer.from([0x50, 0x4b, 0x03, 0x04]);
// Also valid ZIP starts: an empty archive and a spanned archive.
const ZIP_EMPTY = Buffer.from([0x50, 0x4b, 0x05, 0x06]);
const ZIP_SPANNED = Buffer.from([0x50, 0x4b, 0x07, 0x08]);
// Legacy binary PowerPoint (.ppt) is an OLE2 compound document.
const OLE2_MAGIC = Buffer.from([
  0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1,
]);

function startsWith(data: Buffer, magic: Buffer): boolean {
  return data.length >= magic.length && data.subarray(0, magic.length).equals(magic);
}

export function detectKind(data: Buffer): DocumentKind | null {
  if (startsWith(data, PDF_MAGIC)) {
    return "pdf";
  }

  if (startsWith(data, OLE2_MAGIC)) {
    return "ppt";
  }

  if (
    startsWith(data, ZIP_MAGIC) ||
    startsWith(data, ZIP_EMPTY) ||
    startsWith(data, ZIP_SPANNED)
  ) {
    // Every OOXML file is a ZIP. Distinguishing a .pptx from a .docx means
    // finding the presentation part. Entry names appear as plain bytes in both
    // the local headers and the central directory, and the central directory
    // is at the end of the file — so search the whole buffer rather than a
    // window at either end. Buffer.indexOf is a native scan, so this stays
    // cheap even at the upload size limit.
    if (
      data.indexOf("ppt/presentation.xml") !== -1 ||
      data.indexOf("ppt/slides/") !== -1
    ) {
      return "pptx";
    }
    return null;
  }

  return null;
}

export const EXTENSION_FOR_KIND: Record<DocumentKind, string> = {
  pdf: "pdf",
  pptx: "pptx",
  ppt: "ppt",
};

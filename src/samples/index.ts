import notationExample from "./notation_example.py?raw";
import broken from "./broken.py?raw";
import linkOverlayDemo from "./link_overlay_demo.py?raw";

/**
 * ty computes ranges over the source it was given while Monaco normalises to LF.
 * Feeding both the same LF text keeps token and diagnostic columns aligned.
 */
function toUnixNewlines(source: string): string {
  return source.replace(/\r\n/g, "\n");
}

export const SAMPLES: Record<string, string> = {
  "notation_example.py": toUnixNewlines(notationExample),
  "link_overlay_demo.py": toUnixNewlines(linkOverlayDemo),
  "broken.py": toUnixNewlines(broken),
};

export const DEFAULT_FILE = "notation_example.py";

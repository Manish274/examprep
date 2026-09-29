/**
 * The Examprep design system, ported from the Claude Design project's
 * `_ds_bundle.js` to typed React.
 *
 * The bundle is plain `React.createElement` calls with inline style objects and
 * a `window.lucide` CDN dependency, which is right for a design canvas and
 * wrong for an application: no types, no tree-shaking, and an icon component
 * that writes `innerHTML` on every render. These are the same components with
 * the same token references and the same numbers -- variants, sizes, hover
 * rules and transitions are copied, not reinterpreted.
 *
 * Two deliberate substitutions, both flagged in the system's own readme as
 * stand-ins rather than brand decisions:
 *
 * - **Icons** come from `lucide-react` rather than the CDN UMD build. Same set,
 *   same 1.75px stroke, but typed and bundled.
 * - **Fonts** are loaded by `next/font` in the root layout rather than by an
 *   `@import` from fonts.googleapis.com. Same three families, self-hosted, with
 *   no render-blocking request and no layout shift.
 *
 * Styles stay inline, as in the bundle, so each component remains diffable
 * against the source it came from and a re-import is a readable comparison
 * rather than a translation exercise. The tokens they reference live in
 * `src/styles/ds/tokens`.
 */

export { Badge } from "./badge";
export { Button, IconButton } from "./button";
export { Card } from "./card";
export { AttachmentTile, Composer, SuggestionChip } from "./composer";
export { CitationChip, Message } from "./conversation";
export { Flashcard } from "./flashcard";
export { Icon } from "./icon";
export { usePrefersReducedMotion } from "./motion";
export { ProgressRing, QuizOption } from "./quiz";
export { NavItem, RailSection } from "./rail";
export { Display, Wordmark } from "./type";

## 2024-03-24 - Accessibility upgrades for visual-only labels
**Learning:** React custom UI components (like `Input`) and pseudo-labels (using `div`) can break accessibility without proper associations and semantic HTML.
**Action:** Upgrade visual-only pseudo-labels (`<div className="import-label">`) to semantic `<label htmlFor="...">` elements with corresponding `id` attributes on inputs for improved click-targets and proper screen reader association.

# Anime Studio interaction standards

This is the implementation quick reference. Use the [detailed design guide](DESIGN-GUIDE.md) for tokens, component contracts, accessibility targets and acceptance criteria, and the [product guide](PRODUCT-GUIDE.md) for user workflows.

Keep the existing white paper, blue selection, red destructive actions, condensed headings and quiet grid background. Use the same controls across Script, Cast, Voices, Shots, Production, Queue, Studio, Assemble and Settings.

- Organize navigation by Plan, Produce and Finish, with workspace settings separate. Keep the sidebar fixed and scroll the main content independently. Preserve each page’s scroll position during refreshes and tab changes.
- Give the next task a clear primary action. Keep maintenance and destructive operations in secondary menus. Label destructive actions with their object, preview their scope and retain existing recovery checkpoints.
- Use shared button sizes, visible keyboard focus and readable form spacing. Wrap toolbars instead of squeezing actions into tiny controls. Allow wide settings tables to scroll rather than compressing editable names.
- Keep dialog headings and actions visible while the body scrolls. Name dialogs for assistive technology, contain keyboard focus, support Escape and return focus to the opener without scrolling the page.
- Show connection failures and save outcomes in context. Preserve local dialogue drafts when requests fail; do not silently replace fields with polling results.

## Dialogue

Open the same dialogue editor from Shots or Voices. Voices opens the exact selected line. The line navigator shows speaker, words and readiness; the editor shows one line’s character, reference preview, spoken words, optional delivery and generated takes.

Save dialogue explicitly. Generation saves all entered lines before queuing the chosen line or all lines. Keep takes-per-line selection when navigating. Show save state in the footer, and explain missing words or character references at the field that needs attention. Ask before discarding unsaved work or removing a populated line.

Use compact, labelled preview and selection buttons for takes. Show the selected performance without relying on color alone. Keep historical takes distinct from current dialogue; changing words or character does not imply that an earlier performance matches. Updating/selecting one line’s audio must preserve every other draft line and the typing caret. Results refresh without rebuilding the input controls.

## Verification

Use isolated sample projects for browser checks. Verify populated and empty dialogue, multiple characters, line navigation, saving, failure retention, take selection with another line edited, keyboard focus containment and a narrow window. Do not submit production GPU work as part of UX verification.
